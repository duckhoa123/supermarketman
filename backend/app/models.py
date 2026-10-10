"""Database models.

Conventions:
- Primary keys are UUID strings (portable between Postgres and SQLite for tests).
- Every business table has `location_id`; queries must always filter on it (see app.tenancy).
- Money is integer cents in the location's currency. Timestamps are timezone-aware UTC.
"""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# --- Accounts -------------------------------------------------------------------------------------


class Organization(TimestampMixin, Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(32), default="trial")

    locations: Mapped[list["Location"]] = relationship(back_populates="organization")


class User(TimestampMixin, Base):
    """Mirror of the Supabase auth user; `id` is the Supabase user id (JWT `sub`)."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True)
    name: Mapped[str | None] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))  # E.164, for WhatsApp/SMS alerts


class OrgMember(TimestampMixin, Base):
    __tablename__ = "org_members"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16), default="owner")  # owner | manager | staff


class Location(TimestampMixin, Base):
    """One restaurant."""

    __tablename__ = "locations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Toronto")
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    digest_hour: Mapped[int] = mapped_column(Integer, default=8)  # local hour for the daily digest

    organization: Mapped[Organization] = relationship(back_populates="locations")


# --- Integrations ---------------------------------------------------------------------------------


class Integration(TimestampMixin, Base):
    """A connected POS (clover, square) or review source (google) for a location."""

    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("location_id", "provider"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(16))  # clover | square | google
    external_id: Mapped[str | None] = mapped_column(String(200), index=True)  # merchant id / GBP location
    access_token_enc: Mapped[str | None] = mapped_column(Text)
    refresh_token_enc: Mapped[str | None] = mapped_column(Text)
    access_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | error | disconnected
    last_error: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cursor: Mapped[dict] = mapped_column(JSON, default=dict)  # provider-specific sync state
    meta: Mapped[dict] = mapped_column(JSON, default=dict)


# --- POS data -------------------------------------------------------------------------------------


class Item(TimestampMixin, Base):
    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("location_id", "provider", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(200))
    price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    cost_cents: Mapped[int | None] = mapped_column(BigInteger)
    stock_qty: Mapped[float | None] = mapped_column(Float)
    low_stock_threshold: Mapped[float | None] = mapped_column(Float)
    unit: Mapped[str | None] = mapped_column(String(32))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DiningTable(TimestampMixin, Base):
    __tablename__ = "dining_tables"
    __table_args__ = (UniqueConstraint("location_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(50))
    seats: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(16), default="free")  # free | occupied | reserved | cleaning
    current_order_id: Mapped[str | None] = mapped_column(String(36))


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("location_id", "provider", "external_id"),
        Index("ix_orders_location_opened", "location_id", "opened_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(16))
    external_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | paid | refunded | void
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tip_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    table_name: Mapped[str | None] = mapped_column(String(50))
    employee_name: Mapped[str | None] = mapped_column(String(200))
    guests: Mapped[int | None] = mapped_column(Integer)
    opened_at: Mapped[datetime] = mapped_column(UTCDateTime())
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="selectin"
    )


class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    item_external_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(300))
    category: Mapped[str | None] = mapped_column(String(200))
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0)

    order: Mapped[Order] = relationship(back_populates="lines")


class DailySales(Base):
    """Roll-up per location per local day; rebuilt by services.sync.rebuild_rollups."""

    __tablename__ = "daily_sales"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    gross_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tip_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    orders_count: Mapped[int] = mapped_column(Integer, default=0)
    guests: Mapped[int] = mapped_column(Integer, default=0)
    by_hour: Mapped[dict] = mapped_column(JSON, default=dict)  # {"12": cents, ...} local hour


class ItemSalesDaily(Base):
    __tablename__ = "item_sales_daily"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str] = mapped_column(String(300), primary_key=True)
    category: Mapped[str | None] = mapped_column(String(200))
    qty: Mapped[float] = mapped_column(Float, default=0)
    revenue_cents: Mapped[int] = mapped_column(BigInteger, default=0)


# --- Reviews --------------------------------------------------------------------------------------


class Review(TimestampMixin, Base):
    __tablename__ = "reviews"
    __table_args__ = (UniqueConstraint("location_id", "source", "external_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(32))  # google | yelp | tripadvisor ...
    external_id: Mapped[str] = mapped_column(String(400))
    author: Mapped[str | None] = mapped_column(String(200))
    rating: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    posted_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reply_text: Mapped[str | None] = mapped_column(Text)
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # AI triage
    sentiment: Mapped[str | None] = mapped_column(String(16))  # positive | neutral | negative
    topics: Mapped[list] = mapped_column(JSON, default=list)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    suggested_reply: Mapped[str | None] = mapped_column(Text)
    triaged_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


# --- Alerts & notifications -----------------------------------------------------------------------


class AlertRule(TimestampMixin, Base):
    __tablename__ = "alert_rules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))  # low_stock | bad_review | sales_drop | daily_digest
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[list] = mapped_column(JSON, default=lambda: ["push", "email"])
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("location_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    channels: Mapped[list] = mapped_column(JSON, default=list)
    dedupe_key: Mapped[str] = mapped_column(String(200))  # stops the same alert firing twice
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, index=True)
    read_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class NotificationDelivery(Base):
    __tablename__ = "notification_deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    alert_id: Mapped[str] = mapped_column(ForeignKey("alerts.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(16))  # webpush | expo | email | whatsapp | sms
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | sent | failed | skipped
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class PushSubscription(TimestampMixin, Base):
    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "token"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # webpush | expo
    token: Mapped[str] = mapped_column(Text)  # Expo push token, or JSON web-push subscription


class UserChannelPrefs(TimestampMixin, Base):
    __tablename__ = "user_channel_prefs"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    push: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp: Mapped[bool] = mapped_column(Boolean, default=False)
    sms: Mapped[bool] = mapped_column(Boolean, default=False)


# --- Chat & AI usage ------------------------------------------------------------------------------


class Conversation(TimestampMixin, Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    channel: Mapped[str] = mapped_column(String(16), default="app")  # app | whatsapp | sms
    title: Mapped[str | None] = mapped_column(String(200))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))  # user | assistant
    content: Mapped[list] = mapped_column(JSON)  # Claude content blocks (text, tool_use, tool_result)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class AIUsage(Base):
    __tablename__ = "ai_usage"
    __table_args__ = (Index("ix_ai_usage_location_created", "location_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(String(36))
    purpose: Mapped[str] = mapped_column(String(32))  # chat | review_triage | digest
    model: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --- Jobs & webhooks ------------------------------------------------------------------------------


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_ready", "status", "run_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    locked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class WebhookEvent(Base):
    __tablename__ = "webhook_events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(16))
    body: Mapped[dict] = mapped_column(JSON)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
