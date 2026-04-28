"""Add user_mcp_server table for external MCP server visibility.

Merge migration: runs after ALL Langflow v1.9.1 heads so there is exactly one
graph head (c4d5e6f7a8b9) and `alembic upgrade head` (singular) works.

When upgrading to a new Langflow version, update down_revision to the new set
of heads reported by `alembic heads`.

Langflow v1.9.1 heads:
  0e6138e7a0c2  add_ondelete_cascade_to_file_user_id_fk
  1cb603706752  modify_uniqueness_constraint_on_file
  d306e5c17c41  add_api_key_hash_column_to_apikey_table
  d37bc4322900  drop_single_constraint_on_files_name

Revision ID: b3c4d5e6f7a8
Revises: 0e6138e7a0c2, 1cb603706752, d306e5c17c41, d37bc4322900
Create Date: 2026-04-22 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = (
    "0e6138e7a0c2",  # add_ondelete_cascade_to_file_user_id_fk
    "1cb603706752",  # modify_uniqueness_constraint_on_file
    "d306e5c17c41",  # add_api_key_hash_column_to_apikey_table
    "d37bc4322900",  # drop_single_constraint_on_files_name
)
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
