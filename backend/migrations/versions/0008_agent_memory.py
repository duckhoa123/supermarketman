"""Agent memory: things a person asked an agent to remember, which they can see and forget under You.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(64), nullable=False),
        sa.Column("agent", sa.String(24), nullable=False),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE")),
        sa.Column("text", sa.String(300), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agent_memories_owner", "agent_memories", ["user_id", "agent", "location_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_memories_owner", table_name="agent_memories")
    op.drop_table("agent_memories")
