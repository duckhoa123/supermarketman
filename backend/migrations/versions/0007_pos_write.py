"""Customer-app orders sent to the POS: the POS's order id, or why sending failed.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("customer_orders", sa.Column("pos_order_id", sa.String(100)))
    op.add_column("customer_orders", sa.Column("pos_error", sa.Text))


def downgrade() -> None:
    op.drop_column("customer_orders", "pos_error")
    op.drop_column("customer_orders", "pos_order_id")
