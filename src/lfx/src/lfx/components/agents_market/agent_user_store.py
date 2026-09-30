import re

from lfx.custom.custom_component.component import Component
from lfx.io import Output, TableInput
from lfx.schema.data import Data
from lfx.schema.table import EditMode

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class AgentUserStoreComponent(Component):
    """Declares the per-user data an Agent App keeps.

    Each row is one namespace. The platform keeps a separate copy per user
    (and per profile), and only intents granted a namespace in their manifest
    can read or write it. Mark a namespace **sensitive** (holdings, balances)
    and users must consent before the app's service may read it, and no
    request that reads it can also call a model.
    """

    display_name = "Agent User Store"
    description = "Per-user data for an Agent App: one row per namespace. Connect it to Agent App Query."
    icon = "database"
    name = "AgentUserStore"

    inputs = [
        TableInput(
            name="namespaces",
            display_name="Namespaces",
            info="One row per namespace of per-user data.",
            required=True,
            table_schema=[
                {
                    "name": "name",
                    "display_name": "Namespace",
                    "type": "str",
                    "description": "Lowercase, e.g. watchlist or holdings.",
                    "default": "watchlist",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "sensitivity",
                    "display_name": "Sensitivity",
                    "type": "str",
                    "description": "sensitive = users consent first; never mixed with a model call.",
                    "options": ["normal", "sensitive"],
                    "default": "normal",
                    "edit_mode": EditMode.INLINE,
                },
                {
                    "name": "description",
                    "display_name": "Shown to users",
                    "type": "str",
                    "description": "What this data is, in the user's words. Shown on the consent screen.",
                    "default": "",
                    "edit_mode": EditMode.POPOVER,
                },
            ],
            value=[{"name": "watchlist", "sensitivity": "normal", "description": ""}],
        ),
    ]

    outputs = [Output(display_name="User Store", name="store", method="build_store")]

    def build_store(self) -> Data:
        namespaces: dict[str, dict] = {}
        problems = []
        for row in self.namespaces or []:
            row_data = row.data if isinstance(row, Data) else row
            name = str(row_data.get("name") or "").strip()
            if not _NAME.match(name):
                problems.append(f"'{name}' is not a valid namespace (lowercase letters, digits, _)")
                continue
            if name in namespaces:
                problems.append(f"'{name}' is listed twice")
                continue
            entry: dict = {}
            if (row_data.get("sensitivity") or "normal") == "sensitive":
                entry["sensitivity"] = "sensitive"
            if row_data.get("description"):
                entry["description"] = str(row_data["description"])[:300]
            namespaces[name] = entry
        if problems:
            raise ValueError("; ".join(problems))
        sensitive = sorted(n for n, e in namespaces.items() if e.get("sensitivity") == "sensitive")
        self.status = f"{len(namespaces)} namespaces" + (f", sensitive: {', '.join(sensitive)}" if sensitive else "")
        return Data(data={"namespaces": namespaces})
