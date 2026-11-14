"""Inventory: storage areas, the stock ledger, physical counts, recipes, and receiving issues.

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "storage_areas",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_storage_areas_location_id", "storage_areas", ["location_id"])

    op.add_column("items", sa.Column("storage_area_id", sa.String(36),
                                     sa.ForeignKey("storage_areas.id", ondelete="SET NULL")))
    op.add_column("items", sa.Column("avg_cost", sa.Float))
    op.add_column("items", sa.Column("q_factor_cents", sa.BigInteger))
    op.add_column("items", sa.Column("recipe_since", sa.Date))
    op.execute("UPDATE items SET avg_cost = cost_cents WHERE cost_cents IS NOT NULL")
    op.add_column("locations", sa.Column("target_food_cost_pct", sa.Float, nullable=False, server_default="30"))
    op.add_column("received_lines", sa.Column("received_qty", sa.Float))
    op.add_column("received_lines", sa.Column("issue", sa.String(16)))

    op.create_table(
        "stock_movements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("item_id", sa.String(36), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("item_name", sa.String(300), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("qty_delta", sa.Float, nullable=False),
        sa.Column("qty_after", sa.Float, nullable=False),
        sa.Column("unit", sa.String(32)),
        sa.Column("unit_cost", sa.Float),
        sa.Column("value_cents", sa.BigInteger, nullable=False),
        sa.Column("source", sa.String(100)),
        sa.Column("reason", sa.String(32)),
        sa.Column("note", sa.Text),
        sa.Column("user_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stock_movements_location_created", "stock_movements", ["location_id", "created_at"])
    op.create_index("ix_stock_movements_item_created", "stock_movements", ["item_id", "created_at"])
    op.create_index("ix_stock_movements_location_source", "stock_movements", ["location_id", "source"])

    op.create_table(
        "stock_counts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("storage_area_id", sa.String(36), sa.ForeignKey("storage_areas.id", ondelete="SET NULL")),
        sa.Column("area_name", sa.String(100)),
        sa.Column("client_id", sa.String(64), nullable=False),
        sa.Column("lines", sa.JSON, nullable=False),
        sa.Column("variance_cents", sa.BigInteger, nullable=False),
        sa.Column("counted_by", sa.String(64)),
        sa.Column("counted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("location_id", "client_id"),
    )
    op.create_index("ix_stock_counts_location_id", "stock_counts", ["location_id"])

    op.create_table(
        "recipe_lines",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("menu_item_id", sa.String(36), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ingredient_id", sa.String(36), sa.ForeignKey("items.id", ondelete="CASCADE"), nullable=False),
        sa.Column("qty", sa.Float, nullable=False),
        sa.Column("unit", sa.String(32)),
        sa.Column("yield_pct", sa.Float, nullable=False, server_default="100"),
        sa.Column("sort", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_recipe_lines_location_id", "recipe_lines", ["location_id"])
    op.create_index("ix_recipe_lines_menu_item_id", "recipe_lines", ["menu_item_id"])


def downgrade() -> None:
    op.drop_table("recipe_lines")
    op.drop_table("stock_counts")
    op.drop_table("stock_movements")
    op.drop_column("received_lines", "issue")
    op.drop_column("received_lines", "received_qty")
    op.drop_column("locations", "target_food_cost_pct")
    op.drop_column("items", "recipe_since")
    op.drop_column("items", "q_factor_cents")
    op.drop_column("items", "avg_cost")
    op.drop_column("items", "storage_area_id")
    op.drop_table("storage_areas")
