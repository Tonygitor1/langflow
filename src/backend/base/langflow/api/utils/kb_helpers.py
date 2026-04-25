import asyncio
import contextlib
import gc
import json
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import boto3
import chromadb
import chromadb.errors
import pandas as pd
from chromadb.api.shared_system_client import SharedSystemClient
from chromadb.config import Settings
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from lfx.base.data.utils import extract_text_from_bytes
from lfx.base.models.unified_models import get_embedding_model_options
from lfx.components.models_and_agents.embedding_model import EmbeddingModelComponent
from lfx.log import logger

from langflow.api.utils import CurrentActiveUser
from langflow.services.database.models.jobs.model import JobStatus
from langflow.services.deps import get_settings_service
from langflow.services.jobs.service import JobService
from langflow.utils.kb_constants import (
    DELETE_BACKOFF_SECONDS,
    EXPONENTIAL_BACKOFF_MULTIPLIER,
    INGESTION_BATCH_SIZE,
    MAX_DELETE_RETRIES,
    MAX_RETRY_ATTEMPTS,
)


class IngestionCancelledError(Exception):
    """Custom error for when an ingestion job is cancelled."""


class KBStorageHelper:
    """Helper class for Knowledge Base storage and path management."""

    @staticmethod
    @lru_cache
    def get_root_path() -> Path:
        """Lazy load and return the knowledge bases root directory (local metadata storage)."""
        settings = get_settings_service().settings
        knowledge_directory = settings.knowledge_bases_dir
        if not knowledge_directory:
            msg = "Knowledge bases directory is not set in the settings."
            raise ValueError(msg)
        return Path(knowledge_directory).expanduser()

    @staticmethod
    def _is_remote_chroma() -> bool:
        """Return True when an external Chroma HTTP service is configured."""
        return bool(os.environ.get("CHROMA_HOST"))

    @staticmethod
    def get_chroma_client(kb_path: Path) -> chromadb.ClientAPI:
        """Return a Chroma client — remote HttpClient when CHROMA_HOST is set, PersistentClient otherwise.

        Remote path: every request is stateless; no shared-memory registry to manage.
        Local path: clears the SharedSystemClient registry first to avoid 'readonly' errors
        from stale handles left by a previous request in the same process.
        """
        if KBStorageHelper._is_remote_chroma():
            host = os.environ["CHROMA_HOST"]
            port = int(os.environ.get("CHROMA_PORT", "8000"))
            return chromadb.HttpClient(host=host, port=port)

        # Local PersistentClient fallback
        path_key = str(kb_path)
        try:
            if path_key in SharedSystemClient._identifier_to_system:  # noqa: SLF001
                del SharedSystemClient._identifier_to_system[path_key]  # noqa: SLF001
        except KeyError as e:
            logger.debug(f"Failed to clear existing Chroma registry entry for {path_key}: {e}")

        return chromadb.PersistentClient(
            path=path_key,
            settings=Settings(
                is_persistent=True,
                persist_directory=path_key,
                chroma_otel_service_name=str(uuid.uuid4()),
            ),
        )

    # Backward-compatible alias used throughout the codebase.
    get_fresh_chroma_client = get_chroma_client

    @staticmethod
    def release_chroma_resources(kb_path: Path) -> None:
        """Release ChromaDB resources.

        No-op for the remote HttpClient (no shared state). For PersistentClient,
        removes the SharedSystemClient registry entry and forces GC to release
        SQLite file handles.
        """
        if KBStorageHelper._is_remote_chroma():
            return

        path_key = str(kb_path)
        try:
            if path_key in SharedSystemClient._identifier_to_system:  # noqa: SLF001
                del SharedSystemClient._identifier_to_system[path_key]  # noqa: SLF001
        except KeyError:
            pass
        gc.collect()

    @staticmethod
    def get_directory_size(path: Path) -> int:
        """Calculate the total size of all files in a directory."""
        total_size = 0
        try:
            for file_path in path.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
        except (OSError, PermissionError):
            pass
        return total_size

    @staticmethod
    def delete_storage(kb_path: Path, kb_name: str, kb_id: str | None = None) -> bool:
        """Delete a KB: remote Chroma collection, S3 documents, and local metadata directory.

        Args:
            kb_path:  Local filesystem path to the KB metadata directory.
            kb_name:  Human-readable KB name (used for local PersistentClient teardown
                      and fallback collection name).
            kb_id:    UUID string of the KB (used as the Chroma collection name in
                      remote mode and as the S3 key prefix).
        """
        collection_name = kb_id or kb_name

        if KBStorageHelper._is_remote_chroma():
            # Remote Chroma: delete the collection by its UUID name.
            try:
                client = KBStorageHelper.get_chroma_client(kb_path)
                client.delete_collection(name=collection_name)
                logger.info("Deleted remote Chroma collection %s", collection_name)
            except chromadb.errors.ChromaError as e:
                logger.debug("Remote Chroma collection deletion failed for %s: %s", collection_name, e)
            except Exception as e:  # noqa: BLE001
                logger.debug("Unexpected error deleting remote Chroma collection %s: %s", collection_name, e)
        else:
            # Local PersistentClient: graceful teardown before rmtree.
            try:
                has_data = any((kb_path / m).exists() for m in ["chroma", "chroma.sqlite3", "index"])
                if has_data:
                    client = KBStorageHelper.get_chroma_client(kb_path)
                    chroma = Chroma(client=client, collection_name=kb_name)
                    with contextlib.suppress(Exception):
                        chroma.delete_collection()
                    chroma = None
                    client = None
            except (OSError, ValueError, TypeError, chromadb.errors.ChromaError) as e:
                logger.debug("Collection teardown failed for %s: %s", kb_path.name, e)

        # Delete raw documents from S3 (best-effort).
        if kb_id:
            KBStorageHelper._delete_kb_documents_from_s3(kb_id)

        # Delete the local metadata directory.
        if not kb_path.exists():
            return True

        if KBStorageHelper._is_remote_chroma():
            # No SQLite lock issues; delete directly.
            try:
                shutil.rmtree(kb_path, ignore_errors=False)
                logger.info("Deleted KB metadata directory %s", kb_name)
                return not kb_path.exists()
            except OSError as e:
                logger.warning("KB metadata deletion failed for %s: %s", kb_name, e)
                return False

        # Local client: retry with backoff to handle SQLite file locks (mainly Windows).
        gc.collect()

        for attempt in range(MAX_DELETE_RETRIES):
            try:
                if attempt > 0:
                    time.sleep(DELETE_BACKOFF_SECONDS * (2**attempt))

                _remove_sqlite_lock_files(kb_path)
                _truncate_sqlite_files(kb_path)
                gc.collect()

                shutil.rmtree(kb_path, ignore_errors=False)

                if not kb_path.exists():
                    logger.info("Deleted knowledge base %s on attempt %d", kb_name, attempt + 1)
                    return True

            except OSError as e:
                if attempt < MAX_DELETE_RETRIES - 1:
                    logger.debug("KB deletion attempt %d failed for %s: %s", attempt + 1, kb_name, e)
                else:
                    logger.warning(
                        "KB deletion failed for %s after %d attempts: %s",
                        kb_name,
                        MAX_DELETE_RETRIES,
                        e,
                    )

        # Last resort: rename for deferred cleanup.
        if kb_path.exists():
            try:
                deferred = kb_path.with_name(f".deleted_{kb_name}_{int(time.time())}")
                kb_path.rename(deferred)
            except OSError as e:
                logger.warning("Deferred rename failed for %s: %s", kb_name, e)
            else:
                logger.info("Renamed %s for deferred cleanup", kb_name)
                return True

        return False

    # ── S3 helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _s3_key_prefix(user_id: str, kb_id: str) -> str:
        """Return the S3 key prefix for a KB's documents.

        Layout: <object_storage_prefix>/<user_id>/knowledge_base/<kb_id>/documents
        Example: data/abc-123/knowledge_base/550e8400-.../documents
        """
        prefix = os.environ.get("LANGFLOW_OBJECT_STORAGE_PREFIX", "").rstrip("/")
        if prefix:
            return f"{prefix}/{user_id}/knowledge_base/{kb_id}/documents"
        return f"{user_id}/knowledge_base/{kb_id}/documents"

    @staticmethod
    def upload_document_to_s3(
        file_name: str,
        file_content: bytes,
        user_id: str,
        kb_id: str,
    ) -> str | None:
        """Upload a raw document file to S3. Returns the S3 key on success, None on failure.

        Files are stored at:
          s3://<bucket>/<prefix>/<user_id>/knowledge_base/<kb_id>/documents/<file_name>
        """
        bucket = os.environ.get("LANGFLOW_OBJECT_STORAGE_BUCKET_NAME")
        if not bucket:
            logger.debug("S3 bucket not configured — skipping document upload for %s", file_name)
            return None

        try:
            s3_key = f"{KBStorageHelper._s3_key_prefix(user_id, kb_id)}/{file_name}"
            s3 = boto3.client("s3")
            s3.put_object(Bucket=bucket, Key=s3_key, Body=file_content)
            logger.info("Uploaded %s to s3://%s/%s", file_name, bucket, s3_key)
            return s3_key
        except Exception as e:  # noqa: BLE001
            logger.warning("S3 upload failed for %s: %s", file_name, e)
            return None

    @staticmethod
    def _delete_kb_documents_from_s3(kb_id: str) -> None:
        """Delete all S3 objects under the KB documents prefix (best-effort, all users)."""
        bucket = os.environ.get("LANGFLOW_OBJECT_STORAGE_BUCKET_NAME")
        if not bucket:
            return

        try:
            s3 = boto3.client("s3")
            # We don't know the user_id at deletion time, so search broadly.
            # The kb_id is unique so there will be at most one matching prefix per user.
            search_prefix = os.environ.get("LANGFLOW_OBJECT_STORAGE_PREFIX", "").rstrip("/")
            # List with a broad prefix — actual user-scoped key will match via /knowledge_base/<kb_id>/
            broader_prefix = f"{search_prefix}/" if search_prefix else ""
            # Filter after listing is impractical for large buckets; instead rely on
            # the fact that kb_id (UUID) is globally unique.
            kb_prefix = f"{broader_prefix}knowledge_base/{kb_id}/" if not search_prefix else None

            # Build the actual prefix pattern: <prefix>/<any_user>/knowledge_base/<kb_id>/documents/
            # Since we can't wildcard mid-key, list under the object prefix and filter client-side.
            paginator = s3.get_paginator("list_objects_v2")
            # Use the base prefix and filter keys containing the kb_id segment.
            base_prefix = f"{search_prefix}/" if search_prefix else ""
            objects_to_delete = []
            for page in paginator.paginate(Bucket=bucket, Prefix=base_prefix):
                for obj in page.get("Contents", []):
                    if f"/knowledge_base/{kb_id}/" in obj["Key"]:
                        objects_to_delete.append({"Key": obj["Key"]})

            if objects_to_delete:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objects_to_delete})
                logger.info("Deleted %d S3 objects for KB %s", len(objects_to_delete), kb_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("S3 cleanup failed for KB %s: %s", kb_id, e)


def _remove_sqlite_lock_files(kb_path: Path) -> None:
    """Remove SQLite auxiliary files (WAL, SHM, journal) that hold locks."""
    for pattern in ["*.sqlite3-wal", "*.sqlite3-shm", "*.sqlite3-journal"]:
        for lock_file in kb_path.glob(pattern):
            try:
                lock_file.unlink()
            except OSError as e:
                logger.debug("Could not remove lock file %s: %s", lock_file.name, e)


def _truncate_sqlite_files(kb_path: Path) -> None:
    """Truncate SQLite database files to release locks."""
    for sqlite_file in kb_path.glob("*.sqlite3"):
        try:
            with sqlite_file.open("r+b") as f:
                f.truncate(0)
        except OSError as e:
            logger.debug("Could not truncate %s: %s", sqlite_file.name, e)


class KBAnalysisHelper:
    """Helper class for Knowledge Base metadata, metrics, and configuration detection."""

    @staticmethod
    def get_metadata(kb_path: Path, *, fast: bool = False) -> dict:
        """Extract metadata from a knowledge base directory."""
        metadata_file = kb_path / "embedding_metadata.json"
        defaults = {
            "chunks": 0,
            "words": 0,
            "characters": 0,
            "avg_chunk_size": 0.0,
            "embedding_provider": "Unknown",
            "embedding_model": "Unknown",
            "id": str(uuid.uuid4()),
            "size": 0,
            "source_types": [],
            "chunk_size": None,
            "chunk_overlap": None,
            "separator": None,
        }

        metadata = {}
        if metadata_file.exists():
            try:
                metadata = json.loads(metadata_file.read_text())
            except (OSError, json.JSONDecodeError):
                logger.warning(f"Failed to parse metadata file for {kb_path.name}, resetting to defaults.")

        missing_keys = not all(k in metadata for k in defaults)
        has_unknowns = metadata.get("embedding_provider") == "Unknown" or metadata.get("embedding_model") == "Unknown"
        # Detect stale zero-chunk metadata: the file claims 0 chunks but
        # Chroma data exists on disk (only relevant for local PersistentClient).
        has_chroma_data = any((kb_path / m).exists() for m in ["chroma", "chroma.sqlite3", "index"])
        stale_chunks = metadata.get("chunks", 0) == 0 and has_chroma_data

        if fast and not missing_keys and not stale_chunks:
            return metadata

        backfill_needed = not metadata_file.exists() or missing_keys or (not fast and has_unknowns)

        if backfill_needed:
            for key, default_val in defaults.items():
                if key not in metadata or (key == "id" and not metadata[key]):
                    metadata[key] = default_val

            try:
                metadata["size"] = KBStorageHelper.get_directory_size(kb_path)
                if metadata.get("embedding_provider") == "Unknown":
                    metadata["embedding_provider"] = KBAnalysisHelper._detect_embedding_provider(kb_path)
                if metadata.get("embedding_model") == "Unknown":
                    metadata["embedding_model"] = KBAnalysisHelper._detect_embedding_model(kb_path)

                metadata_file.write_text(json.dumps(metadata, indent=2))
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
                logger.debug(f"Metadata backfill failed for {kb_path}: {e}")

        # Recount metrics from Chroma if metadata claims 0 chunks but local data exists.
        # (Only triggered for PersistentClient — remote Chroma won't produce local files.)
        if stale_chunks:
            try:
                KBAnalysisHelper.update_text_metrics(kb_path, metadata)
                metadata["size"] = KBStorageHelper.get_directory_size(kb_path)
                metadata_file.write_text(json.dumps(metadata, indent=2))
            except (OSError, ValueError, TypeError, json.JSONDecodeError, chromadb.errors.ChromaError) as e:
                logger.debug(f"Stale metrics recount failed for {kb_path}: {e}")

        return metadata

    @staticmethod
    def update_text_metrics(kb_path: Path, metadata: dict, chroma: Chroma | None = None) -> None:
        """Update text metrics (chunks, words, characters) for a knowledge base."""
        # Use the KB UUID as the collection name (consistent with ingestion path).
        kb_id = metadata.get("id") or kb_path.name
        created_locally = chroma is None
        client = None
        try:
            if created_locally:
                client = KBStorageHelper.get_chroma_client(kb_path)
                chroma = Chroma(client=client, collection_name=kb_id)

            if chroma is None:
                return
            collection = chroma._collection  # noqa: SLF001
            metadata["chunks"] = collection.count()

            if metadata["chunks"] > 0:
                total_words = 0
                total_characters = 0
                # Use a robust batch size to avoid SQLite limits and memory pressure.
                batch_size = 5000

                for offset in range(0, metadata["chunks"], batch_size):
                    results = collection.get(
                        include=["documents"],
                        limit=batch_size,
                        offset=offset,
                    )
                    if not results["documents"]:
                        break

                    source_chunks = pd.DataFrame({"document": results["documents"]})
                    words, characters = KBAnalysisHelper._calculate_text_metrics(source_chunks, ["document"])
                    total_words += words
                    total_characters += characters

                metadata["words"] = total_words
                metadata["characters"] = total_characters
                metadata["avg_chunk_size"] = (
                    round(total_characters / metadata["chunks"], 1) if metadata["chunks"] > 0 else 0.0
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError, chromadb.errors.ChromaError) as e:
            logger.debug(f"Metrics update failed for {kb_path.name}: {e}")
        finally:
            if created_locally:
                client = None
                chroma = None
                KBStorageHelper.release_chroma_resources(kb_path)

    @staticmethod
    def _detect_embedding_provider(kb_path: Path) -> str:
        """Internal helper to detect the embedding provider."""
        provider_patterns = {
            "OpenAI": ["openai", "text-embedding-ada", "text-embedding-3"],
            "Azure OpenAI": ["azure"],
            "HuggingFace": ["sentence-transformers", "huggingface", "bert-"],
            "Cohere": ["cohere", "embed-english", "embed-multilingual"],
            "Google": ["palm", "gecko", "google"],
            "Ollama": ["ollama"],
            "Chroma": ["chroma"],
        }

        for config_file in kb_path.glob("*.json"):
            try:
                with config_file.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    if not isinstance(config_data, dict):
                        continue

                    config_str = json.dumps(config_data).lower()
                    provider_fields = ["embedding_provider", "provider", "embedding_model_provider"]
                    for field in provider_fields:
                        if field in config_data:
                            provider_value = str(config_data[field]).lower()
                            for provider, patterns in provider_patterns.items():
                                if any(pattern in provider_value for pattern in patterns):
                                    return provider
                            if provider_value and provider_value != "unknown":
                                return provider_value.title()

                    for provider, patterns in provider_patterns.items():
                        if any(pattern in config_str for pattern in patterns):
                            return provider

            except (OSError, json.JSONDecodeError):
                logger.exception("Error reading config file '%s'", config_file)
                continue

        if (kb_path / "chroma").exists():
            return "Chroma"
        if (kb_path / "vectors.npy").exists():
            return "Local"

        return "Unknown"

    @staticmethod
    def _detect_embedding_model(kb_path: Path) -> str:
        """Internal helper to detect the embedding model."""
        metadata_file = kb_path / "embedding_metadata.json"
        if metadata_file.exists():
            try:
                with metadata_file.open("r", encoding="utf-8") as f:
                    metadata = json.load(f)
                    if isinstance(metadata, dict) and "embedding_model" in metadata:
                        model_value = str(metadata.get("embedding_model", "unknown"))
                        if model_value and model_value.lower() != "unknown":
                            return model_value
            except (OSError, json.JSONDecodeError):
                logger.exception("Error reading embedding metadata file '%s'", metadata_file)

        for config_file in kb_path.glob("*.json"):
            if config_file.name == "embedding_metadata.json":
                continue

            try:
                with config_file.open("r", encoding="utf-8") as f:
                    config_data = json.load(f)
                    if not isinstance(config_data, dict):
                        continue

                    model_fields = ["embedding_model", "model", "embedding_model_name", "model_name"]
                    for field in model_fields:
                        if field in config_data:
                            model_value = str(config_data[field])
                            if model_value and model_value.lower() != "unknown":
                                return model_value

                    if "openai" in json.dumps(config_data).lower():
                        openai_models = ["text-embedding-ada-002", "text-embedding-3-small", "text-embedding-3-large"]
                        config_str = json.dumps(config_data).lower()
                        for model in openai_models:
                            if model in config_str:
                                return model

                    if "model" in config_data:
                        model_name = str(config_data["model"])
                        hf_patterns = ["sentence-transformers", "all-MiniLM", "all-mpnet", "multi-qa"]
                        if any(pattern in model_name for pattern in hf_patterns):
                            return model_name

            except (OSError, json.JSONDecodeError):
                logger.exception("Error reading config file '%s'", config_file)
                continue

        return "Unknown"

    @staticmethod
    def _calculate_text_metrics(df: pd.DataFrame, text_columns: list[str]) -> tuple[int, int]:
        """Internal helper to calculate total words and characters."""
        total_words = 0
        total_characters = 0

        for col in text_columns:
            if col not in df.columns:
                continue

            text_series = df[col].astype(str).fillna("")
            total_characters += int(text_series.str.len().sum())
            total_words += int(text_series.str.split().str.len().sum())

        return total_words, total_characters


class KBIngestionHelper:
    """Helper class for Knowledge Base ingestion processes."""

    @staticmethod
    async def perform_ingestion(
        kb_name: str,
        kb_path: Path,
        files_data: list[tuple[str, bytes]],
        chunk_size: int,
        chunk_overlap: int,
        separator: str,
        source_name: str,
        current_user: CurrentActiveUser,
        embedding_provider: str,
        embedding_model: str,
        task_job_id: uuid.UUID,
        job_service: JobService,
    ) -> dict[str, object]:
        """Orchestrate the ingestion of files into a knowledge base.

        Raw files are uploaded to S3 for durable storage before ingestion.
        Vector embeddings are stored in the configured Chroma service (remote HTTP
        or local Persistent depending on CHROMA_HOST env var).
        The Chroma collection is keyed by the KB's UUID (not the human-readable
        name) so that collections are globally unique in a shared Chroma service.
        """
        try:
            metadata = KBAnalysisHelper.get_metadata(kb_path, fast=True)
            kb_id = metadata.get("id") or kb_name
            user_id = str(current_user.id)

            processed_files = []
            total_chunks_created = 0

            splitter_kwargs: dict = {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap}
            if separator:
                resolved_separator = separator.replace("\\n", "\n")
                splitter_kwargs["separators"] = [resolved_separator]
            text_splitter = RecursiveCharacterTextSplitter(**splitter_kwargs)

            embeddings = await KBIngestionHelper._build_embeddings(embedding_provider, embedding_model, current_user)

            client = KBStorageHelper.get_chroma_client(kb_path)
            chroma = Chroma(
                client=client,
                embedding_function=embeddings,
                collection_name=kb_id,
            )

            job_id_str = str(task_job_id)
            for file_name, file_content in files_data:
                # Upload raw file to S3 for durable storage (best-effort).
                KBStorageHelper.upload_document_to_s3(file_name, file_content, user_id, kb_id)

                await logger.ainfo("Starting ingestion of %s for %s", file_name, kb_name)
                content = extract_text_from_bytes(file_name, file_content)
                if not content.strip():
                    continue

                chunks = text_splitter.split_text(content)
                for i in range(0, len(chunks), INGESTION_BATCH_SIZE):
                    if await KBIngestionHelper._is_job_cancelled(job_service, task_job_id):
                        raise IngestionCancelledError

                    batch = chunks[i : i + INGESTION_BATCH_SIZE]
                    docs = [
                        Document(
                            page_content=c,
                            metadata={
                                "source": source_name or file_name,
                                "file_name": file_name,
                                "chunk_index": i + j,
                                "total_chunks": len(chunks),
                                "ingested_at": datetime.now(timezone.utc).isoformat(),
                                "job_id": job_id_str,
                            },
                        )
                        for j, c in enumerate(batch)
                    ]

                    for attempt in range(MAX_RETRY_ATTEMPTS):
                        if await KBIngestionHelper._is_job_cancelled(job_service, task_job_id):
                            raise IngestionCancelledError
                        try:
                            await chroma.aadd_documents(docs)
                            break
                        except Exception as e:
                            if attempt == MAX_RETRY_ATTEMPTS - 1:
                                raise
                            wait = (attempt + 1) * EXPONENTIAL_BACKOFF_MULTIPLIER
                            await logger.awarning("Write failed, retrying in %ds: %s", wait, e)
                            await asyncio.sleep(wait)

                    await asyncio.sleep(0.01)

                total_chunks_created += len(chunks)
                processed_files.append(file_name)

            metadata = KBAnalysisHelper.get_metadata(kb_path, fast=True)
            KBAnalysisHelper.update_text_metrics(kb_path, metadata, chroma=chroma)
            metadata["size"] = KBStorageHelper.get_directory_size(kb_path)
            metadata["chunk_size"] = chunk_size
            metadata["chunk_overlap"] = chunk_overlap
            metadata["separator"] = separator or None
            metadata_path = kb_path / "embedding_metadata.json"
            new_source_types = list({f.rsplit(".", 1)[-1].lower() for f in processed_files if "." in f})
            existing_source_types = metadata.get("source_types", [])
            metadata["source_types"] = list(set(existing_source_types + new_source_types))
            metadata_path.write_text(json.dumps(metadata, indent=2))
            await logger.ainfo(f"Completed ingestion for {kb_name}")

            return {
                "message": f"Successfully ingested {len(processed_files)} file(s)",
                "files_processed": len(processed_files),
                "chunks_created": total_chunks_created,
            }

        except IngestionCancelledError:
            await logger.awarning(f"Ingestion job {task_job_id} was cancelled. Cleaning up partial data...")
            metadata = KBAnalysisHelper.get_metadata(kb_path, fast=True)
            kb_id = metadata.get("id") or kb_name
            await KBIngestionHelper.cleanup_chroma_chunks_by_job(task_job_id, kb_path, kb_name, kb_id=kb_id)
            return {"message": "Job cancelled"}
        except Exception as e:
            await logger.aerror(f"Error in background ingestion: {e!s}. Initiating rollback...")
            metadata = KBAnalysisHelper.get_metadata(kb_path, fast=True)
            kb_id = metadata.get("id") or kb_name
            await KBIngestionHelper.cleanup_chroma_chunks_by_job(task_job_id, kb_path, kb_name, kb_id=kb_id)
            raise
        finally:
            client = None
            chroma = None
            KBStorageHelper.release_chroma_resources(kb_path)

    @staticmethod
    async def cleanup_chroma_chunks_by_job(
        job_id: uuid.UUID,
        kb_path: Path,
        kb_name: str,
        kb_id: str | None = None,
    ) -> None:
        """Clean up ChromaDB chunks associated with a specific job ID.

        Args:
            kb_id: KB UUID string used as the collection name. Falls back to reading
                   metadata from disk, then to kb_name, if not provided.
        """
        if kb_id is None:
            try:
                meta = KBAnalysisHelper.get_metadata(kb_path, fast=True)
                kb_id = meta.get("id") or kb_name
            except Exception:  # noqa: BLE001
                kb_id = kb_name

        try:
            client = KBStorageHelper.get_chroma_client(kb_path)
            chroma = Chroma(
                client=client,
                collection_name=kb_id,
            )
            await chroma.adelete(where={"job_id": str(job_id)})
            await logger.ainfo(f"Cleaned up chunks for job {job_id} in knowledge base '{kb_name}'")
        except (OSError, ValueError, TypeError, chromadb.errors.ChromaError) as cleanup_error:
            await logger.aerror(f"Failed to clean up chunks for job {job_id}: {cleanup_error}")
        finally:
            client = None
            chroma = None
            KBStorageHelper.release_chroma_resources(kb_path)

    @staticmethod
    async def _is_job_cancelled(job_service: JobService, job_id: uuid.UUID) -> bool:
        """Internal helper to check if a job has been cancelled."""
        job = await job_service.get_job_by_job_id(job_id)
        return job is not None and job.status == JobStatus.CANCELLED

    @staticmethod
    async def _build_embeddings(provider: str, model: str, current_user):
        """Internal helper to build embeddings object."""
        options = get_embedding_model_options(user_id=current_user.id)
        selected_option = next((o for o in options if o["provider"] == provider and o["name"] == model), None)

        if not selected_option:
            all_options = get_embedding_model_options()
            selected_option = next((o for o in all_options if o["provider"] == provider and o["name"] == model), None)

            if not selected_option:
                msg = f"Embedding model '{model}' for provider '{provider}' not found."
                raise ValueError(msg)

        embedding_model = EmbeddingModelComponent(model=[selected_option], _user_id=current_user.id)
        return embedding_model.build_embeddings()
