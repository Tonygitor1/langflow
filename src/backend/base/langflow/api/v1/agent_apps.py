"""Publishing an Agent App from a Langflow canvas.

A flow is an Agent App when it contains an **Agent App Skeleton**. Publish then
sends its three nodes' settings to the marketplace, which compiles, checks and
freezes the app (the same checks the Skeleton node shows) and starts its
runtime pod. No Langflow runs in that pod.
"""

from __future__ import annotations

import json
from typing import Any

SKELETON, QUERY, STORE = "AgentAppSkeleton", "AgentAppQuery", "AgentUserStore"


class AppFlowError(ValueError):
    pass


def _nodes(graph: dict, node_type: str) -> list[dict]:
    return [n for n in (graph or {}).get("nodes", []) if (n.get("data") or {}).get("type") == node_type]


def _value(node: dict, field: str, default: Any = None) -> Any:
    template = ((node.get("data") or {}).get("node") or {}).get("template") or {}
    return (template.get(field) or {}).get("value", default)


def _linked(graph: dict, source: dict, target: dict) -> bool:
    return any(e.get("source") == source.get("id") and e.get("target") == target.get("id") for e in graph.get("edges", []))


def is_app_flow(graph: dict) -> bool:
    return bool(_nodes(graph, SKELETON))


def _store(node: dict | None) -> dict:
    if node is None:
        return {"namespaces": {}}
    namespaces = {}
    for row in _value(node, "namespaces", []) or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        entry: dict = {}
        if row.get("sensitivity") == "sensitive":
            entry["sensitivity"] = "sensitive"
        if row.get("description"):
            entry["description"] = str(row["description"])[:300]
        namespaces[name] = entry
    return {"namespaces": namespaces}


def _caps(node: dict) -> dict[str, int | None]:
    caps: dict[str, int | None] = {}
    for row in _value(node, "prices", []) or []:
        name = str(row.get("intent") or "").strip()
        raw = str(row.get("cap") or "").strip()
        if not name:
            continue
        try:
            caps[name] = int(raw) if raw else None
        except ValueError as exc:
            msg = f"cap for '{name}' must be a whole number of credits"
            raise AppFlowError(msg) from exc
    return caps


def app_publish_body(graph: dict, *, flow_id: str, a2a_config: dict) -> dict:
    """The marketplace's POST /api/apps/publish body for an Agent App flow."""
    from lfx.components.agents_market._marketplace import service_id_of

    skeletons, queries, stores = _nodes(graph, SKELETON), _nodes(graph, QUERY), _nodes(graph, STORE)
    if len(skeletons) != 1 or len(queries) != 1:
        msg = "an Agent App needs exactly one Agent App Skeleton and one Agent App Query"
        raise AppFlowError(msg)
    if len(stores) > 1:
        msg = "an Agent App can have at most one Agent User Store"
        raise AppFlowError(msg)
    skeleton_node, query_node = skeletons[0], queries[0]
    store_node = stores[0] if stores else None
    if not _linked(graph, query_node, skeleton_node):
        msg = "connect Agent App Query to Agent App Skeleton"
        raise AppFlowError(msg)
    if store_node is not None and not _linked(graph, store_node, query_node):
        msg = "connect Agent User Store to Agent App Query"
        raise AppFlowError(msg)

    service_id = service_id_of(_value(query_node, "service"))
    if not service_id:
        msg = "pick an App Service in Agent App Query"
        raise AppFlowError(msg)
    try:
        skeleton = json.loads(_value(skeleton_node, "skeleton") or "")
    except json.JSONDecodeError as exc:
        msg = f"the skeleton is not valid JSON: {exc.msg} (line {exc.lineno})"
        raise AppFlowError(msg) from exc

    return {
        "agent_id": flow_id,
        "flow_id": flow_id,
        "name": a2a_config.get("name") or "Agent App",
        "description": a2a_config.get("description") or "",
        "version": a2a_config.get("version") or "1.0.0",
        "service_id": service_id,
        "skeleton": skeleton,
        "store": _store(store_node),
        "caps": _caps(query_node),
        "default_cap": int(_value(query_node, "default_cap", 0) or 0),
    }


def problems_message(detail: Any) -> str:
    """Turn the marketplace's refusal into one readable message for the Publish dialog."""
    if isinstance(detail, dict) and detail.get("problems"):
        lines = [f"- {p.get('where')}: {p.get('message')}" for p in detail["problems"]]
        return f"{detail.get('message', 'the app cannot be published')}:\n" + "\n".join(lines)
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    return str(detail)
