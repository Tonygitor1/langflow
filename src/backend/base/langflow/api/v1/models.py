from __future__ import annotations

import os
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel, field_validator

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.services.auth.utils import get_current_active_user

router = APIRouter(prefix="/models", tags=["Models"], include_in_schema=False)

MAX_STRING_LENGTH = 200

# Constants kept for backward compatibility with variable.py
DISABLED_MODELS_VAR = "__disabled_models__"
ENABLED_MODELS_VAR = "__enabled_models__"


def get_provider_from_variable_name(variable_name: str) -> str | None:  # noqa: ARG001
    """No-op — provider credentials are managed by LiteLLM, not Langflow variables."""
    return None


def get_model_names_for_provider(provider: str) -> set[str]:  # noqa: ARG001
    """No-op — model names come from LiteLLM now."""
    return set()

_OWNED_BY_TO_PROVIDER: dict[str, str] = {
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "vertex_ai": "Google",
    "mistral": "Mistral AI",
    "groq": "Groq",
    "cohere": "Cohere",
    "huggingface": "HuggingFace",
    "together": "Together AI",
    "perplexity": "Perplexity",
    "bedrock": "Bedrock",
    "azure": "Azure OpenAI",
    "ollama": "Ollama",
}

_PROVIDER_ICONS: dict[str, str] = {
    "OpenAI": "OpenAI",
    "Anthropic": "Anthropic",
    "Google": "Google",
    "Mistral AI": "MistralAI",
    "Groq": "Groq",
    "Cohere": "Cohere",
    "HuggingFace": "HuggingFace",
    "Together AI": "TogetherAI",
    "Perplexity": "Perplexity",
    "Bedrock": "AmazonBedrock",
    "Azure OpenAI": "AzureOpenAI",
    "Ollama": "Ollama",
}


def _litellm_url() -> str:
    return os.getenv("LITELLM_URL", "http://localhost:4000").rstrip("/")


def _litellm_key() -> str:
    return os.getenv("LITELLM_MASTER_KEY", "")


def _owned_by_to_provider(owned_by: str) -> str:
    return _OWNED_BY_TO_PROVIDER.get(owned_by.lower(), owned_by.title())


async def _fetch_litellm_models() -> list[dict]:
    """Fetch the model list from the LiteLLM proxy."""
    url = f"{_litellm_url()}/models"
    headers = {"x-litellm-api-key": _litellm_key()}
    params = {"return_wildcard_routes": "false", "include_metadata": "false"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
            return resp.json().get("data", [])
    except Exception:
        logger.warning("Failed to fetch models from LiteLLM at %s", url, exc_info=True)
        return []


_EMBEDDING_PATTERNS = (
    "embed",
    "text-embedding",
    "embedding",
    "ada-002",
    "text-similarity",
    "text-search",
    "code-search",
)


def _infer_model_type(model_id: str) -> str:
    """Return 'embeddings' if the model name looks like an embedding model, else 'llm'."""
    lower = model_id.lower()
    if any(pat in lower for pat in _EMBEDDING_PATTERNS):
        return "embeddings"
    return "llm"


def _build_provider_list(litellm_models: list[dict]) -> list[dict]:
    """Group LiteLLM models into provider dicts matching the frontend Provider type."""
    providers: dict[str, dict] = {}
    for entry in litellm_models:
        model_id: str = entry.get("id", "")
        owned_by: str = entry.get("owned_by", "")
        provider_name = _owned_by_to_provider(owned_by)

        if provider_name not in providers:
            providers[provider_name] = {
                "provider": provider_name,
                "icon": _PROVIDER_ICONS.get(provider_name, "Bot"),
                "is_enabled": True,
                "is_configured": True,
                "model_count": 0,
                "models": [],
                "api_docs_url": None,
            }
        providers[provider_name]["models"].append({
            "model_name": model_id,
            "metadata": {"model_type": _infer_model_type(model_id)},
        })
        providers[provider_name]["model_count"] += 1

    return sorted(providers.values(), key=lambda p: p["provider"])


@router.get("/providers", status_code=200, dependencies=[Depends(get_current_active_user)])
async def list_model_providers() -> list[str]:
    """Return available model providers from LiteLLM."""
    models = await _fetch_litellm_models()
    seen: set[str] = set()
    result = []
    for entry in models:
        provider = _owned_by_to_provider(entry.get("owned_by", ""))
        if provider not in seen:
            seen.add(provider)
            result.append(provider)
    return sorted(result)


@router.get("", status_code=200)
async def list_models(
    *,
    provider: Annotated[list[str] | None, Query(description="Repeat to include multiple providers")] = None,
    model_type: str | None = None,
    current_user: CurrentActiveUser,  # noqa: ARG001
):
    """Return model catalog from LiteLLM, grouped by provider."""
    litellm_models = await _fetch_litellm_models()
    provider_list = _build_provider_list(litellm_models)

    if provider:
        provider_set = set(provider)
        provider_list = [p for p in provider_list if p["provider"] in provider_set]

    if model_type and model_type != "all":
        for p in provider_list:
            p["models"] = [m for m in p["models"] if m.get("metadata", {}).get("model_type") == model_type]
        provider_list = [p for p in provider_list if p["models"]]
        for p in provider_list:
            p["model_count"] = len(p["models"])

    return provider_list


@router.get("/provider-variable-mapping", status_code=200)
async def get_model_provider_mapping() -> dict[str, list[dict]]:
    """Return empty mapping — provider credentials are managed by LiteLLM."""
    return {}


@router.get("/enabled_providers", status_code=200)
async def get_enabled_providers(
    *,
    current_user: CurrentActiveUser,  # noqa: ARG001
):
    """All providers configured in LiteLLM are considered enabled."""
    models = await _fetch_litellm_models()
    seen: set[str] = set()
    provider_status: dict[str, bool] = {}
    for entry in models:
        provider = _owned_by_to_provider(entry.get("owned_by", ""))
        if provider not in seen:
            seen.add(provider)
            provider_status[provider] = True
    return {
        "enabled_providers": list(provider_status.keys()),
        "provider_status": provider_status,
    }


@router.get("/enabled_models", status_code=200)
async def get_enabled_models(
    *,
    current_user: CurrentActiveUser,  # noqa: ARG001
    model_names: Annotated[list[str] | None, Query()] = None,
):
    """Return all LiteLLM models as enabled — no per-user toggles."""
    models = await _fetch_litellm_models()
    enabled_models: dict[str, dict[str, bool]] = {}

    for entry in models:
        model_id: str = entry.get("id", "")
        provider = _owned_by_to_provider(entry.get("owned_by", ""))
        if provider not in enabled_models:
            enabled_models[provider] = {}
        enabled_models[provider][model_id] = True

    if model_names:
        model_set = set(model_names)
        filtered: dict[str, dict[str, bool]] = {}
        for prov, models_dict in enabled_models.items():
            subset = {m: v for m, v in models_dict.items() if m in model_set}
            if subset:
                filtered[prov] = subset
        return {"enabled_models": filtered}

    return {"enabled_models": enabled_models}


class DefaultModelRequest(BaseModel):
    model_name: str
    provider: str
    model_type: str

    @field_validator("model_name", "provider")
    @classmethod
    def validate_non_empty_string(cls, v: str) -> str:
        if not v or not v.strip():
            msg = "Field cannot be empty"
            raise ValueError(msg)
        if len(v) > MAX_STRING_LENGTH:
            msg = f"Field exceeds maximum length of {MAX_STRING_LENGTH} characters"
            raise ValueError(msg)
        return v.strip()

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, v: str) -> str:
        if v not in ("language", "embedding"):
            msg = "model_type must be 'language' or 'embedding'"
            raise ValueError(msg)
        return v


@router.get("/default_model", status_code=200)
async def get_default_model(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    model_type: Annotated[str, Query(description="Type of model: 'language' or 'embedding'")] = "language",
):
    """Get the default model for the current user from variables."""
    import json

    from langflow.services.deps import get_variable_service
    from langflow.services.variable.service import DatabaseVariableService

    var_name = "__default_language_model__" if model_type == "language" else "__default_embedding_model__"
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        return {"default_model": None}

    try:
        var = await variable_service.get_variable_object(user_id=current_user.id, name=var_name, session=session)
        if var.value:
            try:
                parsed = json.loads(var.value)
            except (json.JSONDecodeError, TypeError):
                return {"default_model": None}
            if isinstance(parsed, dict) and all(k in parsed for k in ("model_name", "provider", "model_type")):
                return {"default_model": parsed}
    except ValueError:
        pass
    return {"default_model": None}


@router.post("/default_model", status_code=200)
async def set_default_model(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    request: DefaultModelRequest,
):
    """Set the default model for the current user."""
    import json

    from langflow.services.deps import get_variable_service
    from langflow.services.variable.constants import GENERIC_TYPE
    from langflow.services.variable.service import DatabaseVariableService

    var_name = "__default_language_model__" if request.model_type == "language" else "__default_embedding_model__"
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        raise HTTPException(status_code=500, detail="Variable service unavailable")

    model_data = {"model_name": request.model_name, "provider": request.provider, "model_type": request.model_type}
    model_json = json.dumps(model_data)

    try:
        existing = await variable_service.get_variable_object(user_id=current_user.id, name=var_name, session=session)
        from langflow.services.database.models.variable.model import VariableUpdate

        await variable_service.update_variable_fields(
            user_id=current_user.id,
            variable_id=existing.id,
            variable=VariableUpdate(id=existing.id, name=var_name, value=model_json, type=GENERIC_TYPE),
            session=session,
        )
    except ValueError:
        await variable_service.create_variable(
            user_id=current_user.id, name=var_name, value=model_json, type_=GENERIC_TYPE, session=session
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to set default model for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to set default model.") from e

    return {"default_model": model_data}


@router.delete("/default_model", status_code=200)
async def clear_default_model(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
    model_type: Annotated[str, Query(description="Type of model: 'language' or 'embedding'")] = "language",
):
    """Clear the default model for the current user."""
    from langflow.services.deps import get_variable_service
    from langflow.services.variable.service import DatabaseVariableService

    var_name = "__default_language_model__" if model_type == "language" else "__default_embedding_model__"
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        raise HTTPException(status_code=500, detail="Variable service unavailable")

    try:
        existing = await variable_service.get_variable_object(user_id=current_user.id, name=var_name, session=session)
        await variable_service.delete_variable(user_id=current_user.id, name=existing.name, session=session)
    except ValueError:
        pass
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to clear default model for user %s", current_user.id)
        raise HTTPException(status_code=500, detail="Failed to clear default model.") from e

    return {"default_model": None}
