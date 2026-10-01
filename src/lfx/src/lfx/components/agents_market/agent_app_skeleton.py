import json

from lfx.components.agents_market._marketplace import MarketplaceError, call, producer_headers
from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, MessageTextInput, MultilineInput, Output
from lfx.schema.data import Data

_EXAMPLE = json.dumps(
    {
        "version": 1,
        "title": "My App",
        "nav": [{"screen": "home", "label": "Home"}],
        "screens": {"home": {"title": "Home", "data": {}, "layout": {"type": "stack", "children": []}}},
    },
    indent=2,
)


class AgentAppSkeletonComponent(Component):
    """The screens of an Agent App, as JSON (docs/agent-apps.md).

    Each screen names the intents it loads (``data``) and binds fields with
    ``$data.<key>.<field>``. Building this node checks every screen against the
    connected service, exactly as Publish will: any intent the service lacks,
    field its result doesn't have, namespace the User Store doesn't grant, or
    paid intent without a cap turns the node red with the reason.
    """

    display_name = "Agent App Skeleton"
    description = "The screens of an Agent App. Checked against the Agent App Query service on build."
    icon = "layout-dashboard"
    name = "AgentAppSkeleton"

    inputs = [
        DataInput(
            name="query",
            display_name="Query",
            info="Connect an Agent App Query.",
            required=True,
        ),
        MultilineInput(
            name="skeleton",
            display_name="Skeleton (JSON)",
            info="Screens, navigation and bindings. See examples/stock-advisor-service/app/skeleton.json. "
            "Ignored while a Studio draft is linked.",
            value=_EXAMPLE,
            required=True,
        ),
        MessageTextInput(
            name="draft_id",
            display_name="Studio draft",
            info="Set by 'Edit app UI'. While set, the screens come from that marketplace Studio draft; "
            "clear it to use the JSON above again.",
            advanced=True,
        ),
    ]

    outputs = [Output(display_name="App", name="app", method="build_app")]

    async def build_app(self) -> Data:
        query = (self.query.data if isinstance(self.query, Data) else self.query) or {}
        if not query.get("service_id"):
            msg = "Connect an Agent App Query with a service selected."
            raise ValueError(msg)
        headers = await producer_headers(self.user_id)
        draft_id = (self.draft_id or "").strip()
        if draft_id:
            try:
                skeleton = (await call(headers, "GET", f"/api/app-drafts/{draft_id}"))["skeleton"]
            except MarketplaceError as exc:
                msg = f"Could not read Studio draft {draft_id}: {exc}"
                raise ValueError(msg) from exc
        else:
            try:
                skeleton = json.loads(self.skeleton or "")
            except json.JSONDecodeError as exc:
                msg = f"Skeleton is not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}"
                raise ValueError(msg) from exc

        try:
            report = await call(
                headers,
                "POST",
                f"/api/app-services/{query['service_id']}/check",
                {
                    "skeleton": skeleton,
                    "store": query.get("store") or {},
                    "caps": query.get("caps") or {},
                    "default_cap": query.get("default_cap") or 0,
                    "sources": query.get("sources") or {},
                },
            )
        except MarketplaceError as exc:
            msg = f"Could not check the app: {exc}"
            raise ValueError(msg) from exc

        rows = report.get("rows") or {}
        if report.get("problems"):
            lines = [f"- {p['where']}: {p['message']}" for p in report["problems"]]
            msg = f"{len(lines)} problem(s) — Publish is blocked until they are fixed:\n" + "\n".join(lines)
            raise ValueError(msg)

        prices = {k: v["max_credits"] for k, v in (report.get("prices") or {}).items()}
        self.status = f"OK: {len(rows)} intents in use, all fields found" + (
            "; prices " + ", ".join(f"{k} up to {v}" for k, v in sorted(prices.items())) if prices else "; all free"
        )
        return Data(
            data={
                "app_protocol": "agents-market/app@1",
                "service_id": query["service_id"],
                "service": query.get("service"),
                "skeleton": skeleton,
                "store": query.get("store") or {},
                "caps": query.get("caps") or {},
                "default_cap": query.get("default_cap") or 0,
                "sources": query.get("sources") or {},
                "check": report,
            }
        )
