"""Supplier order messages: a per-restaurant template, and each order's delivery date and sent message.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("locations", sa.Column("order_template", sa.Text))
    op.add_column("purchase_orders", sa.Column("delivery_date", sa.Date))
    op.add_column("purchase_orders", sa.Column("message", sa.Text))
    op.add_column("purchase_orders", sa.Column("sent_via", sa.String(16)))


def downgrade() -> None:
    op.drop_column("purchase_orders", "sent_via")
    op.drop_column("purchase_orders", "message")
    op.drop_column("purchase_orders", "delivery_date")
    op.drop_column("locations", "order_template")
