"""Add user_mcp_server table for external MCP server visibility.

This is a merge migration: it runs after ALL current Langflow heads so that our
custom table is always created last.  If you upgrade Langflow to a newer version
and new migration heads appear, regenerate this migration (or update
down_revision to the new heads tuple) so it continues to be last.

Revision ID: b3c4d5e6f7a8
Revises: 79e675cb6752, d306e5c17c41
Create Date: 2026-04-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
# down_revision is a tuple → merge migration: runs after BOTH Langflow heads.
# Update this tuple whenever Langflow is upgraded and adds new migration heads.
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = ("79e675cb6752", "d306e5c17c41")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create user_mcp_server table and clean up any leftover folder.mcp_visibility column."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    table_names = inspector.get_table_names()

    # Create user_mcp_server table if it does not already exist.
    if "user_mcp_server" not in table_names:
        op.create_table(
            "user_mcp_server",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("user.id"), nullable=False, index=True),
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("mcp_visibility", sa.String(), nullable=False, server_default="private"),
            sa.UniqueConstraint("user_id", "name", name="uq_user_mcp_server_user_name"),
        )

    # Drop folder.mcp_visibility if it exists — it was added by an older version of this
    # migration that put visibility on the Folder model (since reverted).
    if "folder" in table_names:
        folder_columns = [col["name"] for col in inspector.get_columns("folder")]
        if "mcp_visibility" in folder_columns:
            with op.batch_alter_table("folder", schema=None) as batch_op:
                batch_op.drop_column("mcp_visibility")


def downgrade() -> None:
    """Drop user_mcp_server table if it exists."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if "user_mcp_server" in inspector.get_table_names():
        op.drop_table("user_mcp_server")
