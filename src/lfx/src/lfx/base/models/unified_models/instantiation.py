"""Model instantiation helpers (LLM + embeddings)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.base.models.unified_models.model_catalog import _get_litellm_credentials

if TYPE_CHECKING:
    from uuid import UUID


def get_llm(
    model,
    user_id: UUID | str | None,
    api_key=None,
    temperature=None,
    *,
    stream=False,
    max_tokens=None,
    # Legacy provider-specific params accepted but unused — LiteLLM handles routing
    watsonx_url=None,  # noqa: ARG001
    watsonx_project_id=None,  # noqa: ARG001
    ollama_base_url=None,  # noqa: ARG001
) -> Any:
    """Instantiate an LLM via the LiteLLM proxy.

    All provider models are routed through the platform's LiteLLM gateway.
    api_key overrides the LITELLM_KEY variable when provided (e.g. user sets
    the field to the LITELLM_KEY Langflow variable).
    """
    from lfx.base.models import unified_models as unified_models_module

    try:
        from langchain_core.language_models import BaseLanguageModel

        if isinstance(model, BaseLanguageModel):
            return model
    except ImportError:
        pass

    if not model or not isinstance(model, list) or len(model) == 0:
        msg = "A model selection is required"
        raise ValueError(msg)

    model_dict = model[0]
    model_name = model_dict.get("name")

    if not model_name:
        msg = "Model name is required"
        raise ValueError(msg)

    model_class = unified_models_module.get_model_class("ChatOpenAI")

    litellm_url, litellm_key = _get_litellm_credentials(user_id)
    # If caller passed an explicit api_key (e.g. from the component's api_key
    # field pointing at the LITELLM_KEY variable), prefer it over the resolved key.
    resolved_key = (str(api_key) if api_key else None) or litellm_key

    kwargs: dict[str, Any] = {
        "model": model_name,
        "streaming": stream,
        "api_key": resolved_key,
        "base_url": f"{litellm_url}/v1",
        "stream_usage": True,
    }

    if temperature is not None:
        kwargs["temperature"] = temperature

    if max_tokens is not None and max_tokens != "":
        try:
            max_tokens_int = int(max_tokens)
            if max_tokens_int >= 1:
                kwargs["max_tokens"] = max_tokens_int
        except (TypeError, ValueError):
            pass

    return model_class(**kwargs)


def get_embeddings(
    model,
    user_id: UUID | str | None = None,
    api_key=None,
    api_base=None,
    *,
    dimensions=None,
    chunk_size=None,
    request_timeout=None,
    max_retries=None,
    show_progress_bar=None,
    model_kwargs=None,
    # Legacy provider-specific params accepted but unused — LiteLLM handles routing
    watsonx_url=None,  # noqa: ARG001
    watsonx_project_id=None,  # noqa: ARG001
    watsonx_truncate_input_tokens=None,  # noqa: ARG001
    watsonx_input_text=None,  # noqa: ARG001
    ollama_base_url=None,  # noqa: ARG001
) -> Any:
    """Instantiate an embeddings model via the LiteLLM proxy.

    All embedding providers are routed through the platform's LiteLLM gateway.
    OpenAIEmbeddings is used as the client class because LiteLLM exposes an
    OpenAI-compatible endpoint for all upstream providers.
    """
    # Resolve helpers via package namespace so tests patching
    # lfx.base.models.unified_models.<name> keep working.
    from lfx.base.models import unified_models as unified_models_module

    # Passthrough: already-instantiated Embeddings object from a connection
    try:
        from langchain_core.embeddings import Embeddings as BaseEmbeddings

        if isinstance(model, BaseEmbeddings):
            return model
    except ImportError:
        pass

    if not model or not isinstance(model, list) or len(model) == 0:
        msg = "An embedding model selection is required"
        raise ValueError(msg)

    model_dict = model[0]
    model_name = model_dict.get("name")

    if not model_name:
        msg = "Embedding model name is required"
        raise ValueError(msg)

    # LiteLLM exposes an OpenAI-compatible API for all upstream providers.
    # Use OpenAIEmbeddings via the class registry so the import path stays consistent.
    embedding_class = unified_models_module.get_embedding_class("OpenAIEmbeddings")

    litellm_url, litellm_key = _get_litellm_credentials(user_id)
    resolved_key = (str(api_key) if api_key else None) or litellm_key
    resolved_base = (str(api_base).rstrip("/") if api_base else None) or f"{litellm_url}/v1"

    kwargs: dict[str, Any] = {
        "model": model_name,
        "openai_api_key": resolved_key,
        "openai_api_base": resolved_base,
    }

    if chunk_size is not None:
        try:
            kwargs["chunk_size"] = int(chunk_size)
        except (TypeError, ValueError):
            pass

    if request_timeout is not None:
        try:
            kwargs["request_timeout"] = float(request_timeout)
        except (TypeError, ValueError):
            pass

    if max_retries is not None:
        try:
            kwargs["max_retries"] = int(max_retries)
        except (TypeError, ValueError):
            pass

    if show_progress_bar is not None:
        kwargs["show_progress_bar"] = show_progress_bar

    if dimensions is not None:
        try:
            dim = int(dimensions)
            if dim > 0:
                kwargs["dimensions"] = dim
        except (TypeError, ValueError):
            pass

    if model_kwargs:
        kwargs.update(model_kwargs)

    return embedding_class(**kwargs)
