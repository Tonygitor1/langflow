"""Agent App flows -> the marketplace publish body (B08 refusals come from the marketplace)."""

import json

import pytest
from langflow.api.v1.agent_apps import AppFlowError, app_publish_body, is_app_flow, problems_message

SERVICE_ID = "11111111-2222-3333-4444-555555555555"


def _node(node_id, node_type, **template):
    return {"id": node_id, "data": {"type": node_type, "node": {"template": {k: {"value": v} for k, v in template.items()}}}}


def _graph(*, store=True, linked=True, skeleton='{"title": "x"}'):
    nodes = [
        _node("s", "AgentAppSkeleton", skeleton=skeleton),
        _node(
            "q",
            "AgentAppQuery",
            service=f"notes-service 1.0.0 (image) [{SERVICE_ID}]",
            default_cap=2,
            prices=[{"intent": "ask", "cap": "3"}, {"intent": "other", "cap": ""}],
        ),
    ]
    edges = [{"source": "q", "target": "s"}] if linked else []
    if store:
        nodes.append(
            _node("u", "AgentUserStore", namespaces=[
                {"name": "notes", "sensitivity": "normal", "description": ""},
                {"name": "holdings", "sensitivity": "sensitive", "description": "your holdings"},
            ])
        )
        edges.append({"source": "u", "target": "q"})
    return {"nodes": nodes, "edges": edges}


def test_detects_app_flows():
    assert is_app_flow(_graph())
    assert not is_app_flow({"nodes": [_node("a", "ChatInput")]})


def test_builds_the_publish_body_from_the_three_nodes():
    body = app_publish_body(_graph(), flow_id="f1", a2a_config={"name": "Notes", "version": "1.2.0"})
    assert body["service_id"] == SERVICE_ID and body["agent_id"] == "f1"
    assert body["skeleton"] == {"title": "x"}
    assert body["store"] == {"namespaces": {"notes": {}, "holdings": {"sensitivity": "sensitive", "description": "your holdings"}}}
    assert body["caps"] == {"ask": 3, "other": None} and body["default_cap"] == 2
    assert (body["name"], body["version"]) == ("Notes", "1.2.0")


@pytest.mark.parametrize(
    ("graph", "message"),
    [
        (_graph(linked=False), "connect Agent App Query to Agent App Skeleton"),
        (_graph(skeleton="{not json"), "not valid JSON"),
    ],
)
def test_refuses_broken_canvases(graph, message):
    with pytest.raises(AppFlowError, match=message):
        app_publish_body(graph, flow_id="f", a2a_config={})


def test_problems_are_listed_one_per_line():
    detail = {"message": "the app can't be published", "problems": [
        {"where": "screens.home.data.x", "message": "service does not provide 'nope'"},
        {"where": "prices.ask", "message": "'ask' is billable but has no cap"},
    ]}
    text = problems_message(detail)
    assert "service does not provide 'nope'" in text and "no cap" in text and text.count("\n- ") == 2


def test_store_is_optional():
    body = app_publish_body(_graph(store=False), flow_id="f", a2a_config={})
    assert body["store"] == {"namespaces": {}}
    assert json.dumps(body)


def test_a_linked_studio_draft_replaces_the_json():
    graph = _graph(skeleton="{not json")
    graph["nodes"][0]["data"]["node"]["template"]["draft_id"] = {"value": "d-1"}
    body = app_publish_body(graph, flow_id="f", a2a_config={})
    assert body["draft_id"] == "d-1" and "skeleton" not in body
