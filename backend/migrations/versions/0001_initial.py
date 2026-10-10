"""Initial schema.

Revision ID: 0001
Revises:
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
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


def upgrade() -> None:
    op.create_table(
        "organizations", uid(), sa.Column("name", sa.String(200), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False), *stamps(),
    )
    op.create_table(
        "users", sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, index=True),
        sa.Column("name", sa.String(200)), sa.Column("phone", sa.String(32)), *stamps(),
    )
    op.create_table(
        "org_members", uid(),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("role", sa.String(16), nullable=False), *stamps(),
        sa.UniqueConstraint("org_id", "user_id"),
    )
    op.create_table(
        "locations", uid(),
        sa.Column("org_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("digest_hour", sa.Integer, nullable=False), *stamps(),
    )
    op.create_table(
        "integrations", uid(), loc_fk(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(200), index=True),
        sa.Column("access_token_enc", sa.Text), sa.Column("refresh_token_enc", sa.Text),
        ts("access_expires_at"),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("last_error", sa.Text),
        ts("last_synced_at"), sa.Column("cursor", sa.JSON, nullable=False), sa.Column("meta", sa.JSON, nullable=False),
        *stamps(),
        sa.UniqueConstraint("location_id", "provider"),
    )
    op.create_table(
        "items", uid(), loc_fk(),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("name", sa.String(300), nullable=False), sa.Column("category", sa.String(200)),
        sa.Column("price_cents", sa.BigInteger, nullable=False), sa.Column("cost_cents", sa.BigInteger),
        sa.Column("stock_qty", sa.Float), sa.Column("low_stock_threshold", sa.Float),
        sa.Column("unit", sa.String(32)), sa.Column("active", sa.Boolean, nullable=False), *stamps(),
        sa.UniqueConstraint("location_id", "provider", "external_id"),
    )
    op.create_table(
        "dining_tables", uid(), loc_fk(),
        sa.Column("name", sa.String(50), nullable=False), sa.Column("seats", sa.Integer, nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("current_order_id", sa.String(36)),
        *stamps(),
        sa.UniqueConstraint("location_id", "name"),
    )
    op.create_table(
        "orders", uid(), loc_fk(index=False),
        sa.Column("provider", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("total_cents", sa.BigInteger, nullable=False),
        sa.Column("tax_cents", sa.BigInteger, nullable=False),
        sa.Column("tip_cents", sa.BigInteger, nullable=False),
        sa.Column("table_name", sa.String(50)), sa.Column("employee_name", sa.String(200)),
        sa.Column("guests", sa.Integer), ts("opened_at", nullable=False), ts("closed_at"), *stamps(),
        sa.UniqueConstraint("location_id", "provider", "external_id"),
    )
    op.create_index("ix_orders_location_opened", "orders", ["location_id", "opened_at"])
    op.create_table(
        "order_lines", uid(),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("item_external_id", sa.String(100)), sa.Column("name", sa.String(300), nullable=False),
        sa.Column("category", sa.String(200)), sa.Column("qty", sa.Float, nullable=False),
        sa.Column("unit_price_cents", sa.BigInteger, nullable=False),
        sa.Column("total_cents", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "daily_sales",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("gross_cents", sa.BigInteger, nullable=False), sa.Column("tax_cents", sa.BigInteger, nullable=False),
        sa.Column("tip_cents", sa.BigInteger, nullable=False), sa.Column("orders_count", sa.Integer, nullable=False),
        sa.Column("guests", sa.Integer, nullable=False), sa.Column("by_hour", sa.JSON, nullable=False),
    )
    op.create_table(
        "item_sales_daily",
        sa.Column("location_id", sa.String(36), sa.ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("name", sa.String(300), primary_key=True),
        sa.Column("category", sa.String(200)), sa.Column("qty", sa.Float, nullable=False),
        sa.Column("revenue_cents", sa.BigInteger, nullable=False),
    )
    op.create_table(
        "reviews", uid(), loc_fk(),
        sa.Column("source", sa.String(32), nullable=False), sa.Column("external_id", sa.String(400), nullable=False),
        sa.Column("author", sa.String(200)), sa.Column("rating", sa.Integer, nullable=False),
        sa.Column("text", sa.Text), ts("posted_at", nullable=False), sa.Column("reply_text", sa.Text),
        ts("replied_at"), sa.Column("sentiment", sa.String(16)), sa.Column("topics", sa.JSON, nullable=False),
        sa.Column("urgent", sa.Boolean, nullable=False), sa.Column("suggested_reply", sa.Text), ts("triaged_at"),
        *stamps(),
        sa.UniqueConstraint("location_id", "source", "external_id"),
    )
    op.create_table(
        "alert_rules", uid(), loc_fk(),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("params", sa.JSON, nullable=False),
        sa.Column("channels", sa.JSON, nullable=False), sa.Column("enabled", sa.Boolean, nullable=False), *stamps(),
    )
    op.create_table(
        "alerts", uid(), loc_fk(),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("title", sa.String(300), nullable=False),
        sa.Column("body", sa.Text, nullable=False), sa.Column("data", sa.JSON, nullable=False),
        sa.Column("channels", sa.JSON, nullable=False), sa.Column("dedupe_key", sa.String(200), nullable=False),
        ts("created_at"), ts("read_at"),
        sa.UniqueConstraint("location_id", "dedupe_key"),
    )
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.create_table(
        "notification_deliveries", uid(),
        sa.Column("alert_id", sa.String(36), sa.ForeignKey("alerts.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("user_id", sa.String(64), nullable=False), sa.Column("channel", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("error", sa.Text), ts("created_at"),
    )
    op.create_table(
        "push_subscriptions", uid(),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("kind", sa.String(16), nullable=False), sa.Column("token", sa.Text, nullable=False), *stamps(),
        sa.UniqueConstraint("user_id", "token"),
    )
    op.create_table(
        "user_channel_prefs",
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("push", sa.Boolean, nullable=False), sa.Column("email", sa.Boolean, nullable=False),
        sa.Column("whatsapp", sa.Boolean, nullable=False), sa.Column("sms", sa.Boolean, nullable=False),
        *stamps(),
    )
    op.create_table(
        "conversations", uid(), loc_fk(),
        sa.Column("user_id", sa.String(64), nullable=False, index=True),
        sa.Column("channel", sa.String(16), nullable=False), sa.Column("title", sa.String(200)), *stamps(),
    )
    op.create_table(
        "messages", uid(),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("role", sa.String(16), nullable=False), sa.Column("content", sa.JSON, nullable=False),
        ts("created_at"),
    )
    op.create_table(
        "ai_usage", uid(),
        sa.Column("location_id", sa.String(36), nullable=False), sa.Column("purpose", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=False), sa.Column("output_tokens", sa.Integer, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, nullable=False),
        sa.Column("cost_usd", sa.Float, nullable=False), ts("created_at"),
    )
    op.create_index("ix_ai_usage_location_created", "ai_usage", ["location_id", "created_at"])
    op.create_table(
        "jobs", uid(),
        sa.Column("kind", sa.String(64), nullable=False), sa.Column("payload", sa.JSON, nullable=False),
        sa.Column("dedupe_key", sa.String(200), index=True),
        sa.Column("status", sa.String(16), nullable=False), sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False), ts("run_at", nullable=False), ts("locked_at"),
        sa.Column("last_error", sa.Text), ts("created_at"), ts("finished_at"),
    )
    op.create_index("ix_jobs_ready", "jobs", ["status", "run_at"])
    op.create_table(
        "webhook_events", uid(),
        sa.Column("provider", sa.String(16), nullable=False), sa.Column("body", sa.JSON, nullable=False),
        ts("received_at"), ts("processed_at"),
    )


def downgrade() -> None:
    for table in (
        "webhook_events", "jobs", "ai_usage", "messages", "conversations", "user_channel_prefs",
        "push_subscriptions", "notification_deliveries", "alerts", "alert_rules", "reviews", "item_sales_daily",
        "daily_sales", "order_lines", "orders", "dining_tables", "items", "integrations", "locations",
        "org_members", "users", "organizations",
    ):
        op.drop_table(table)
