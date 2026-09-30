"""Settings -> App Services: the builder's view of the marketplace's App Services registry.

The browser talks only to Langflow; this forwards to the marketplace as the
signed-in producer, in the profile (personal or org) of this builder session.
See 0to1-agents-market/docs/agent-apps.md.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from lfx.components.agents_market._marketplace import MarketplaceError, call, headers_for
from pydantic import BaseModel, Field

from langflow.api.utils import CurrentActiveUser

router = APIRouter(prefix="/app-services", tags=["App Services"])


class ServiceCreate(BaseModel):
    mode: str = Field(pattern="^(url|image)$")
    url: str | None = None
    image: str | None = None
    port: int = 9000
    registry_username: str | None = None
    registry_password: str | None = None


async def _forward(user: Any, method: str, path: str, body: Any = None) -> Any:
    try:
        return await call(headers_for(user.username), method, f"/api/app-services{path}", body)
    except MarketplaceError as exc:
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail})
    except Exception as exc:  # noqa: BLE001 — a network error must reach the UI as a message
        raise HTTPException(status_code=502, detail=f"marketplace unreachable: {exc}") from exc


@router.get("")
async def list_services(current_user: CurrentActiveUser):
    return await _forward(current_user, "GET", "")


@router.post("", status_code=201)
async def create_service(body: ServiceCreate, current_user: CurrentActiveUser):
    return await _forward(current_user, "POST", "", body.model_dump(exclude_none=True))


@router.get("/{service_id}")
async def get_service(service_id: UUID, current_user: CurrentActiveUser):
    return await _forward(current_user, "GET", f"/{service_id}")


@router.post("/{service_id}/verify")
async def verify_service(service_id: UUID, current_user: CurrentActiveUser):
    return await _forward(current_user, "POST", f"/{service_id}/verify")


@router.post("/{service_id}/rotate-secret")
async def rotate_secret(service_id: UUID, current_user: CurrentActiveUser):
    return await _forward(current_user, "POST", f"/{service_id}/rotate-secret")


@router.delete("/{service_id}", status_code=204)
async def delete_service(service_id: UUID, current_user: CurrentActiveUser):
    return await _forward(current_user, "DELETE", f"/{service_id}")
