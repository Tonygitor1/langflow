from typing import Any

from lfx.components.agents_market._marketplace import (
    MarketplaceError,
    call,
    producer_headers,
    service_id_of,
    service_label,
)
from lfx.custom.custom_component.component import Component
from lfx.io import DataInput, DropdownInput, IntInput, Output, TableInput
from lfx.schema.data import Data
from lfx.schema.table import EditMode

PLATFORM_CEILING = 100


class AgentAppQueryComponent(Component):
    """The backend of an Agent App: an App Service you registered in
    Settings -> App Services, plus what each paid intent may cost.

    Pricing: an intent is paid only if the service's code says so
    (``Billing(max_credits=...)``). Set the most a user can be charged per
    query; it can't exceed the service's max. That number is what users are
    promised, frozen at Publish.
    """

    display_name = "Agent App Query"
    description = "Picks the App Service behind an Agent App and sets the price cap of each paid intent."
    icon = "server"
    name = "AgentAppQuery"

    inputs = [
        DropdownInput(
            name="service",
            display_name="App Service",
            info="Registered in Settings -> App Services, for this profile.",
            options=[],
            refresh_button=True,
            real_time_refresh=True,
            required=True,
        ),
        DataInput(
            name="store",
            display_name="User Store",
            info="Connect an Agent User Store.",
            required=False,
        ),
        DataInput(
            name="sources",
            display_name="Data sources",
            info="Connect Price Source, News Sources or Research Documents nodes: what users may pick from.",
            required=False,
            is_list=True,
        ),
        IntInput(
            name="default_cap",
            display_name="Default cap",
            info="Credits per query for paid intents without their own row. 0 = none.",
            value=0,
        ),
        TableInput(
            name="prices",
            display_name="Pricing (paid intents)",
            info="Free intents are always free. Cap = the most a user pays per query.",
            table_schema=[
                {"name": "intent", "display_name": "Intent", "type": "str", "edit_mode": EditMode.INLINE},
                {"name": "service_max", "display_name": "Service max", "type": "str", "edit_mode": EditMode.INLINE},
                {"name": "cap", "display_name": "Max per query", "type": "str", "edit_mode": EditMode.INLINE,
                 "description": "Empty = use the default cap."},
                {"name": "description", "display_name": "What it is", "type": "str", "edit_mode": EditMode.POPOVER},
            ],
            value=[],
        ),
    ]

    outputs = [Output(display_name="Query", name="query", method="build_query")]

    async def _services(self) -> list[dict]:
        headers = await producer_headers(self.user_id)
        return [s for s in await call(headers, "GET", "/api/app-services") if s["status"] == "verified"]

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        if field_name not in ("service", None):
            return build_config
        try:
            services = await self._services()
        except (MarketplaceError, ValueError, ImportError) as exc:
            build_config["service"]["info"] = f"Could not load App Services: {exc}"
            return build_config
        build_config["service"]["options"] = [service_label(s) for s in services]
        chosen = service_id_of(field_value if field_name == "service" else build_config["service"].get("value"))
        svc = next((s for s in services if s["id"] == chosen), None)
        if svc is not None:
            existing = {r.get("intent"): r.get("cap", "") for r in build_config["prices"].get("value") or []}
            build_config["prices"]["value"] = [
                {
                    "intent": name,
                    "service_max": str(spec["billing"]["max_credits"]),
                    "cap": existing.get(name, ""),
                    "description": spec["billing"].get("description") or "",
                }
                for name, spec in sorted(svc["manifest"]["intents"].items())
                if spec.get("billing")
            ]
        return build_config

    def _caps(self, manifest: dict) -> tuple[dict[str, int | None], list[str]]:
        intents = manifest["intents"]
        caps: dict[str, int | None] = {}
        problems = []
        for row in self.prices or []:
            data = row.data if isinstance(row, Data) else row
            name = str(data.get("intent") or "").strip()
            raw = str(data.get("cap") or "").strip()
            if not name:
                continue
            spec = intents.get(name)
            if spec is None:
                problems.append(f"'{name}' is not an intent of this service")
                continue
            if not spec.get("billing"):
                problems.append(f"'{name}' is free in the service's code; it can't be priced")
                continue
            if not raw:
                caps[name] = None
                continue
            try:
                cap = int(raw)
            except ValueError:
                problems.append(f"'{name}': cap must be a whole number of credits")
                continue
            service_max = spec["billing"]["max_credits"]
            if cap < 1:
                problems.append(f"'{name}': cap must be at least 1 (or empty for the default)")
            elif cap > service_max:
                problems.append(f"'{name}': cap {cap} is above what the service can charge ({service_max})")
            elif cap > PLATFORM_CEILING:
                problems.append(f"'{name}': cap {cap} is above the platform limit ({PLATFORM_CEILING})")
            else:
                caps[name] = cap
        return caps, problems

    async def build_query(self) -> Data:
        service_id = service_id_of(self.service)
        if not service_id:
            msg = "Pick an App Service (Settings -> App Services to register one)."
            raise ValueError(msg)
        headers = await producer_headers(self.user_id)
        svc = await call(headers, "GET", f"/api/app-services/{service_id}")
        if svc["status"] != "verified":
            msg = f"App Service '{svc['name']}' is not verified: {svc.get('verificationError')}"
            raise ValueError(msg)
        caps, problems = self._caps(svc["manifest"])
        sources, more = self._sources()
        problems += more
        if problems:
            raise ValueError("; ".join(problems))
        store = (self.store.data if isinstance(self.store, Data) else self.store) or {"namespaces": {}}
        self.status = f"{svc['name']} {svc['manifest']['version']} ({svc['mode']}): {len(svc['manifest']['intents'])} intents"
        return Data(
            data={
                "service_id": service_id,
                "service": {"name": svc["name"], "version": svc["manifest"]["version"], "mode": svc["mode"]},
                "caps": caps,
                "default_cap": int(self.default_cap or 0),
                "store": store,
                "sources": sources,
            }
        )

    def _sources(self) -> tuple[dict[str, dict], list[str]]:
        connected = self.sources if isinstance(self.sources, list) else [self.sources] if self.sources else []
        sources: dict[str, dict] = {}
        problems = []
        for item in connected:
            data = item.data if isinstance(item, Data) else item
            category = (data or {}).get("category")
            if not category:
                continue
            if category in sources:
                problems.append(f"two nodes offer {category}; keep one")
                continue
            sources[category] = data["slot"]
        return sources, problems
