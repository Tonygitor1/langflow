import json
import uuid
from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from langflow.api.utils.kb_helpers import KBStorageHelper
from langflow.services.database.models.user.crud import get_user_by_id

from lfx.base.knowledge_bases.knowledge_base_utils import get_knowledge_bases
from lfx.base.models.unified_models import get_embeddings
from lfx.custom import Component
from lfx.io import BoolInput, DropdownInput, IntInput, MessageTextInput, Output
from lfx.log.logger import logger
from lfx.schema.data import Data
from lfx.schema.dataframe import DataFrame
from lfx.services.deps import get_settings_service, session_scope
from lfx.utils.validate_cloud import raise_error_if_astra_cloud_disable_component

_KNOWLEDGE_BASES_ROOT_PATH: Path | None = None

# Error message to raise if we're in Astra cloud environment and the component is not supported.
astra_error_msg = "Knowledge retrieval is not supported in Astra cloud environment."


def _get_knowledge_bases_root_path() -> Path:
    """Lazy load the knowledge bases root path from settings."""
    global _KNOWLEDGE_BASES_ROOT_PATH  # noqa: PLW0603
    if _KNOWLEDGE_BASES_ROOT_PATH is None:
        settings = get_settings_service().settings
        knowledge_directory = settings.knowledge_bases_dir
        if not knowledge_directory:
            msg = "Knowledge bases directory is not set in the settings."
            raise ValueError(msg)
        _KNOWLEDGE_BASES_ROOT_PATH = Path(knowledge_directory).expanduser()
    return _KNOWLEDGE_BASES_ROOT_PATH


class KnowledgeBaseComponent(Component):
    display_name = "Knowledge Base"
    description = "Search and retrieve data from knowledge."
    icon = "download"
    name = "KnowledgeBase"

    inputs = [
        DropdownInput(
            name="knowledge_base",
            display_name="Knowledge",
            info="Select the knowledge to load data from.",
            required=True,
            options=[],
            refresh_button=True,
            real_time_refresh=True,
        ),
        MessageTextInput(
            name="search_query",
            display_name="Search Query",
            info="Optional search query to filter knowledge base data.",
            tool_mode=True,
        ),
        IntInput(
            name="top_k",
            display_name="Top K Results",
            info="Number of top results to return from the knowledge base.",
            value=5,
            advanced=True,
            required=False,
        ),
        BoolInput(
            name="include_metadata",
            display_name="Include Metadata",
            info="Whether to include all metadata in the output. If false, only content is returned.",
            value=True,
            advanced=False,
        ),
        BoolInput(
            name="include_embeddings",
            display_name="Include Embeddings",
            info="Whether to include embeddings in the output. Only applicable if 'Include Metadata' is enabled.",
            value=False,
            advanced=True,
        ),
    ]

    outputs = [
        Output(
            name="retrieve_data",
            display_name="Results",
            method="retrieve_data",
            info="Returns the data from the selected knowledge base.",
        ),
    ]

    async def update_build_config(self, build_config, field_value, field_name=None):  # noqa: ARG002
        # Check if we're in Astra cloud environment and raise an error if we are.
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        if field_name == "knowledge_base":
            # Update the knowledge base options dynamically
            build_config["knowledge_base"]["options"] = await get_knowledge_bases(
                _get_knowledge_bases_root_path(),
                user_id=self.user_id,  # Use the user_id from the component context
            )

            # If the selected knowledge base is not available, reset it
            if build_config["knowledge_base"]["value"] not in build_config["knowledge_base"]["options"]:
                build_config["knowledge_base"]["value"] = None

        return build_config

    @property
    def _user_uuid(self) -> uuid.UUID | None:
        """Return self.user_id as a UUID, converting from str if necessary."""
        if not self.user_id:
            return None
        return self.user_id if isinstance(self.user_id, uuid.UUID) else uuid.UUID(self.user_id)

    def _get_kb_metadata(self, kb_path: Path) -> dict:
        """Load and process knowledge base metadata."""
        # Check if we're in Astra cloud environment and raise an error if we are.
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        metadata: dict[str, Any] = {}
        metadata_file = kb_path / "embedding_metadata.json"
        if not metadata_file.exists():
            logger.warning(f"Embedding metadata file not found at {metadata_file}")
            return metadata

        try:
            with metadata_file.open("r", encoding="utf-8") as f:
                metadata = json.load(f)
        except json.JSONDecodeError:
            logger.error(f"Error decoding JSON from {metadata_file}")
            return {}

        return metadata

    def _build_embeddings(self, metadata: dict):
        """Build embedding model from metadata."""
        model_name = metadata.get("embedding_model")
        chunk_size = metadata.get("chunk_size")

        if not model_name or model_name == "Unknown":
            msg = "Embedding model not found in knowledge base metadata. Re-ingest the knowledge base."
            raise ValueError(msg)

        # get_embeddings expects model as a list of dicts: [{"name": "..."}]
        return get_embeddings(
            model=[{"name": model_name}],
            user_id=self.user_id,
            chunk_size=chunk_size,
        )

    async def retrieve_data(self) -> DataFrame:
        """Retrieve data from the selected knowledge base by reading the Chroma collection.

        Returns:
            A DataFrame containing the data rows from the knowledge base.
        """
        # Check if we're in Astra cloud environment and raise an error if we are.
        raise_error_if_astra_cloud_disable_component(astra_error_msg)
        # Get the current user
        async with session_scope() as db:
            if not self.user_id:
                msg = "User ID is required for fetching Knowledge Base data."
                raise ValueError(msg)
            current_user = await get_user_by_id(db, self.user_id)
            if not current_user:
                msg = f"User with ID {self.user_id} not found."
                raise ValueError(msg)
            kb_user = current_user.username
        kb_path = _get_knowledge_bases_root_path() / kb_user / self.knowledge_base

        metadata = self._get_kb_metadata(kb_path)
        if not metadata:
            msg = f"Metadata not found for knowledge base: {self.knowledge_base}. Ensure it has been indexed."
            raise ValueError(msg)

        # Build the embedder for the knowledge base
        embedding_function = self._build_embeddings(metadata)

        # Use the same collection name as ingestion: the KB's UUID (not the
        # human-readable name).  Using the wrong name creates a new empty
        # collection and always returns zero results.
        kb_id = metadata.get("id") or self.knowledge_base

        # Use the same client factory as ingestion so remote Chroma (CHROMA_HOST)
        # is respected.  Passing persist_directory here would always target local
        # disk even when data lives in a remote Chroma service.
        chroma_client = KBStorageHelper.get_chroma_client(kb_path)
        chroma = Chroma(
            client=chroma_client,
            embedding_function=embedding_function,
            collection_name=kb_id,
        )

        # If a search query is provided, perform a similarity search
        if self.search_query:
            # Use the search query to perform a similarity search
            logger.info("Performing similarity search")
            results = chroma.similarity_search_with_score(
                query=self.search_query or "",
                k=self.top_k,
            )
        else:
            results = chroma.similarity_search(
                query=self.search_query or "",
                k=self.top_k,
            )

            # For each result, make it a tuple to match the expected output format
            results = [(doc, 0) for doc in results]  # Assign a dummy score of 0

        # If include_embeddings is enabled, get embeddings for the results
        id_to_embedding = {}
        if self.include_embeddings and results:
            doc_ids = [doc[0].metadata.get("_id") for doc in results if doc[0].metadata.get("_id")]

            # Only proceed if we have valid document IDs
            if doc_ids:
                # Access underlying collection to get embeddings
                collection = chroma._collection  # noqa: SLF001
                embeddings_result = collection.get(where={"_id": {"$in": doc_ids}}, include=["metadatas", "embeddings"])

                # Create a mapping from document ID to embedding
                for i, metadata in enumerate(embeddings_result.get("metadatas", [])):
                    if metadata and "_id" in metadata:
                        id_to_embedding[metadata["_id"]] = embeddings_result["embeddings"][i]

        # Build output data based on include_metadata setting
        data_list = []
        for doc in results:
            kwargs = {
                "content": doc[0].page_content,
            }
            if self.search_query:
                kwargs["_score"] = -1 * doc[1]
            if self.include_metadata:
                # Include all metadata, embeddings, and content
                kwargs.update(doc[0].metadata)
            if self.include_embeddings:
                kwargs["_embeddings"] = id_to_embedding.get(doc[0].metadata.get("_id"))

            data_list.append(Data(**kwargs))

        # Return the DataFrame containing the data
        return DataFrame(data=data_list)
