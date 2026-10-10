"""Clover REST adapter (OAuth v2 with expiring tokens, orders, inventory).

Endpoints (docs.clover.com):
- Authorize:  {web_base}/oauth/v2/authorize        -> redirects back with ?code=&merchant_id=
- Token:      POST {api_base}/oauth/v2/token       {client_id, client_secret, code}
- Refresh:    POST {api_base}/oauth/v2/refresh     {client_id, refresh_token}  (refresh tokens are single use)
- Orders:     GET  {api_base}/v3/merchants/{mId}/orders
- Items:      GET  {api_base}/v3/merchants/{mId}/items  (limit max 1000)

Hosts: sandbox apisandbox.dev.clover.com; production api.clover.com (North America: US and Canada),
api.eu.clover.com, api.la.clover.com.
"""

import asyncio
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.integrations.pos import clover_parse
from app.integrations.pos.base import (
    NormalizedItem,
    NormalizedOrder,
    POSAuthError,
    POSRateLimited,
    TokenSet,
    WebhookChange,
)
from app.money import dt_to_ms

PAGE_SIZE = 100
ORDER_EXPAND = "lineItems,lineItems.modifications,employee,payments"
USER_AGENT = "RestaurantAI/0.1"


def _expiry(value) -> datetime | None:
    if not value:
        return None
    value = int(value)
    if value > 10**12:  # tolerate milliseconds
        value //= 1000
    return datetime.fromtimestamp(value, tz=timezone.utc)


class CloverAdapter:
    provider = "clover"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.settings = get_settings()
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=5.0))
        return self._client

    # --- OAuth ----------------------------------------------------------------------------------

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        query = urlencode({"client_id": self.settings.clover_app_id, "redirect_uri": redirect_uri, "state": state})
        return f"{self.settings.clover_web_base}/oauth/v2/authorize?{query}"

    def _token_set(self, data: dict, merchant_id: str | None = None) -> TokenSet:
        return TokenSet(
            access_token=data["access_token"],
            refresh_token=data.get("refresh_token"),
            access_expires_at=_expiry(data.get("access_token_expiration")),
            external_id=merchant_id,
        )

    async def exchange_code(self, code: str, query: dict[str, str], redirect_uri: str) -> TokenSet:
        resp = await self._http().post(
            f"{self.settings.clover_api_base}/oauth/v2/token",
            json={
                "client_id": self.settings.clover_app_id,
                "client_secret": self.settings.clover_app_secret,
                "code": code,
            },
        )
        if resp.status_code >= 400:
            raise POSAuthError(f"Clover token exchange failed: {resp.status_code} {resp.text[:200]}")
        return self._token_set(resp.json(), merchant_id=query.get("merchant_id"))

    async def refresh(self, refresh_token: str) -> TokenSet:
        resp = await self._http().post(
            f"{self.settings.clover_api_base}/oauth/v2/refresh",
            json={"client_id": self.settings.clover_app_id, "refresh_token": refresh_token},
        )
        if resp.status_code in (400, 401):
            raise POSAuthError("Clover refresh token rejected; merchant must reconnect")
        resp.raise_for_status()
        return self._token_set(resp.json())

    # --- REST -----------------------------------------------------------------------------------

    async def _get(self, token: str, path: str, params: dict) -> dict:
        url = f"{self.settings.clover_api_base}{path}"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT, "Accept": "application/json"}
        for attempt in range(4):
            resp = await self._http().get(url, params=params, headers=headers)
            if resp.status_code == 429:
                wait = float(resp.headers.get("Retry-After", 2 ** (attempt + 1)))
                if attempt == 3:
                    raise POSRateLimited(wait)
                await asyncio.sleep(min(wait, 10))
                continue
            if resp.status_code == 401:
                raise POSAuthError("Clover access token rejected")
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
        raise POSRateLimited()

    async def _paged(self, token: str, path: str, params: dict, page_size: int = PAGE_SIZE) -> list[dict]:
        out: list[dict] = []
        offset = 0
        while True:
            data = await self._get(token, path, params | {"limit": page_size, "offset": offset})
            elements = data.get("elements") or []
            out.extend(elements)
            if len(elements) < page_size:
                return out
            offset += page_size

    async def fetch_items(self, access_token: str, merchant_id: str) -> list[NormalizedItem]:
        raw = await self._paged(
            access_token,
            f"/v3/merchants/{merchant_id}/items",
            {"expand": "itemStock,categories"},
            page_size=1000,
        )
        return [clover_parse.normalize_item(r) for r in raw]

    async def fetch_orders(
        self, access_token: str, merchant_id: str, modified_since: datetime | None
    ) -> list[NormalizedOrder]:
        params: dict = {"expand": ORDER_EXPAND, "orderBy": "modifiedTime ASC"}
        if modified_since is not None:
            # Filter operator syntax per Clover's "Manage orders data" guide; verify in sandbox.
            params["filter"] = f"modifiedTime>={dt_to_ms(modified_since)}"
        raw = await self._paged(access_token, f"/v3/merchants/{merchant_id}/orders", params)
        return [clover_parse.normalize_order(r) for r in raw]

    async def fetch_order(self, access_token: str, merchant_id: str, order_id: str) -> NormalizedOrder | None:
        raw = await self._get(
            access_token, f"/v3/merchants/{merchant_id}/orders/{order_id}", {"expand": ORDER_EXPAND}
        )
        return clover_parse.normalize_order(raw) if raw.get("id") else None

    async def fetch_item(self, access_token: str, merchant_id: str, item_id: str) -> NormalizedItem | None:
        raw = await self._get(
            access_token, f"/v3/merchants/{merchant_id}/items/{item_id}", {"expand": "itemStock,categories"}
        )
        return clover_parse.normalize_item(raw) if raw.get("id") else None

    def parse_webhook(self, body: dict) -> list[WebhookChange]:
        return clover_parse.parse_webhook(body)
