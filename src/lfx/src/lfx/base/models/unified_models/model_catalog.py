"""Unified model catalog — LiteLLM-backed filtering and UI option construction."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import httpx

from lfx.utils.async_helpers import run_until_complete

if TYPE_CHECKING:
    from uuid import UUID

# ── LiteLLM credentials ───────────────────────────────────────────────────────


def _get_litellm_credentials(user_id=None) -> tuple[str, str]:
    """Return (litellm_url, litellm_key) for a user, with env-var fallback.

    When user_id is provided, LITELLM_URL and LITELLM_KEY are read from the
    variable service (LITELLM_URL is a public platform variable; LITELLM_KEY is
    a private per-user credential provisioned on first SSO login).
    Falls back to LITELLM_URL / LITELLM_MASTER_KEY env vars when no user is
    given or the variable is not found.
    """
    litellm_url: str | None = None
    litellm_key: str | None = None

    if user_id and str(user_id) != "None":
        from uuid import UUID as _UUID

        from lfx.services.deps import get_variable_service, session_scope

        uid = _UUID(user_id) if isinstance(user_id, str) else user_id

        async def _fetch():
            url: str | None = None
            key: str | None = None
            async with session_scope() as session:
                variable_service = get_variable_service()
                if variable_service is None:
                    return url, key
                try:
                    url = await variable_service.get_variable(
                        user_id=uid, name="LITELLM_URL", field="", session=session
                    )
                except ValueError:
                    pass
                try:
                    key = await variable_service.get_variable(
                        user_id=uid, name="LITELLM_KEY", field="", session=session
                    )
                except ValueError:
                    pass
            return url, key

        litellm_url, litellm_key = run_until_complete(_fetch())

    if not litellm_url:
        litellm_url = os.environ.get("LITELLM_URL", "http://localhost:4000")
    if not litellm_key:
        litellm_key = os.environ.get("LITELLM_MASTER_KEY", "dummy")

    return litellm_url.rstrip("/"), litellm_key


# ── LiteLLM provider mapping ──────────────────────────────────────────────────

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

_EMBEDDING_PATTERNS = (
    "embed",
    "text-embedding",
    "embedding",
    "ada-002",
    "text-similarity",
    "text-search",
    "code-search",
)

# ── LiteLLM connection helpers ────────────────────────────────────────────────

def _owned_by_to_provider(owned_by: str) -> str:
    return _OWNED_BY_TO_PROVIDER.get(owned_by.lower(), owned_by.title())


def _infer_model_type(model_id: str) -> str:
    """Return 'embeddings' if the model name looks like an embedding model, else 'llm'."""
    lower = model_id.lower()
    if any(pat in lower for pat in _EMBEDDING_PATTERNS):
        return "embeddings"
    return "llm"


async def _fetch_litellm_models(user_id=None) -> list[dict]:
    """Fetch the model list from the LiteLLM proxy.

    Uses the user's personal LITELLM_KEY when user_id is given, otherwise
    falls back to the LITELLM_MASTER_KEY env var.
    """
    url, key = _get_litellm_credentials(user_id)
    headers = {"x-litellm-api-key": key}
    params = {"return_wildcard_routes": "false", "include_metadata": "false"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{url}/models", headers=headers, params=params)
            resp.raise_for_status()
            return resp.json().get("data", [])
    except Exception:
        from lfx.log.logger import logger
        logger.warning("Failed to fetch models from LiteLLM at %s", url)
        return []


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


# ── Public catalog API ────────────────────────────────────────────────────────


def get_unified_models_detailed(
    providers: list[str] | None = None,
    model_name: str | None = None,
    model_type: str | None = None,
    *,
    user_id: UUID | str | None = None,
    include_unsupported: bool | None = None,  # noqa: ARG001 — kept for API compat
    include_deprecated: bool | None = None,   # noqa: ARG001 — kept for API compat
    only_defaults: bool = False,
    **metadata_filters,  # noqa: ARG001 — kept for API compat
) -> list[dict]:
    """Return providers and their models from LiteLLM, optionally filtered.

    Parameters
    ----------
    providers : list[str] | None
        If given, only models from these providers are returned.
    model_name : str | None
        If given, only the model with this exact name is returned.
    model_type : str | None
        Restrict to 'llm' or 'embeddings'. Omit for all models.
    user_id : UUID | str | None
        When provided, the user's personal LITELLM_KEY is used to fetch models.
        Falls back to LITELLM_MASTER_KEY env var when None.
    only_defaults : bool
        When True, only the first 5 models per provider (by LiteLLM order) are returned.
    """
    litellm_models = run_until_complete(_fetch_litellm_models(user_id))
    provider_list = _build_provider_list(litellm_models)

    if providers:
        provider_set = set(providers)
        provider_list = [p for p in provider_list if p["provider"] in provider_set]

    if model_type and model_type != "all":
        for p in provider_list:
            p["models"] = [m for m in p["models"] if m.get("metadata", {}).get("model_type") == model_type]
        provider_list = [p for p in provider_list if p["models"]]

    if model_name:
        for p in provider_list:
            p["models"] = [m for m in p["models"] if m.get("model_name") == model_name]
        provider_list = [p for p in provider_list if p["models"]]

    # Mark defaults (first 5 per provider by list order)
    default_count = 5
    for p in provider_list:
        for i, m in enumerate(p["models"]):
            m["metadata"]["default"] = i < default_count
        if only_defaults:
            p["models"] = [m for m in p["models"] if m["metadata"].get("default")]
        p["model_count"] = len(p["models"])

    return provider_list


def get_language_model_options(
    user_id: UUID | str | None = None,
    *,
    tool_calling: bool | None = None,   # noqa: ARG001 — kept for API compat
) -> list[dict[str, Any]]:
    """Return available LLM providers and their default models from LiteLLM."""
    provider_list = get_unified_models_detailed(model_type="llm", only_defaults=True, user_id=user_id)

    options = []
    for provider_data in provider_list:
        provider = provider_data["provider"]
        icon = provider_data.get("icon", "Bot")
        for model_data in provider_data["models"]:
            model_name = model_data.get("model_name")
            options.append({
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": {"model_type": "llm"},
            })

    return options


def get_embedding_model_options(
    user_id: UUID | str | None = None,
) -> list[dict[str, Any]]:
    """Return available embedding model providers and their default models from LiteLLM."""
    provider_list = get_unified_models_detailed(model_type="embeddings", only_defaults=True, user_id=user_id)

    options = []
    for provider_data in provider_list:
        provider = provider_data["provider"]
        icon = provider_data.get("icon", "Bot")
        for model_data in provider_data["models"]:
            model_name = model_data.get("model_name")
            options.append({
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": {"model_type": "embeddings"},
            })

    return options


def normalize_model_names_to_dicts(
    model_names: list[str] | str,
) -> list[dict[str, Any]]:
    """Convert simple model name(s) to list of dicts with provider and icon metadata."""
    if isinstance(model_names, str):
        model_names = [model_names]

    try:
        provider_list = get_unified_models_detailed()
    except Exception:  # noqa: BLE001
        return [{"name": name} for name in model_names]

    model_lookup: dict[str, dict] = {}
    for provider_data in provider_list:
        provider = provider_data["provider"]
        icon = provider_data.get("icon", "Bot")
        for model_data in provider_data["models"]:
            model_name = model_data.get("model_name")
            model_lookup[model_name] = {
                "name": model_name,
                "icon": icon,
                "category": provider,
                "provider": provider,
                "metadata": model_data.get("metadata", {}),
            }

    return [
        model_lookup.get(name, {
            "name": name,
            "provider": "Unknown",
            "metadata": {"model_type": _infer_model_type(name)},
        })
        for name in model_names
    ]
