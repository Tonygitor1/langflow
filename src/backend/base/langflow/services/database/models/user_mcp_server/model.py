from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class UserMCPServer(SQLModel, table=True):  # type: ignore[call-arg]
    """Stores per-user visibility metadata for external MCP server configurations.

    The actual server config (command, url, env, …) is stored in a per-user
    JSON file managed by api/v2/mcp.py.  This table holds only the visibility
    flag so it can be queried efficiently across users.
    """

    __tablename__ = "user_mcp_server"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_user_mcp_server_user_name"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="user.id", index=True, nullable=False)
    name: str = Field(nullable=False)
    mcp_visibility: str = Field(default="private", nullable=False)
