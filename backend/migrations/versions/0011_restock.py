"""Restock alerts: when each supplier delivers and when to order, how long an item takes to be ready after it
arrives, and the safety margin (2 days by default).

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("suppliers", sa.Column("delivery_days", sa.JSON))
    op.add_column("suppliers", sa.Column("order_cutoff", sa.String(5)))
    op.add_column("suppliers", sa.Column("order_lead_days", sa.Integer, nullable=False, server_default="1"))
    op.add_column("items", sa.Column("ready_days", sa.Integer))
    op.add_column("items", sa.Column("safety_days", sa.Float))
    op.add_column("locations", sa.Column("safety_days", sa.Float, nullable=False, server_default="2"))


def downgrade() -> None:
    op.drop_column("locations", "safety_days")
    op.drop_column("items", "safety_days")
    op.drop_column("items", "ready_days")
    op.drop_column("suppliers", "order_lead_days")
    op.drop_column("suppliers", "order_cutoff")
    op.drop_column("suppliers", "delivery_days")
