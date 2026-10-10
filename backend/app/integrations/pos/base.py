"""Provider-neutral shapes and the adapter interface every POS integration implements.

Adding a POS (Square, Toast, Lightspeed) means writing one adapter that returns these shapes;
sync, analytics and the agent never see provider-specific data.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str | None
    access_expires_at: datetime | None
    external_id: str | None = None  # merchant id returned during OAuth


@dataclass
class NormalizedItem:
    external_id: str
    name: str
    category: str | None = None
    price_cents: int = 0
    cost_cents: int | None = None
    stock_qty: float | None = None
    unit: str | None = None
    active: bool = True


@dataclass
class NormalizedLine:
    name: str
    qty: float
    unit_price_cents: int
    total_cents: int
    item_external_id: str | None = None
    category: str | None = None


@dataclass
class NormalizedOrder:
    external_id: str
    status: str  # open | paid | refunded | void
    total_cents: int
    opened_at: datetime
    closed_at: datetime | None = None
    tax_cents: int = 0
    tip_cents: int = 0
    table_name: str | None = None
    employee_name: str | None = None
    guests: int | None = None
    lines: list[NormalizedLine] = field(default_factory=list)


@dataclass
class WebhookChange:
    merchant_id: str
    object_type: str  # order | item | category | payment | merchant | other
    object_id: str
    change: str  # CREATE | UPDATE | DELETE
    ts_ms: int | None = None


class POSAdapter(Protocol):
    provider: str

    def authorize_url(self, state: str, redirect_uri: str) -> str: ...

    async def exchange_code(self, code: str, query: dict[str, str], redirect_uri: str) -> TokenSet: ...

    async def refresh(self, refresh_token: str) -> TokenSet: ...

    async def fetch_items(self, access_token: str, merchant_id: str) -> list[NormalizedItem]: ...

    async def fetch_orders(
        self, access_token: str, merchant_id: str, modified_since: datetime | None
    ) -> list[NormalizedOrder]: ...

    async def fetch_order(self, access_token: str, merchant_id: str, order_id: str) -> NormalizedOrder | None: ...

    def parse_webhook(self, body: dict) -> list[WebhookChange]: ...


class POSAuthError(Exception):
    """The merchant's token is no longer valid; they must reconnect."""


class POSRateLimited(Exception):
    def __init__(self, retry_after: float = 30.0):
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after
