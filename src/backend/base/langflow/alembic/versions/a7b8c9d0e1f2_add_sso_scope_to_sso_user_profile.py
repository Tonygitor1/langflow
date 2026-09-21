"""add sso_scope to sso_user_profile

Revision ID: a7b8c9d0e1f2
Revises: f1a2b3c4d5e6
Create Date: 2026-09-20

Phase: EXPAND

A provider identity now has one profile row per profile it acts under: "" for
personal, or the organization id. Existing rows are personal.
"""
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from alembic import op

from langflow.utils import migration

# revision identifiers, used by Alembic.
revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    if not migration.table_exists("sso_user_profile", conn):
        return
    if not migration.column_exists("sso_user_profile", "sso_scope", conn):
        with op.batch_alter_table("sso_user_profile", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "sso_scope",
                    sqlmodel.sql.sqltypes.AutoString(),
                    nullable=False,
                    server_default="",
                )
            )
    with op.batch_alter_table("sso_user_profile", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("uq_sso_user_profile_provider_user"))
        batch_op.create_index(
            batch_op.f("uq_sso_user_profile_provider_user"),
            ["sso_provider", "sso_user_id", "sso_scope"],
            unique=True,
        )


def downgrade() -> None:
    conn = op.get_bind()
    if not migration.table_exists("sso_user_profile", conn):
        return
    with op.batch_alter_table("sso_user_profile", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("uq_sso_user_profile_provider_user"))
        batch_op.create_index(
            batch_op.f("uq_sso_user_profile_provider_user"),
            ["sso_provider", "sso_user_id"],
            unique=True,
        )
        batch_op.drop_column("sso_scope")
