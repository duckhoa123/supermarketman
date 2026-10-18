"""Ecosystem foundation: agent avatars, purchasing, labour, guests, bookings, weather,
customer ordering and nutrition, and the in-house delivery fleet.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def stamps() -> list[sa.Column]:
    return [ts("created_at"), ts("updated_at")]


def uid(name: str = "id") -> sa.Column:
    return sa.Column(name, sa.String(36), primary_key=True)


def loc_fk(index: bool = True) -> sa.Column:
    return sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"),
                     nullable=False, index=index)


def flag(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean, nullable=False, server_default=sa.false())


def json_col(name: str, empty: str) -> sa.Column:
    return sa.Column(name, sa.JSON, nullable=False, server_default=sa.text(f"'{empty}'"))


def location_columns() -> list[sa.Column]:
    return [
    sa.Column("address", sa.String(300)), sa.Column("lat", sa.Float), sa.Column("lng", sa.Float),
    sa.Column("phone", sa.String(32)), sa.Column("cuisine", sa.String(100)), json_col("opening_hours", "{}"),
    flag("listed"), flag("accepts_orders"), flag("accepts_delivery"), flag("accepts_bookings"),
    sa.Column("tax_rate_bp", sa.Integer, nullable=False, server_default="500"),
]

def item_columns() -> list[sa.Column]:
    return [
    sa.Column("reorder_qty", sa.Float), sa.Column("description", sa.Text), sa.Column("calories", sa.Float),
    sa.Column("protein_g", sa.Float), sa.Column("carbs_g", sa.Float), sa.Column("fat_g", sa.Float),
    json_col("tags", "[]"), sa.Column("orderable", sa.Boolean, nullable=False, server_default=sa.true()),
]


def upgrade() -> None:
    for col in location_columns():
        op.add_column("locations", col)

    op.create_table(
        "agent_profiles",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("agent", sa.String(24), primary_key=True),
        sa.Column("display_name", sa.String(60), nullable=False), sa.Column("avatar", sa.JSON, nullable=False),
        sa.Column("tone", sa.String(24), nullable=False), *stamps(),
    )
    op.create_table(
        "suppliers", uid(), loc_fk(),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("email", sa.String(320)),
        sa.Column("phone", sa.String(32)), sa.Column("notes", sa.Text), *stamps(),
    )
    for col in item_columns():
        op.add_column("items", col)
    op.add_column("items", sa.Column("supplier_id", sa.String(36),
                                     sa.ForeignKey("suppliers.id", ondelete="SET NULL", name="fk_items_supplier")))

    op.create_table(
        "purchase_orders", uid(), loc_fk(),
        sa.Column("supplier_id", sa.String(36), sa.ForeignKey("suppliers.id", ondelete="SET NULL"), index=True),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("lines", sa.JSON, nullable=False),
        sa.Column("note", sa.Text), sa.Column("approved_by", sa.String(64)), ts("approved_at"), ts("sent_at"),
        sa.Column("send_error", sa.Text), *stamps(),
    )
    op.create_table(
        "staff_shifts", uid(), loc_fk(index=False),
        sa.Column("staff_name", sa.String(200), nullable=False), sa.Column("role", sa.String(32)),
        ts("starts_at", nullable=False), ts("ends_at", nullable=False),
        sa.Column("hourly_rate_cents", sa.Integer, nullable=False), sa.Column("source", sa.String(16), nullable=False),
        *stamps(),
    )
    op.create_index("ix_staff_shifts_location_start", "staff_shifts", ["location_id", "starts_at"])
    op.create_table(
        "guests", uid(), loc_fk(),
        sa.Column("user_id", sa.String(64), index=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("email", sa.String(320)), sa.Column("phone", sa.String(32)),
        sa.Column("marketing_consent", sa.Boolean, nullable=False), ts("consent_at"),
        sa.Column("visits", sa.Integer, nullable=False), sa.Column("total_spend_cents", sa.BigInteger, nullable=False),
        ts("last_visit_at"), sa.Column("notes", sa.Text), sa.Column("tags", sa.JSON, nullable=False), *stamps(),
    )
    op.create_table(
        "bookings", uid(), loc_fk(index=False),
        sa.Column("guest_id", sa.String(36), sa.ForeignKey("guests.id", ondelete="SET NULL")),
        sa.Column("customer_user_id", sa.String(64), index=True), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("phone", sa.String(32)), sa.Column("party_size", sa.Integer, nullable=False),
        ts("starts_at", nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False), sa.Column("table_id", sa.String(36)),
        sa.Column("notes", sa.Text), *stamps(),
    )
    op.create_index("ix_bookings_location_start", "bookings", ["location_id", "starts_at"])
    op.create_table(
        "weather_daily",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True), sa.Column("temp_max_c", sa.Float),
        sa.Column("temp_min_c", sa.Float), sa.Column("precip_mm", sa.Float), sa.Column("weather_code", sa.Integer),
        ts("fetched_at"),
    )

    op.create_table(
        "customer_profiles",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("home_address", sa.String(300)), sa.Column("home_lat", sa.Float), sa.Column("home_lng", sa.Float),
        sa.Column("diet_tags", sa.JSON, nullable=False), sa.Column("calories_goal", sa.Float),
        sa.Column("protein_goal_g", sa.Float), sa.Column("carbs_goal_g", sa.Float), sa.Column("fat_goal_g", sa.Float),
        *stamps(),
    )
    op.create_table(
        "customer_orders", uid(), loc_fk(index=False),
        sa.Column("customer_user_id", sa.String(64), nullable=False),
        sa.Column("fulfilment", sa.String(16), nullable=False), sa.Column("status", sa.String(20), nullable=False),
        sa.Column("lines", sa.JSON, nullable=False),
        *[sa.Column(c, sa.BigInteger, nullable=False) for c in (
            "subtotal_cents", "tax_cents", "delivery_fee_cents", "tip_cents", "total_cents", "restaurant_fee_cents")],
        sa.Column("payment_status", sa.String(20), nullable=False), sa.Column("payment_ref", sa.String(200)),
        sa.Column("delivery_address", sa.String(300)), sa.Column("dropoff_lat", sa.Float),
        sa.Column("dropoff_lng", sa.Float), sa.Column("notes", sa.Text),
        sa.Column("share_contact", sa.Boolean, nullable=False),
        ts("placed_at"), ts("ready_at"), ts("completed_at"), ts("eta_at"), *stamps(),
    )
    op.create_index("ix_customer_orders_location_placed", "customer_orders", ["location_id", "placed_at"])
    op.create_index("ix_customer_orders_user_placed", "customer_orders", ["customer_user_id", "placed_at"])
    op.create_table(
        "nutrition_log", uid(),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("day", sa.Date, nullable=False), sa.Column("name", sa.String(300), nullable=False),
        sa.Column("calories", sa.Float), sa.Column("protein_g", sa.Float), sa.Column("carbs_g", sa.Float),
        sa.Column("fat_g", sa.Float), sa.Column("source", sa.String(16), nullable=False),
        sa.Column("customer_order_id", sa.String(36)), ts("created_at"),
    )
    op.create_index("ix_nutrition_log_user_day", "nutrition_log", ["user_id", "day"])

    op.create_table(
        "drivers", uid(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="SET NULL"), unique=True),
        sa.Column("name", sa.String(200), nullable=False), sa.Column("phone", sa.String(32)),
        sa.Column("role", sa.String(16), nullable=False), sa.Column("vehicle", sa.String(16), nullable=False),
        sa.Column("hourly_wage_cents", sa.Integer, nullable=False), sa.Column("weekly_goal_cents", sa.Integer),
        sa.Column("active", sa.Boolean, nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_lat", sa.Float), sa.Column("last_lng", sa.Float), ts("last_seen_at"), *stamps(),
    )
    op.create_table(
        "driver_shifts", uid(),
        sa.Column("driver_id", sa.String(36), sa.ForeignKey("drivers.id", ondelete="CASCADE"), nullable=False),
        ts("starts_at", nullable=False), ts("ends_at"), ts("created_at"),
    )
    op.create_index("ix_driver_shifts_driver_start", "driver_shifts", ["driver_id", "starts_at"])
    op.create_table(
        "deliveries", uid(),
        sa.Column("customer_order_id", sa.String(36), sa.ForeignKey("customer_orders.id", ondelete="CASCADE"),
                  nullable=False, unique=True),
        loc_fk(),
        sa.Column("driver_id", sa.String(36), sa.ForeignKey("drivers.id", ondelete="SET NULL"), index=True),
        sa.Column("status", sa.String(16), nullable=False, index=True),
        *[sa.Column(c, sa.Float, nullable=False) for c in (
            "pickup_lat", "pickup_lng", "dropoff_lat", "dropoff_lng", "distance_km")],
        ts("assigned_at"), ts("picked_up_at"), ts("delivered_at"), ts("promised_at"), ts("eta_at"),
        sa.Column("tip_cents", sa.BigInteger, nullable=False), *stamps(),
    )

    # Customers and drivers chat and get notified without a restaurant.
    op.alter_column("conversations", "location_id", existing_type=sa.String(36), nullable=True)
    op.add_column("conversations", sa.Column("agent", sa.String(24), nullable=False,
                                             server_default="restaurant_manager"))
    op.alter_column("alerts", "location_id", existing_type=sa.String(36), nullable=True)
    op.add_column("alerts", sa.Column("user_id", sa.String(64)))
    op.create_index("ix_alerts_user_id", "alerts", ["user_id"])
    op.alter_column("ai_usage", "location_id", existing_type=sa.String(36), nullable=True)
    op.add_column("ai_usage", sa.Column("user_id", sa.String(64)))
    op.add_column("ai_usage", sa.Column("agent", sa.String(24)))
    op.create_index("ix_ai_usage_user_created", "ai_usage", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_user_created", "ai_usage")
    op.drop_column("ai_usage", "agent")
    op.drop_column("ai_usage", "user_id")
    op.execute("DELETE FROM ai_usage WHERE location_id IS NULL")
    op.alter_column("ai_usage", "location_id", existing_type=sa.String(36), nullable=False)
    op.drop_index("ix_alerts_user_id", "alerts")
    op.drop_column("alerts", "user_id")
    op.execute("DELETE FROM alerts WHERE location_id IS NULL")
    op.alter_column("alerts", "location_id", existing_type=sa.String(36), nullable=False)
    op.drop_column("conversations", "agent")
    op.execute("DELETE FROM conversations WHERE location_id IS NULL")
    op.alter_column("conversations", "location_id", existing_type=sa.String(36), nullable=False)

    for table in (
        "deliveries", "driver_shifts", "drivers", "nutrition_log", "customer_orders", "customer_profiles",
        "weather_daily", "bookings", "guests", "staff_shifts", "purchase_orders",
    ):
        op.drop_table(table)
    op.drop_constraint("fk_items_supplier", "items", type_="foreignkey")
    op.drop_column("items", "supplier_id")
    for col in item_columns():
        op.drop_column("items", col.name)
    op.drop_table("suppliers")
    op.drop_table("agent_profiles")
    for col in location_columns():
        op.drop_column("locations", col.name)
