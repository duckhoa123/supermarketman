"""Inventory, the rest of Phase 1: par and reorder point, barcodes and count order, prep items and unit
conversions, count approval, supplier credits, plate cost history and the audit of recipe and par changes.

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

ITEM_COLUMNS = [
    ("par_level", sa.Float), ("barcode", sa.String(64)), ("shelf_order", sa.Integer), ("default_yield_pct", sa.Float),
    ("conversions", sa.JSON), ("batch_qty", sa.Float), ("batch_unit", sa.String(32)),
    ("target_food_cost_pct", sa.Float), ("q_factor_pct", sa.Float), ("recipe_notes", sa.Text),
]
CREDIT_COLUMNS = [
    ("credit_status", sa.String(16)), ("credit_cents", sa.BigInteger), ("credit_note", sa.Text),
    ("credit_resolved_by", sa.String(64)), ("credit_resolved_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, kind in ITEM_COLUMNS:
        op.add_column("items", sa.Column(name, kind))
    op.add_column("locations", sa.Column("target_beverage_cost_pct", sa.Float, nullable=False, server_default="22"))
    op.add_column("locations", sa.Column("cost_method", sa.String(16), nullable=False, server_default="average"))
    for name, kind in CREDIT_COLUMNS:
        op.add_column("received_lines", sa.Column(name, kind))
    op.add_column("stock_counts", sa.Column("status", sa.String(16), nullable=False, server_default="approved"))
    op.add_column("stock_counts", sa.Column("started_at", sa.DateTime(timezone=True)))
    op.add_column("stock_counts", sa.Column("approved_by", sa.String(64)))
    op.add_column("stock_counts", sa.Column("approved_at", sa.DateTime(timezone=True)))

    op.create_table(
        "plate_cost_history",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("menu_item_id", sa.String(36), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plate_cost_cents", sa.BigInteger, nullable=False),
        sa.Column("price_cents", sa.BigInteger, nullable=False),
        sa.Column("reason", sa.String(200)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_plate_cost_history_location_id", "plate_cost_history", ["location_id"])
    op.create_index("ix_plate_cost_history_item_created", "plate_cost_history", ["menu_item_id", "created_at"])

    op.create_table(
        "inventory_audit",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("item_name", sa.String(300), nullable=False),
        sa.Column("field", sa.String(40), nullable=False),
        sa.Column("old", sa.Text),
        sa.Column("new", sa.Text),
        sa.Column("user_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_audit_location_created", "inventory_audit", ["location_id", "created_at"])
    op.create_index("ix_inventory_audit_item_id", "inventory_audit", ["item_id"])


def downgrade() -> None:
    op.drop_table("inventory_audit")
    op.drop_table("plate_cost_history")
    for name in ("approved_at", "approved_by", "started_at", "status"):
        op.drop_column("stock_counts", name)
    for name, _ in reversed(CREDIT_COLUMNS):
        op.drop_column("received_lines", name)
    op.drop_column("locations", "cost_method")
    op.drop_column("locations", "target_beverage_cost_pct")
    for name, _ in reversed(ITEM_COLUMNS):
        op.drop_column("items", name)
