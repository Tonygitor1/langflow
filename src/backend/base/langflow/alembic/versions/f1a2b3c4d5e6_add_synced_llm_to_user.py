"""Add synced_llm column to user table for LiteLLM user sync tracking.

Revision ID: f1a2b3c4d5e6
Revises: c4d5e6f7a8b9
Create Date: 2026-05-05 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"  # pragma: allowlist secret
down_revision: str | None = "c4d5e6f7a8b9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("user")]
    if "synced_llm" not in columns:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column("synced_llm", sa.Boolean(), nullable=False, server_default=sa.false())
            )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col["name"] for col in inspector.get_columns("user")]
    if "synced_llm" in columns:
        with op.batch_alter_table("user", schema=None) as batch_op:
            batch_op.drop_column("synced_llm")
