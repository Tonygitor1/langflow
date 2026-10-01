"""What an Agent App offers users for one data category; the three source nodes share it.

A source node lists the connectors users may pick from and what a user gets
before they pick. Connect it to Agent App Query; Publish freezes the offer
into the App Manifest, and each user then picks their own in the app.
"""

from __future__ import annotations

from typing import Any

from lfx.components.agents_market._marketplace import MarketplaceError, call, producer_headers
from lfx.io import MessageTextInput, MultiselectInput, Output
from lfx.schema.data import Data


class SourceNode:
    """Mixed into each source component (not a Component itself, so it isn't a node of its own)."""

    category: str = ""
    cardinality: str = "many"
    icon = "database-zap"

    inputs = [
        MultiselectInput(
            name="options",
            display_name="Sources users may pick",
            info="Connectors that serve this category.",
            options=[],
            value=[],
            refresh_button=True,
            real_time_refresh=True,
            required=True,
        ),
        MultiselectInput(
            name="default",
            display_name="Default",
            info="What a user reads until they pick. Accounts users connect themselves can't be a default.",
            options=[],
            value=[],
        ),
        MessageTextInput(
            name="label",
            display_name="Label shown to users",
            info="Optional; the category's own name if empty.",
            advanced=True,
        ),
    ]

    outputs = [Output(display_name="Source", name="source", method="build_source")]

    async def _connectors(self) -> dict[str, dict]:
        headers = await producer_headers(self.user_id)
        catalog = await call(headers, "GET", "/api/app-sources/catalog")
        return {cid: c for cid, c in catalog["connectors"].items() if self.category in c["categories"]}

    async def update_build_config(self, build_config: dict, field_value: Any, field_name: str | None = None) -> dict:
        if field_name not in ("options", None):
            return build_config
        try:
            connectors = await self._connectors()
        except (MarketplaceError, ValueError, ImportError) as exc:
            build_config["options"]["info"] = f"Could not load the source catalog: {exc}"
            return build_config
        build_config["options"]["options"] = sorted(connectors)
        chosen = [c for c in (field_value if field_name == "options" else build_config["options"].get("value")) or []
                  if c in connectors]
        build_config["default"]["options"] = [c for c in chosen if not connectors[c]["private"]]
        notes = [f"{cid}: {c['name']}" + (" (user's own account)" if c["private"] else "") for cid, c in sorted(connectors.items())]
        build_config["options"]["info"] = "Connectors: " + "; ".join(notes)
        return build_config

    def build_source(self) -> Data:
        options = [str(o) for o in self.options or []]
        default = [str(d) for d in self.default or []]
        if not options:
            msg = "Pick at least one source users may choose from."
            raise ValueError(msg)
        if not set(default) <= set(options):
            msg = f"The default {sorted(set(default) - set(options))} isn't one of the sources offered."
            raise ValueError(msg)
        if self.cardinality == "one" and len(default) != 1:
            msg = "Users read exactly one source here: pick one default."
            raise ValueError(msg)
        slot: dict[str, Any] = {"options": options, "default": default}
        if (self.label or "").strip():
            slot["label"] = self.label.strip()[:80]
        self.status = f"{self.category}: {', '.join(options)} (default {', '.join(default) or 'none'})"
        return Data(data={"category": self.category, "slot": slot})
