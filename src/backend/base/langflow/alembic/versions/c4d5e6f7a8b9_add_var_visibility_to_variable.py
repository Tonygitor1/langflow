"""Add var_visibility column to variable table.

Adds a visibility field to the variable table so that platform_admin users can
mark global variables as public (visible to all users) or private (owner only).
Defaults to 'private' to preserve existing behaviour.

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-04-23 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: str | Sequence[str] | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add var_visibility column to variable table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    table_names = inspector.get_table_names()
    if "variable" not in table_names:
        return

    existing_columns = [col["name"] for col in inspector.get_columns("variable")]
    if "var_visibility" not in existing_columns:
        with op.batch_alter_table("variable", schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "var_visibility",
                    sa.String(),
                    nullable=False,
                    server_default="private",
                )
            )


def downgrade() -> None:
    """Drop var_visibility column from variable table (idempotent)."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "variable" not in inspector.get_table_names():
        return

    existing_columns = [col["name"] for col in inspector.get_columns("variable")]
    if "var_visibility" in existing_columns:
        with op.batch_alter_table("variable", schema=None) as batch_op:
            batch_op.drop_column("var_visibility")
