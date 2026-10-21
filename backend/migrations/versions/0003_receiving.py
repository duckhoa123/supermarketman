"""Receiving: scanned supplier invoices, the receiving log and price history.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "invoice_scans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("uploaded_by", sa.String(64)), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("file_name", sa.String(300)), sa.Column("content_type", sa.String(100)),
        sa.Column("file_bytes", sa.LargeBinary),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id", ondelete="SET NULL")),
        sa.Column("supplier_name", sa.String(200)), sa.Column("invoice_number", sa.String(100)),
        sa.Column("invoice_date", sa.Date), sa.Column("subtotal_cents", sa.BigInteger),
        sa.Column("tax_cents", sa.BigInteger), sa.Column("total_cents", sa.BigInteger),
        sa.Column("lines", sa.JSON, nullable=False), sa.Column("warnings", sa.JSON, nullable=False),
        sa.Column("error", sa.Text), sa.Column("purchase_order_id", sa.String(36)),
        sa.Column("confirmed_by", sa.String(64)), ts("confirmed_at"), ts("created_at"), ts("updated_at"),
    )
    op.create_index("ix_invoice_scans_location_created", "invoice_scans", ["location_id", "created_at"])
    op.create_table(
        "received_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("scan_id", sa.String(36), sa.ForeignKey("invoice_scans.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id", ondelete="SET NULL")),
        sa.Column("supplier_name", sa.String(200)), sa.Column("invoice_number", sa.String(100)),
        sa.Column("received_on", sa.Date, nullable=False), sa.Column("description", sa.String(300), nullable=False),
        sa.Column("sku", sa.String(100)), sa.Column("product_key", sa.String(400), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("items.id", ondelete="SET NULL"), index=True),
        sa.Column("qty", sa.Float, nullable=False), sa.Column("unit", sa.String(32)),
        sa.Column("unit_price_cents", sa.BigInteger, nullable=False),
        sa.Column("line_total_cents", sa.BigInteger, nullable=False),
        sa.Column("pack_size", sa.Float, nullable=False), sa.Column("stock_added", sa.Float),
        sa.Column("previous_unit_price_cents", sa.BigInteger), sa.Column("price_change_pct", sa.Float),
        ts("created_at"),
    )
    op.create_index("ix_received_lines_location_day", "received_lines", ["location_id", "received_on"])
    op.create_index("ix_received_lines_location_product", "received_lines", ["location_id", "product_key"])


def downgrade() -> None:
    op.drop_table("received_lines")
    op.drop_table("invoice_scans")
