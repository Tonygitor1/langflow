"""Bootstrap tasks that run once at startup to seed required platform state.

Add new BootstrapVariable entries to PLATFORM_VARIABLES to seed additional
global variables from the environment on first run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from lfx.log.logger import logger
from sqlmodel import select

from langflow.services.database.models.user.model import User
from langflow.services.database.models.variable.model import Variable
from langflow.services.deps import get_variable_service, session_scope
from langflow.services.variable.constants import GENERIC_TYPE


@dataclass
class BootstrapVariable:
    name: str
    env_var: str
    type: str = GENERIC_TYPE
    visibility: str = "public"


# Variables seeded from the environment on first run.
# They are only created if they don't already exist (idempotent).
PLATFORM_VARIABLES: list[BootstrapVariable] = [
    BootstrapVariable(
        name="LITELLM_URL",
        env_var="LITELLM_URL",
        type=GENERIC_TYPE,
        visibility="public",
    ),
]


async def bootstrap_platform_variables() -> None:
    """Seed PLATFORM_VARIABLES into the superuser's global variables.

    Only creates variables that are missing; existing ones are left untouched.
    Skips any entry whose environment variable is unset or empty.
    """
    async with session_scope() as session:
        superuser_stmt = select(User).where(User.is_superuser == True)  # noqa: E712
        superuser = (await session.exec(superuser_stmt)).first()
        if not superuser:
            await logger.awarning("[bootstrap] No superuser found — skipping platform variable bootstrap.")
            return

        variable_service = get_variable_service()
        existing_names = set(await variable_service.list_variables(user_id=superuser.id, session=session))

        for spec in PLATFORM_VARIABLES:
            if spec.name in existing_names:
                await logger.adebug(f"[bootstrap] Variable '{spec.name}' already exists — skipping.")
                continue

            value = os.environ.get(spec.env_var, "").strip()
            if not value:
                await logger.adebug(
                    f"[bootstrap] Env var '{spec.env_var}' is not set — skipping variable '{spec.name}'."
                )
                continue

            db_var = await variable_service.create_variable(
                user_id=superuser.id,
                name=spec.name,
                value=value,
                type_=spec.type,
                session=session,
            )

            if db_var.var_visibility != spec.visibility:
                db_var.var_visibility = spec.visibility
                session.add(db_var)
                await session.flush()
                await session.refresh(db_var)

            await logger.ainfo(
                f"[bootstrap] Created variable '{spec.name}' (type={spec.type}, visibility={spec.visibility})."
            )
