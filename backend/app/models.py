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
    LargeBinary,
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
    timezone: Mapped[str] = mapped_column(String(64), default="America/Vancouver")
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    digest_hour: Mapped[int] = mapped_column(Integer, default=8)  # local hour for the daily digest
    # Public profile, used by the Customer agent and delivery.
    address: Mapped[str | None] = mapped_column(String(300))
    lat: Mapped[float | None] = mapped_column(Float)
    lng: Mapped[float | None] = mapped_column(Float)
    phone: Mapped[str | None] = mapped_column(String(32))
    cuisine: Mapped[str | None] = mapped_column(String(100))
    # {"mon": [["11:30", "22:00"]], ...}; a closing time before the opening time runs past midnight.
    opening_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    listed: Mapped[bool] = mapped_column(Boolean, default=False)  # visible in the customer app
    accepts_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    accepts_delivery: Mapped[bool] = mapped_column(Boolean, default=False)
    accepts_bookings: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_rate_bp: Mapped[int] = mapped_column(Integer, default=500)  # BC restaurant meals: 5% GST

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
    # Purchasing: who supplies it and how much to order when it runs low.
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    reorder_qty: Mapped[float | None] = mapped_column(Float)
    # Menu details for customers: nutrition per serving, and tags like "vegetarian", "gluten_free".
    description: Mapped[str | None] = mapped_column(Text)
    calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    orderable: Mapped[bool] = mapped_column(Boolean, default=True)  # shown on the customer menu


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
    """A notification. Restaurant alerts have a location_id and go to its team; personal ones
    (a customer's order update, a driver's job) have a user_id and go to that person only."""

    __tablename__ = "alerts"
    __table_args__ = (UniqueConstraint("location_id", "dedupe_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
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
    # Set for the Restaurant Manager; customers and drivers talk to their agent without a restaurant.
    location_id: Mapped[str | None] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    agent: Mapped[str] = mapped_column(String(24), default="restaurant_manager")
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
    __table_args__ = (
        Index("ix_ai_usage_location_created", "location_id", "created_at"),
        Index("ix_ai_usage_user_created", "user_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str | None] = mapped_column(String(36))
    user_id: Mapped[str | None] = mapped_column(String(64))  # set for customer and driver chats
    agent: Mapped[str | None] = mapped_column(String(24))
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


# --- Ecosystem: agents and avatars -----------------------------------------------------------------

AGENTS = ("restaurant_manager", "customer", "driver")


class AgentProfile(TimestampMixin, Base):
    """How a person has customised one of their AI agents (name, look, tone)."""

    __tablename__ = "agent_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    agent: Mapped[str] = mapped_column(String(24), primary_key=True)  # one of AGENTS
    display_name: Mapped[str] = mapped_column(String(60))
    avatar: Mapped[dict] = mapped_column(JSON, default=dict)  # {"icon": "chef", "color": "#2563eb"}
    tone: Mapped[str] = mapped_column(String(24), default="friendly")  # friendly | concise | detailed | playful


# --- Restaurant Manager: purchasing, labour, guests, bookings, weather ---------------------------


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    notes: Mapped[str | None] = mapped_column(Text)  # e.g. "order by 2pm for next-day delivery"


class PurchaseOrder(TimestampMixin, Base):
    """Drafted by the app when stock runs low; sent to the supplier only after a manager approves it."""

    __tablename__ = "purchase_orders"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft | approved | sent | received | cancelled
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{"item_id", "name", "qty", "unit"}]
    note: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(64))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    send_error: Mapped[str | None] = mapped_column(Text)


class StaffShift(TimestampMixin, Base):
    __tablename__ = "staff_shifts"
    __table_args__ = (Index("ix_staff_shifts_location_start", "location_id", "starts_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    staff_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str | None] = mapped_column(String(32))  # kitchen | floor | bar | manager
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime] = mapped_column(UTCDateTime())
    hourly_rate_cents: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | pos


class Guest(TimestampMixin, Base):
    """A restaurant's guest list. Contact details are kept only with the guest's consent (BC PIPA);
    marketing messages additionally need marketing_consent (CASL)."""

    __tablename__ = "guests"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)  # linked customer account, if any
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    marketing_consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    visits: Mapped[int] = mapped_column(Integer, default=0)
    total_spend_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    last_visit_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    notes: Mapped[str | None] = mapped_column(Text)  # e.g. allergies, favourite table
    tags: Mapped[list] = mapped_column(JSON, default=list)


class Booking(TimestampMixin, Base):
    __tablename__ = "bookings"
    __table_args__ = (Index("ix_bookings_location_start", "location_id", "starts_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    guest_id: Mapped[str | None] = mapped_column(ForeignKey("guests.id", ondelete="SET NULL"))
    customer_user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    party_size: Mapped[int] = mapped_column(Integer, default=2)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # requested | confirmed | seated | completed | cancelled | no_show
    status: Mapped[str] = mapped_column(String(16), default="requested")
    source: Mapped[str] = mapped_column(String(16), default="staff")  # staff | phone | customer_app | walk_in
    table_id: Mapped[str | None] = mapped_column(String(36))
    notes: Mapped[str | None] = mapped_column(Text)


class WeatherDay(Base):
    """Daily weather forecast per restaurant (Open-Meteo), used by the sales forecast."""

    __tablename__ = "weather_daily"
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    temp_max_c: Mapped[float | None] = mapped_column(Float)
    temp_min_c: Mapped[float | None] = mapped_column(Float)
    precip_mm: Mapped[float | None] = mapped_column(Float)
    weather_code: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --- Customer agent: profiles, orders, nutrition --------------------------------------------------


class CustomerProfile(TimestampMixin, Base):
    __tablename__ = "customer_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    home_address: Mapped[str | None] = mapped_column(String(300))
    home_lat: Mapped[float | None] = mapped_column(Float)
    home_lng: Mapped[float | None] = mapped_column(Float)
    diet_tags: Mapped[list] = mapped_column(JSON, default=list)  # e.g. ["vegetarian", "nut_free"]
    calories_goal: Mapped[float | None] = mapped_column(Float)  # per day
    protein_goal_g: Mapped[float | None] = mapped_column(Float)
    carbs_goal_g: Mapped[float | None] = mapped_column(Float)
    fat_goal_g: Mapped[float | None] = mapped_column(Float)


class CustomerOrder(TimestampMixin, Base):
    """An order placed through the customer app (pickup or delivery), separate from POS orders."""

    __tablename__ = "customer_orders"
    __table_args__ = (
        Index("ix_customer_orders_location_placed", "location_id", "placed_at"),
        Index("ix_customer_orders_user_placed", "customer_user_id", "placed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    customer_user_id: Mapped[str] = mapped_column(String(64))
    fulfilment: Mapped[str] = mapped_column(String(16))  # pickup | delivery
    # placed | accepted | ready | out_for_delivery | delivered | picked_up | cancelled
    status: Mapped[str] = mapped_column(String(20), default="placed")
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{"item_id","name","qty","unit_price_cents",...}]
    subtotal_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tax_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    delivery_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    tip_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    restaurant_fee_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # platform fee, capped at 20%
    # unpaid | requires_payment | paid | test | refunded
    payment_status: Mapped[str] = mapped_column(String(20), default="unpaid")
    payment_ref: Mapped[str | None] = mapped_column(String(200))
    delivery_address: Mapped[str | None] = mapped_column(String(300))
    dropoff_lat: Mapped[float | None] = mapped_column(Float)
    dropoff_lng: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    share_contact: Mapped[bool] = mapped_column(Boolean, default=False)  # join the restaurant's guest list
    placed_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    ready_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    eta_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class NutritionEntry(Base):
    __tablename__ = "nutrition_log"
    __table_args__ = (Index("ix_nutrition_log_user_day", "user_id", "day"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(300))
    calories: Mapped[float | None] = mapped_column(Float)
    protein_g: Mapped[float | None] = mapped_column(Float)
    carbs_g: Mapped[float | None] = mapped_column(Float)
    fat_g: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | order
    customer_order_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --- Driver Assistant: the in-house delivery fleet ------------------------------------------------


class Driver(TimestampMixin, Base):
    """A driver employed by the platform (not a gig courier). Added by invite: a platform admin
    enters the email, and the account with that email becomes the driver on first sign-in."""

    __tablename__ = "drivers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[str] = mapped_column(String(16), default="driver")  # driver | dispatcher
    vehicle: Mapped[str] = mapped_column(String(16), default="e_bike")  # e_bike | bike | car
    hourly_wage_cents: Mapped[int] = mapped_column(Integer, default=2000)
    weekly_goal_cents: Mapped[int | None] = mapped_column(Integer)  # the driver's own earnings goal
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="off")  # off | on_shift | on_call
    last_lat: Mapped[float | None] = mapped_column(Float)
    last_lng: Mapped[float | None] = mapped_column(Float)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DriverShift(Base):
    __tablename__ = "driver_shifts"
    __table_args__ = (Index("ix_driver_shifts_driver_start", "driver_id", "starts_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    driver_id: Mapped[str] = mapped_column(ForeignKey("drivers.id", ondelete="CASCADE"))
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime())
    ends_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # empty while the shift is running
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class Delivery(TimestampMixin, Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    customer_order_id: Mapped[str] = mapped_column(
        ForeignKey("customer_orders.id", ondelete="CASCADE"), unique=True
    )
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"), index=True)
    driver_id: Mapped[str | None] = mapped_column(ForeignKey("drivers.id", ondelete="SET NULL"), index=True)
    # waiting (order not ready or no driver yet) | assigned | picked_up | delivered | cancelled
    status: Mapped[str] = mapped_column(String(16), default="waiting", index=True)
    pickup_lat: Mapped[float] = mapped_column(Float)
    pickup_lng: Mapped[float] = mapped_column(Float)
    dropoff_lat: Mapped[float] = mapped_column(Float)
    dropoff_lng: Mapped[float] = mapped_column(Float)
    distance_km: Mapped[float] = mapped_column(Float)  # estimated road distance, restaurant to customer
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    picked_up_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    delivered_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    promised_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # ETA shown when the order was placed
    eta_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # latest estimate
    tip_cents: Mapped[int] = mapped_column(BigInteger, default=0)  # 100% goes to the driver


# --- Receiving: scanned supplier invoices, the receiving log and price history --------------------


class InvoiceScan(TimestampMixin, Base):
    """A supplier invoice photographed or uploaded when a delivery arrives.

    processing -> review (AI read it; a person checks the lines) -> confirmed (lines go into the
    receiving log and stock) | failed | discarded. The image is kept for INVOICE_FILE_DAYS, the data forever.
    """

    __tablename__ = "invoice_scans"
    __table_args__ = (Index("ix_invoice_scans_location_created", "location_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    uploaded_by: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="processing")
    file_name: Mapped[str | None] = mapped_column(String(300))
    content_type: Mapped[str | None] = mapped_column(String(100))
    file_bytes: Mapped[bytes | None] = mapped_column(LargeBinary, deferred=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    subtotal_cents: Mapped[int | None] = mapped_column(BigInteger)
    tax_cents: Mapped[int | None] = mapped_column(BigInteger)
    total_cents: Mapped[int | None] = mapped_column(BigInteger)
    # Draft lines while in review: [{"description","sku","qty","unit","unit_price_cents","line_total_cents",
    # "kind","item_id","pack_size","match","last_price_cents","change_pct"}]
    lines: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    purchase_order_id: Mapped[str | None] = mapped_column(String(36))
    confirmed_by: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ReceivedLine(Base):
    """One line of a confirmed invoice: the receiving log sheet, and the price history of every product."""

    __tablename__ = "received_lines"
    __table_args__ = (
        Index("ix_received_lines_location_day", "location_id", "received_on"),
        Index("ix_received_lines_location_product", "location_id", "product_key"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    location_id: Mapped[str] = mapped_column(ForeignKey("locations.id", ondelete="CASCADE"))
    scan_id: Mapped[str] = mapped_column(ForeignKey("invoice_scans.id", ondelete="CASCADE"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(ForeignKey("suppliers.id", ondelete="SET NULL"))
    supplier_name: Mapped[str | None] = mapped_column(String(200))
    invoice_number: Mapped[str | None] = mapped_column(String(100))
    received_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(String(300))
    sku: Mapped[str | None] = mapped_column(String(100))
    # Supplier + normalised description: how the same product is recognised on the next invoice.
    product_key: Mapped[str] = mapped_column(String(400))
    kind: Mapped[str] = mapped_column(String(16), default="item")  # item | fee | credit
    item_id: Mapped[str | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"), index=True)
    qty: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str | None] = mapped_column(String(32))
    unit_price_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    line_total_cents: Mapped[int] = mapped_column(BigInteger, default=0)
    pack_size: Mapped[float] = mapped_column(Float, default=1)  # stock units per invoice unit, e.g. 24 per case
    stock_added: Mapped[float | None] = mapped_column(Float)
    previous_unit_price_cents: Mapped[int | None] = mapped_column(BigInteger)
    price_change_pct: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
