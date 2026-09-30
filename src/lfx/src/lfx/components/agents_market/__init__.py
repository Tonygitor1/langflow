from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lfx.components._importing import import_mod

if TYPE_CHECKING:
    from lfx.components.agents_market.agent_app_query import AgentAppQueryComponent
    from lfx.components.agents_market.agent_app_skeleton import AgentAppSkeletonComponent
    from lfx.components.agents_market.agent_user_store import AgentUserStoreComponent

_dynamic_imports = {
    "AgentAppQueryComponent": "agent_app_query",
    "AgentAppSkeletonComponent": "agent_app_skeleton",
    "AgentUserStoreComponent": "agent_user_store",
}

__all__ = [
    "AgentAppQueryComponent",
    "AgentAppSkeletonComponent",
    "AgentUserStoreComponent",
]


def __getattr__(attr_name: str) -> Any:
    """Lazily import Agents Market components on attribute access."""
    if attr_name not in _dynamic_imports:
        msg = f"module '{__name__}' has no attribute '{attr_name}'"
        raise AttributeError(msg)
    try:
        result = import_mod(attr_name, _dynamic_imports[attr_name], __spec__.parent)
    except (ModuleNotFoundError, ImportError, AttributeError) as e:
        msg = f"Could not import '{attr_name}' from '{__name__}': {e}"
        raise AttributeError(msg) from e
    globals()[attr_name] = result
    return result


def __dir__() -> list[str]:
    return list(__all__)
