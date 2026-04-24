import asyncio
import json
from collections import defaultdict
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile
from lfx.base.agents.utils import safe_cache_get, safe_cache_set
from lfx.base.mcp.util import update_tools
from sqlmodel import select

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.v2.files import (
    MCP_SERVERS_FILE,
    download_file,
    edit_file_name,
    get_file_by_name,
    get_mcp_file,
    upload_user_file,
)
from langflow.api.v2.schemas import MCPServerConfig
from langflow.logging import logger
from langflow.services.database.models.file.model import File as UserFile
from langflow.services.database.models.user.model import User
from langflow.services.database.models.user_mcp_server.model import UserMCPServer
from langflow.services.deps import get_settings_service, get_shared_component_cache_service, get_storage_service
from langflow.services.settings.service import SettingsService
from langflow.services.storage.service import StorageService

router = APIRouter(tags=["MCP"], prefix="/mcp")

# Per-user locks to serialize update_server() calls and prevent lost updates
# from the non-atomic read-modify-write cycle on the MCP config file.
_update_server_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)


async def upload_server_config(
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    content_str = json.dumps(server_config)
    content_bytes = content_str.encode("utf-8")  # Convert to bytes
    file_obj = BytesIO(content_bytes)  # Use BytesIO for binary data

    mcp_file = await get_mcp_file(current_user, extension=True)
    upload_file = UploadFile(file=file_obj, filename=mcp_file, size=len(content_str))

    return await upload_user_file(
        file=upload_file,
        session=session,
        current_user=current_user,
        storage_service=storage_service,
        settings_service=settings_service,
    )


async def get_server_list(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    # Backwards compatibilty with old format file name
    mcp_file = await get_mcp_file(current_user)
    old_format_config_file = await get_file_by_name(MCP_SERVERS_FILE, current_user, session)
    if old_format_config_file:
        await edit_file_name(old_format_config_file.id, mcp_file, current_user, session)

    # Read the server configuration from a file using the files api
    server_config_file = await get_file_by_name(mcp_file, current_user, session)

    # Attempt to download the configuration file content
    try:
        server_config_bytes = await download_file(
            server_config_file.id if server_config_file else None,
            current_user,
            session,
            storage_service=storage_service,
            return_content=True,
        )
    except (FileNotFoundError, HTTPException):
        if server_config_file:
            # DB record exists but storage file is missing — likely a transient state
            # during a concurrent update_server() write cycle. Return empty config
            # WITHOUT persisting to avoid permanently wiping existing servers.
            logger.warning(
                "MCP config file missing from storage for user %s (transient). "
                "Returning empty config without persisting.",
                current_user.id,
            )
            return {"mcpServers": {}}

        # No DB record and no storage file — genuinely first-time use. Create empty config.
        await upload_server_config(
            {"mcpServers": {}},
            current_user,
            session,
            storage_service=storage_service,
            settings_service=settings_service,
        )

        # Fetch and download again
        mcp_file = await get_mcp_file(current_user)
        server_config_file = await get_file_by_name(mcp_file, current_user, session)
        if not server_config_file:
            raise HTTPException(status_code=500, detail="Failed to create MCP Servers configuration file") from None

        server_config_bytes = await download_file(
            server_config_file.id,
            current_user,
            session,
            storage_service=storage_service,
            return_content=True,
        )

    # Parse JSON content
    try:
        servers = json.loads(server_config_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Invalid server configuration file format.") from None

    return servers


async def get_server(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    server_list: dict | None = None,
):
    """Get a specific server configuration."""
    if server_list is None:
        server_list = await get_server_list(current_user, session, storage_service, settings_service)

    if server_name not in server_list["mcpServers"]:
        return None

    return server_list["mcpServers"][server_name]


async def _upsert_server_visibility(
    server_name: str,
    visibility: str,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> None:
    """Create or update the UserMCPServer visibility record for a given server."""
    existing = (
        await session.exec(
            select(UserMCPServer).where(
                UserMCPServer.user_id == current_user.id,
                UserMCPServer.name == server_name,
            )
        )
    ).first()
    if existing:
        existing.mcp_visibility = visibility
        session.add(existing)
    else:
        session.add(
            UserMCPServer(user_id=current_user.id, name=server_name, mcp_visibility=visibility)
        )
    await session.commit()


async def _delete_server_visibility(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
) -> None:
    """Remove the UserMCPServer visibility record for a given server."""
    existing = (
        await session.exec(
            select(UserMCPServer).where(
                UserMCPServer.user_id == current_user.id,
                UserMCPServer.name == server_name,
            )
        )
    ).first()
    if existing:
        await session.delete(existing)
        await session.commit()


async def _get_visibility_map(
    current_user: CurrentActiveUser,
    session: DbSession,
) -> dict[str, str]:
    """Return a name → mcp_visibility dict for the current user's servers."""
    rows = (
        await session.exec(
            select(UserMCPServer).where(UserMCPServer.user_id == current_user.id)
        )
    ).all()
    return {row.name: row.mcp_visibility for row in rows}


async def _get_public_servers_from_other_users(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: StorageService,
) -> list[dict]:
    """Return server entries for public MCP servers owned by other users.

    Each entry has keys: name, config (dict), owner_user_id (UUID), owner_username (str).
    Servers that cannot be read (missing file, JSON error) are silently skipped.
    """
    # Fetch all public visibility rows that don't belong to the current user
    public_rows = (
        await session.exec(
            select(UserMCPServer).where(
                UserMCPServer.mcp_visibility == "public",
                UserMCPServer.user_id != current_user.id,
            )
        )
    ).all()

    if not public_rows:
        return []

    # Group by owner user_id → set of server names
    from collections import defaultdict
    owner_servers: dict = defaultdict(set)
    for row in public_rows:
        owner_servers[row.user_id].add(row.name)

    # Fetch owner usernames in one query
    owner_ids = list(owner_servers.keys())
    owner_users = (
        await session.exec(select(User).where(User.id.in_(owner_ids)))
    ).all()
    owner_username_map = {u.id: u.username for u in owner_users}

    results: list[dict] = []
    for owner_id, server_names in owner_servers.items():
        # Build the expected MCP file name for this owner
        mcp_file_name = f"_mcp_servers_{owner_id}"
        # Look up the file DB record (without current_user scoping)
        file_record = (
            await session.exec(
                select(UserFile).where(
                    UserFile.user_id == owner_id,
                    UserFile.name == mcp_file_name,
                )
            )
        ).first()
        if not file_record:
            continue
        # Download the JSON from storage using the owner's user_id as flow_id
        try:
            from pathlib import Path
            file_name = Path(file_record.path).name
            raw = await storage_service.get_file(flow_id=str(owner_id), file_name=file_name)
            if raw is None:
                continue
            # raw may be bytes or str
            if isinstance(raw, (bytes, bytearray)):
                content = raw.decode("utf-8")
            else:
                content = str(raw)
            servers_json = json.loads(content)
        except Exception as exc:  # noqa: BLE001
            await logger.awarning("Failed to read MCP config for owner %s: %s", owner_id, exc)
            continue

        for server_name in server_names:
            config = servers_json.get("mcpServers", {}).get(server_name)
            if config is None:
                continue
            results.append({
                "name": server_name,
                "config": config,
                "owner_user_id": owner_id,
                "owner_username": owner_username_map.get(owner_id, str(owner_id)),
            })

    return results


# Define a Get servers endpoint
@router.get("/servers")
async def get_servers(
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    *,
    action_count: bool | None = None,
):
    """Get the list of available servers."""
    import asyncio

    from lfx.base.mcp.util import MCPStdioClient, MCPStreamableHttpClient

    server_list = await get_server_list(current_user, session, storage_service, settings_service)
    visibility_map = await _get_visibility_map(current_user, session)
    foreign_public = await _get_public_servers_from_other_users(current_user, session, storage_service)

    if not action_count:
        # Return only the server names, with mode and toolsCount as None
        own_entries = [
            {
                "name": server_name,
                "mode": None,
                "toolsCount": None,
                "mcp_visibility": visibility_map.get(server_name, "private"),
                "is_owner": True,
                "owner_username": current_user.username,
            }
            for server_name in server_list["mcpServers"]
        ]
        foreign_entries = [
            {
                "name": s["name"],
                "mode": None,
                "toolsCount": None,
                "mcp_visibility": "public",
                "is_owner": False,
                "owner_username": s["owner_username"],
            }
            for s in foreign_public
        ]
        return own_entries + foreign_entries

    # Check all of the tool counts for each server concurrently
    async def check_server(
        server_name: str,
        mcp_visibility: str = "private",
        is_owner: bool = True,
        owner_username: str = "",
        server_config_override: dict | None = None,
    ) -> dict:
        server_info: dict[str, str | int | None] = {
            "name": server_name,
            "mode": None,
            "toolsCount": None,
            "mcp_visibility": mcp_visibility,
            "is_owner": is_owner,
            "owner_username": owner_username,
        }
        # Create clients that we control so we can clean them up after
        mcp_stdio_client = MCPStdioClient()
        mcp_streamable_http_client = MCPStreamableHttpClient()
        try:
            # Get global variables from database for header resolution
            request_variables = {}
            try:
                from sqlmodel import select

                from langflow.services.auth import utils as auth_utils
                from langflow.services.database.models.variable.model import Variable

                # Load variables directly from database and decrypt ALL types (including CREDENTIAL)
                stmt = select(Variable).where(Variable.user_id == current_user.id)
                variables = list((await session.exec(stmt)).all())

                # Decrypt variables based on type (following the pattern from get_all_decrypted_variables)
                for variable in variables:
                    if variable.name and variable.value:
                        # Prior to v1.8, both Generic and Credential variables were encrypted.
                        # As such, must attempt to decrypt both types to ensure backwards-compatibility.
                        try:
                            decrypted_value = auth_utils.decrypt_api_key(variable.value)
                            request_variables[variable.name] = decrypted_value
                        except Exception as e:  # noqa: BLE001
                            await logger.aerror(
                                f"Failed to decrypt credential variable '{variable.name}': {e}. "
                                "This credential will not be available for MCP server."
                            )
            except Exception as e:  # noqa: BLE001
                await logger.awarning(f"Failed to load global variables for MCP server test: {e}")

            effective_config = server_config_override if server_config_override is not None else server_list["mcpServers"][server_name]
            mode, tool_list, _ = await update_tools(
                server_name=server_name,
                server_config=effective_config,
                mcp_stdio_client=mcp_stdio_client,
                mcp_streamable_http_client=mcp_streamable_http_client,
                request_variables=request_variables,
            )
            server_info["mode"] = mode.lower()
            server_info["toolsCount"] = len(tool_list)
            if len(tool_list) == 0:
                server_info["error"] = "No tools found"
        except ValueError as e:
            # Configuration validation errors, invalid URLs, etc.
            await logger.aerror(f"Configuration error for server {server_name}: {e}")
            server_info["error"] = f"Configuration error: {e}"
        except ConnectionError as e:
            # Network connection and timeout issues
            await logger.aerror(f"Connection error for server {server_name}: {e}")
            server_info["error"] = f"Connection failed: {e}"
        except (TimeoutError, asyncio.TimeoutError) as e:
            # Timeout errors
            await logger.aerror(f"Timeout error for server {server_name}: {e}")
            server_info["error"] = "Timeout when checking server tools"
        except OSError as e:
            # System-level errors (process execution, file access)
            await logger.aerror(f"System error for server {server_name}: {e}")
            server_info["error"] = f"System error: {e}"
        except (KeyError, TypeError) as e:
            # Data parsing and access errors
            await logger.aerror(f"Data error for server {server_name}: {e}")
            server_info["error"] = f"Configuration data error: {e}"
        except (RuntimeError, ProcessLookupError, PermissionError) as e:
            # Runtime and process-related errors
            await logger.aerror(f"Runtime error for server {server_name}: {e}")
            server_info["error"] = f"Runtime error: {e}"
        except Exception as e:  # noqa: BLE001
            # Generic catch-all for other exceptions (including ExceptionGroup)
            if hasattr(e, "exceptions") and e.exceptions:
                # Extract the first underlying exception for a more meaningful error message
                underlying_error = e.exceptions[0]
                if hasattr(underlying_error, "exceptions"):
                    await logger.aerror(
                        f"Error checking server {server_name}: {underlying_error}, {underlying_error.exceptions}"
                    )
                    underlying_error = underlying_error.exceptions[0]
                else:
                    await logger.aexception(f"Error checking server {server_name}: {underlying_error}")
                server_info["error"] = f"Error loading server: {underlying_error}"
            else:
                await logger.aexception(f"Error checking server {server_name}: {e}")
                server_info["error"] = f"Error loading server: {e}"
        finally:
            # Always disconnect clients to prevent mcp-proxy process leaks
            # These clients spawn subprocesses that need to be explicitly terminated
            await mcp_stdio_client.disconnect()
            await mcp_streamable_http_client.disconnect()
        return server_info

    # Run all server checks concurrently
    own_tasks = [
        check_server(
            server,
            visibility_map.get(server, "private"),
            is_owner=True,
            owner_username=current_user.username,
        )
        for server in server_list["mcpServers"]
    ]
    foreign_tasks = [
        check_server(
            s["name"],
            "public",
            is_owner=False,
            owner_username=s["owner_username"],
            server_config_override=s["config"],
        )
        for s in foreign_public
    ]
    return await asyncio.gather(*(own_tasks + foreign_tasks), return_exceptions=True)


@router.get("/servers/{server_name}")
async def get_server_endpoint(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    """Get a specific server (own servers only — for editing)."""
    config = await get_server(server_name, current_user, session, storage_service, settings_service)
    if config is None:
        raise HTTPException(status_code=404, detail="Server not found.")
    visibility_row = (
        await session.exec(
            select(UserMCPServer).where(
                UserMCPServer.user_id == current_user.id,
                UserMCPServer.name == server_name,
            )
        )
    ).first()
    return {
        **config,
        "mcp_visibility": visibility_row.mcp_visibility if visibility_row else "private",
        "is_owner": True,
        "owner_username": current_user.username,
    }


async def update_server(
    server_name: str,
    server_config: dict,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
    *,
    check_existing: bool = False,
    delete: bool = False,
):
    # Extract visibility before writing config to JSON (it lives in DB, not the file)
    visibility = server_config.pop("mcp_visibility", None)

    # Only superusers (platform_admin) may set a server public; everyone else is forced private.
    if visibility == "public" and not current_user.is_superuser:
        visibility = "private"

    async with _update_server_locks[str(current_user.id)]:
        server_list = await get_server_list(current_user, session, storage_service, settings_service)

        # Validate server name
        if check_existing and server_name in server_list["mcpServers"]:
            raise HTTPException(status_code=500, detail="Server already exists.")

        # Handle the delete case
        if delete:
            if server_name in server_list["mcpServers"]:
                del server_list["mcpServers"][server_name]
            else:
                raise HTTPException(status_code=500, detail="Server not found.")
            await _delete_server_visibility(server_name, current_user, session)
        else:
            server_list["mcpServers"][server_name] = server_config
            await _upsert_server_visibility(
                server_name, visibility or "private", current_user, session
            )

        # Upload the updated server configuration
        # (upload_user_file handles replacing the existing MCP file atomically)
        await upload_server_config(
            server_list, current_user, session, storage_service=storage_service, settings_service=settings_service
        )

        shared_component_cache_service = get_shared_component_cache_service()
        # Clear the servers cache
        servers = safe_cache_get(shared_component_cache_service, "servers", {})
        if isinstance(servers, dict):
            if server_name in servers:
                del servers[server_name]
            safe_cache_set(shared_component_cache_service, "servers", servers)

        if delete:
            return None

        config = await get_server(
            server_name,
            current_user,
            session,
            storage_service,
            settings_service,
            server_list=server_list,
        )
        return {**(config or {}), "mcp_visibility": visibility or "private"}


@router.post("/servers/{server_name}")
async def add_server(
    server_name: str,
    *,
    server_config: Annotated[MCPServerConfig, Body()],
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    return await update_server(
        server_name,
        server_config.model_dump(exclude_unset=True),
        current_user,
        session,
        storage_service,
        settings_service,
        check_existing=True,
    )


@router.patch("/servers/{server_name}")
async def update_server_endpoint(
    server_name: str,
    *,
    server_config: Annotated[MCPServerConfig, Body()],
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    return await update_server(
        server_name,
        server_config.model_dump(exclude_unset=True),
        current_user,
        session,
        storage_service,
        settings_service,
    )


@router.delete("/servers/{server_name}")
async def delete_server(
    server_name: str,
    current_user: CurrentActiveUser,
    session: DbSession,
    storage_service: Annotated[StorageService, Depends(get_storage_service)],
    settings_service: Annotated[SettingsService, Depends(get_settings_service)],
):
    return await update_server(
        server_name,
        {},
        current_user,
        session,
        storage_service,
        settings_service,
        delete=True,
    )
