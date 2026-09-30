"""Talking to the marketplace as the producer who is building.

The builder session already fixes who the producer is and which profile
(personal or org) they are acting under. We pass that to the marketplace with
the internal key, the same way Publish does, so the canvas only ever sees and
uses the App Services of the profile it belongs to.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

_TIMEOUT = httpx.Timeout(connect=10.0, read=240.0, write=30.0, pool=10.0)


class MarketplaceError(Exception):
    def __init__(self, status: int, detail: Any):
        self.status = status
        self.detail = detail
        super().__init__(_message(detail))


def _message(detail: Any) -> str:
    if isinstance(detail, dict):
        return str(detail.get("message") or detail.get("code") or detail)
    return str(detail)


def base_url() -> str:
    return os.getenv("MARKETPLACE_SERVICE_BASE_URL", "http://localhost:8014").rstrip("/")


async def producer_headers(user_id: Any) -> dict[str, str]:
    try:
        from langflow.services.database.models.user.crud import get_user_by_id

        from lfx.services.deps import session_scope
    except ImportError as exc:
        msg = "Agent Apps need the full Langflow installation."
        raise ImportError(msg) from exc
    if not user_id:
        msg = "no builder user on this component"
        raise ValueError(msg)
    async with session_scope() as db:
        user = await get_user_by_id(db, user_id)
    if user is None:
        msg = "builder user not found"
        raise ValueError(msg)
    return headers_for(user.username)


def headers_for(scoped_username: str) -> dict[str, str]:
    from langflow.api.v1.oidc_sso import split_scoped_username

    username, org_id = split_scoped_username(scoped_username)
    headers = {"x-api-key": os.getenv("AGENTS_MARKET_INTERNAL_API_KEY", ""), "x-username": username}
    if org_id:
        headers["x-org-id"] = org_id
    return headers


async def call(headers: dict[str, str], method: str, path: str, body: Any = None) -> Any:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.request(method, f"{base_url()}{path}", json=body, headers=headers)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        raise MarketplaceError(resp.status_code, detail)
    if resp.status_code == 204 or not resp.content:
        return None
    return resp.json()


def service_label(svc: dict) -> str:
    """Dropdown label; the id rides at the end so the choice survives renames."""
    return f"{svc['name']} {svc['manifest']['version'] if svc.get('manifest') else ''} ({svc['mode']}) [{svc['id']}]"


def service_id_of(label: str | None) -> str | None:
    if not label or "[" not in label:
        return None
    return label.rsplit("[", 1)[1].rstrip("]").strip() or None
