import asyncio
import json
import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException
from lfx.base.models.unified_models import get_model_provider_variable_mapping, validate_model_provider_key
from sqlalchemy.exc import NoResultFound
from sqlmodel import select

from langflow.api.utils import CurrentActiveUser, DbSession
from langflow.api.v1.models import (
    DISABLED_MODELS_VAR,
    ENABLED_MODELS_VAR,
    get_model_names_for_provider,
    get_provider_from_variable_name,
)
from langflow.api.v1.schemas.deployments import DetectVarsRequest, DetectVarsResponse
from langflow.services.auth import utils as auth_utils
from langflow.services.database.models.flow_version.crud import get_flow_version_entries_by_ids
from langflow.services.database.models.user.model import User
from langflow.services.database.models.variable.model import Variable, VariableCreate, VariableRead, VariableUpdate
from langflow.services.deps import get_variable_service
from langflow.services.variable.constants import CREDENTIAL_TYPE, GENERIC_TYPE
from langflow.services.variable.service import DatabaseVariableService

router = APIRouter(prefix="/variables", tags=["Variables"])
model_provider_variable_mapping = get_model_provider_variable_mapping()
logger = logging.getLogger(__name__)

# Reserved provider variable names that are always public (except OLLAMA_BASE_URL which is always private).
# These are variables managed on the Model Providers page.
_OLLAMA_BASE_URL_VAR = "OLLAMA_BASE_URL"
RESERVED_PUBLIC_VARS: set[str] = {
    name for name in model_provider_variable_mapping.values() if name != _OLLAMA_BASE_URL_VAR
}


async def _cleanup_model_list_variable(
    variable_service: DatabaseVariableService,
    user_id: UUID,
    variable_name: str,
    models_to_remove: set[str],
    session: DbSession,
) -> None:
    """Remove specified models from a model list variable (disabled or enabled models).

    If all models are removed, the variable is deleted entirely.
    If the variable doesn't exist, this is a no-op.
    """
    try:
        model_list_var = await variable_service.get_variable_object(
            user_id=user_id, name=variable_name, session=session
        )
    except ValueError:
        # Variable doesn't exist, nothing to clean up
        return

    if not model_list_var or not model_list_var.value:
        return

    # Parse current models
    try:
        current_models = set(json.loads(model_list_var.value))
    except (json.JSONDecodeError, TypeError):
        current_models = set()

    # Filter out the provider's models
    filtered_models = current_models - models_to_remove

    # Nothing changed, no update needed
    if filtered_models == current_models:
        return

    if filtered_models:
        # Update with filtered list
        if model_list_var.id is not None:
            await variable_service.update_variable_fields(
                user_id=user_id,
                variable_id=model_list_var.id,
                variable=VariableUpdate(
                    id=model_list_var.id,
                    name=variable_name,
                    value=json.dumps(list(filtered_models)),
                    type=GENERIC_TYPE,
                ),
                session=session,
            )
    else:
        # No models left, delete the variable
        await variable_service.delete_variable(user_id=user_id, name=variable_name, session=session)


async def _cleanup_provider_models(
    variable_service: DatabaseVariableService,
    user_id: UUID,
    provider: str,
    session: DbSession,
) -> None:
    """Clean up disabled and enabled model lists for a deleted provider credential."""
    try:
        provider_models = get_model_names_for_provider(provider)
    except ValueError:
        logger.exception("Provider model retrieval failed")
        return

    # Clean up disabled and enabled models
    await _cleanup_model_list_variable(variable_service, user_id, DISABLED_MODELS_VAR, provider_models, session)
    await _cleanup_model_list_variable(variable_service, user_id, ENABLED_MODELS_VAR, provider_models, session)


@router.post("/", response_model=VariableRead, status_code=201, include_in_schema=False)
async def create_variable(
    *,
    session: DbSession,
    variable: VariableCreate,
    current_user: CurrentActiveUser,
):
    """Create a new variable."""
    variable_service = get_variable_service()
    if not variable.name and not variable.value:
        raise HTTPException(status_code=400, detail="Variable name and value cannot be empty")

    if not variable.name:
        raise HTTPException(status_code=400, detail="Variable name cannot be empty")

    if not variable.value:
        raise HTTPException(status_code=400, detail="Variable value cannot be empty")

    # Reserved provider variables (except Ollama) can only be created by platform admins.
    if variable.name in RESERVED_PUBLIC_VARS and not current_user.is_superuser:
        raise HTTPException(
            status_code=403, detail="Only platform admins can create reserved model provider variables"
        )

    if variable.name in await variable_service.list_variables(user_id=current_user.id, session=session):
        raise HTTPException(status_code=400, detail="Variable name already exists")

    # Check if the variable is a reserved model provider variable
    if variable.name in model_provider_variable_mapping.values():
        provider = get_provider_from_variable_name(variable.name)
        if provider is not None:
            # Validate that the key actually works using the Language Model Service
            # Run validation off the event loop to avoid blocking
            try:
                await asyncio.to_thread(validate_model_provider_key, provider, {variable.name: variable.value})
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e

    # Determine visibility:
    #   - Reserved provider vars (not Ollama) → always public (admin already checked above)
    #   - OLLAMA_BASE_URL → always private (any user can configure)
    #   - Non-admin users → always private
    #   - Admin users → respect what they sent (default private)
    if variable.name in RESERVED_PUBLIC_VARS:
        determined_visibility = "public"
    elif variable.name == _OLLAMA_BASE_URL_VAR:
        determined_visibility = "private"
    elif current_user.is_superuser:
        determined_visibility = variable.var_visibility or "private"
    else:
        determined_visibility = "private"

    try:
        db_var = await variable_service.create_variable(
            user_id=current_user.id,
            name=variable.name,
            value=variable.value,
            default_fields=variable.default_fields or [],
            type_=variable.type or CREDENTIAL_TYPE,
            session=session,
        )
        # Apply determined visibility (service always defaults to "private").
        if db_var.var_visibility != determined_visibility:
            db_var.var_visibility = determined_visibility
            session.add(db_var)
            await session.flush()
            await session.refresh(db_var)
        var_read = VariableRead.model_validate(db_var, from_attributes=True)
        var_read.is_owner = True
        var_read.owner_username = None
        return var_read
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/", response_model=list[VariableRead], status_code=200, include_in_schema=False)
async def read_variables(
    *,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Read all variables.

    Returns the current user's own variables plus public variables owned by other users.
    Model provider credentials are validated when they are created or updated,
    not on every read. This avoids latency from external API calls on read operations.
    """
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        msg = "Variable service is not an instance of DatabaseVariableService"
        raise TypeError(msg)
    try:
        # Own variables
        own_variables = await variable_service.get_all(user_id=current_user.id, session=session)

        # Filter out internal variables (those starting and ending with __)
        filtered_variables: list[VariableRead] = [
            var for var in own_variables if not (var.name and var.name.startswith("__") and var.name.endswith("__"))
        ]
        for var in filtered_variables:
            var.is_owner = True
            var.owner_username = None

        # Public variables from other users
        public_stmt = select(Variable).where(
            Variable.var_visibility == "public",
            Variable.user_id != current_user.id,
        )
        public_vars_db = list((await session.exec(public_stmt)).all())

        # Collect owner user IDs to fetch usernames in one query
        owner_ids = {var.user_id for var in public_vars_db}
        user_map: dict = {}
        if owner_ids:
            users_stmt = select(User).where(User.id.in_(list(owner_ids)))
            users = list((await session.exec(users_stmt)).all())
            user_map = {u.id: u.username for u in users}

        # Track names already returned (own vars) to avoid duplicates
        own_var_names = {var.name for var in filtered_variables}

        for db_var in public_vars_db:
            # Skip internal variables
            if db_var.name and db_var.name.startswith("__") and db_var.name.endswith("__"):
                continue
            # Skip if user already has their own variable with this name
            if db_var.name in own_var_names:
                continue
            # Decrypt GENERIC type for display
            value = None
            if db_var.type == GENERIC_TYPE and db_var.value:
                try:
                    value = auth_utils.decrypt_api_key(db_var.value)
                    if not value:
                        continue
                except Exception:  # noqa: BLE001
                    continue

            var_read = VariableRead.model_validate(db_var, from_attributes=True)
            if db_var.type == GENERIC_TYPE:
                var_read.value = value
            var_read.is_owner = False
            var_read.owner_username = user_map.get(db_var.user_id)
            filtered_variables.append(var_read)

        # Mark model provider credentials - validation status is based on existence
        # (actual validation happens on create/update)
        for var in filtered_variables:
            if var.name and var.name in model_provider_variable_mapping.values() and var.type == CREDENTIAL_TYPE:
                # Credential exists and was validated on save
                var.is_valid = True
                var.validation_error = None
            else:
                # Not a model provider credential
                var.is_valid = None
                var.validation_error = None

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    else:
        return filtered_variables


@router.patch("/{variable_id}", response_model=VariableRead, status_code=200, include_in_schema=False)
async def update_variable(
    *,
    session: DbSession,
    variable_id: UUID,
    variable: VariableUpdate,
    current_user: CurrentActiveUser,
):
    """Update a variable."""
    variable_service = get_variable_service()
    if not isinstance(variable_service, DatabaseVariableService):
        msg = "Variable service is not an instance of DatabaseVariableService"
        raise TypeError(msg)
    try:
        # Fetch the variable directly (without user_id filter) to support cross-user ownership check.
        raw_stmt = select(Variable).where(Variable.id == variable_id)
        existing_variable = (await session.exec(raw_stmt)).first()
        if not existing_variable:
            raise HTTPException(status_code=404, detail="Variable not found")

        # Non-superusers may only update their own variables.
        if not current_user.is_superuser and existing_variable.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You do not have permission to update this variable")

        # Reserved provider variables (not Ollama) can only be updated by platform admins.
        if existing_variable.name in RESERVED_PUBLIC_VARS and not current_user.is_superuser:
            raise HTTPException(
                status_code=403, detail="Only platform admins can update reserved model provider variables"
            )

        # Enforce visibility rules:
        #   - Reserved provider vars (not Ollama) → always public
        #   - OLLAMA_BASE_URL → always private
        #   - Non-admin users → strip any visibility change (keep existing)
        if existing_variable.name in RESERVED_PUBLIC_VARS:
            variable.var_visibility = "public"
        elif existing_variable.name == _OLLAMA_BASE_URL_VAR:
            variable.var_visibility = "private"
        elif not current_user.is_superuser:
            variable.var_visibility = None  # Exclude from update — keep existing value

        # Validate API key if updating a model provider variable
        if existing_variable.name in model_provider_variable_mapping.values() and variable.value:
            provider = get_provider_from_variable_name(existing_variable.name)
            if provider is not None:
                # Run validation off the event loop to avoid blocking
                try:
                    await asyncio.to_thread(
                        validate_model_provider_key,
                        provider,
                        {existing_variable.name: variable.value},
                    )
                except ValueError as e:
                    raise HTTPException(status_code=400, detail=str(e)) from e

        db_var = await variable_service.update_variable_fields(
            user_id=existing_variable.user_id,
            variable_id=variable_id,
            variable=variable,
            session=session,
        )
        var_read = VariableRead.model_validate(db_var, from_attributes=True)
        var_read.is_owner = db_var.user_id == current_user.id
        var_read.owner_username = None
        return var_read
    except NoResultFound as e:
        raise HTTPException(status_code=404, detail="Variable not found") from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail="Variable not found") from e
    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{variable_id}", status_code=204, include_in_schema=False)
async def delete_variable(
    *,
    session: DbSession,
    variable_id: UUID,
    current_user: CurrentActiveUser,
) -> None:
    """Delete a variable.

    If the deleted variable is a model provider credential (e.g., OPENAI_API_KEY),
    all disabled models for that provider are automatically cleared.
    """
    variable_service = get_variable_service()
    try:
        # Fetch without user_id filter to support ownership check
        raw_stmt = select(Variable).where(Variable.id == variable_id)
        variable_to_delete = (await session.exec(raw_stmt)).first()
        if not variable_to_delete:
            raise HTTPException(status_code=404, detail="Variable not found")

        # Non-superusers may only delete their own variables.
        if not current_user.is_superuser and variable_to_delete.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="You do not have permission to delete this variable")

        # Reserved provider variables (not Ollama) can only be deleted by platform admins.
        if variable_to_delete.name in RESERVED_PUBLIC_VARS and not current_user.is_superuser:
            raise HTTPException(
                status_code=403, detail="Only platform admins can delete reserved model provider variables"
            )

        # Check if this variable is a model provider credential
        provider = get_provider_from_variable_name(variable_to_delete.name)
        owner_id = variable_to_delete.user_id

        # Delete the variable
        await variable_service.delete_variable_by_id(
            user_id=variable_to_delete.user_id, variable_id=variable_id, session=session
        )

        # If this was a provider credential, clean up disabled and enabled models for that provider
        if provider and isinstance(variable_service, DatabaseVariableService):
            await _cleanup_provider_models(variable_service, owner_id, provider, session)

    except Exception as e:
        if isinstance(e, HTTPException):
            raise
        raise HTTPException(status_code=500, detail=str(e)) from e


def _collect_candidate_variable_keys_from_flow_data(data: dict) -> set[str]:
    """Collect explicit global-variable keys from flow data."""
    candidate_keys: set[str] = set()

    for node in data.get("nodes", []):
        template = node.get("data", {}).get("node", {}).get("template", {})
        if not isinstance(template, dict):
            continue
        for field in template.values():
            if not isinstance(field, dict):
                continue
            if field.get("load_from_db") is True:
                var_name = field.get("value")
                normalized_var_name = var_name.strip() if isinstance(var_name, str) else None
                if normalized_var_name:
                    candidate_keys.add(normalized_var_name)

    return candidate_keys


def _validate_flow_or_422(*, version_id: UUID, data: object) -> dict:
    """Validate flow version data structure and raise HTTP 422 on malformed input."""
    if not (isinstance(data, dict) and "nodes" in data and isinstance(data["nodes"], list)):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Flow version {version_id} data must be a JSON object with a 'nodes' list containing node templates."
            ),
        )
    return data


@router.post("/detections", response_model=DetectVarsResponse, include_in_schema=False)
async def detect_env_vars(
    payload: DetectVarsRequest,
    session: DbSession,
    current_user: CurrentActiveUser,
):
    """Detect global variable references used by the given flow version IDs.

    Candidates are inferred only from ``load_from_db=True`` fields, where
    the field value is interpreted as the referenced global variable name.

    Returned values are cross-checked against the user's existing global
    variable names. This is a security guardrail: it prevents echoing arbitrary
    template values (including accidental secrets) and ensures results are
    actual stored global variables.
    """
    variable_service = get_variable_service()
    existing_variable_names = {
        name
        for name in await variable_service.list_variables(user_id=current_user.id, session=session)
        if isinstance(name, str) and name
    }

    candidate_keys: set[str] = set()
    versions_by_id = await get_flow_version_entries_by_ids(
        session,
        version_ids=payload.flow_version_ids,
        user_id=current_user.id,
    )

    for version_id in payload.flow_version_ids:
        version = versions_by_id.get(version_id)
        if version is None:
            raise HTTPException(status_code=404, detail=f"Flow version {version_id} not found")

        data = _validate_flow_or_422(version_id=version_id, data=version.data)
        candidate_keys.update(_collect_candidate_variable_keys_from_flow_data(data))

    return DetectVarsResponse(variables=sorted(existing_variable_names.intersection(candidate_keys)))
