"""Square adapter: phase 2.

The interface is the same as CloverAdapter. Implementation notes for when this is built:
- OAuth: https://connect.squareup.com/oauth2/authorize -> POST /oauth2/token (code and refresh grants).
- Orders: POST /v2/orders/search with a `state_filter` and `date_time_filter.updated_at` window.
- Catalog: GET /v2/catalog/list?types=ITEM,CATEGORY ; inventory: POST /v2/inventory/counts/batch-retrieve.
- Webhooks: order.updated, inventory.count.updated, catalog.version.updated; verify the
  x-square-hmacsha256-signature header.
Check these against Square's current API reference before implementing.
"""

from datetime import datetime

from app.integrations.pos.base import NormalizedItem, NormalizedOrder, TokenSet, WebhookChange


class SquareAdapter:
    provider = "square"

    def _todo(self):
        raise NotImplementedError("Square integration is scheduled for phase 2")

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        self._todo()

    async def exchange_code(self, code: str, query: dict[str, str], redirect_uri: str) -> TokenSet:
        self._todo()

    async def refresh(self, refresh_token: str) -> TokenSet:
        self._todo()

    async def fetch_items(self, access_token: str, merchant_id: str) -> list[NormalizedItem]:
        self._todo()

    async def fetch_orders(
        self, access_token: str, merchant_id: str, modified_since: datetime | None
    ) -> list[NormalizedOrder]:
        self._todo()

    async def fetch_order(self, access_token: str, merchant_id: str, order_id: str) -> NormalizedOrder | None:
        self._todo()

    def parse_webhook(self, body: dict) -> list[WebhookChange]:
        self._todo()
