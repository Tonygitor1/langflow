"""Provider metadata accessors."""

from __future__ import annotations

from functools import lru_cache

from lfx.base.models.model_metadata import MODEL_PROVIDER_METADATA


@lru_cache(maxsize=1)
def get_model_provider_metadata() -> dict:
    """Return the model provider metadata configuration."""
    return MODEL_PROVIDER_METADATA


model_provider_metadata = get_model_provider_metadata()


@lru_cache(maxsize=1)
def get_model_provider_variable_mapping() -> dict[str, str]:
    """Return primary (first required secret) variable for each provider."""
    result = {}
    for provider, meta in model_provider_metadata.items():
        for var in meta.get("variables", []):
            if var.get("required") and var.get("is_secret"):
                result[provider] = var["variable_key"]
                break
        if provider not in result and meta.get("variables"):
            result[provider] = meta["variables"][0]["variable_key"]
    return result


def get_provider_all_variables(provider: str) -> list[dict]:
    """Get all variables for a provider."""
    meta = model_provider_metadata.get(provider, {})
    return meta.get("variables", [])


def get_provider_required_variable_keys(provider: str) -> list[str]:
    """Get all required variable keys for a provider."""
    variables = get_provider_all_variables(provider)
    return [v["variable_key"] for v in variables if v.get("required")]


@lru_cache(maxsize=1)
def _get_all_provider_specific_field_names() -> set[str]:
    """Return set of all field names used as mapping_field by any provider."""
    names: set[str] = set()
    for meta in model_provider_metadata.values():
        for v in meta.get("variables", []):
            mapping = v.get("component_metadata", {}).get("mapping_field")
            if mapping:
                names.add(mapping)
    return names


def get_provider_from_variable_key(variable_key: str) -> str | None:
    """Get provider name from a variable key."""
    for provider, meta in model_provider_metadata.items():
        for var in meta.get("variables", []):
            if var.get("variable_key") == variable_key:
                return provider
    return None
