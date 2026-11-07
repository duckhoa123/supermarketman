"""Clover REST adapter (OAuth v2 with expiring tokens, orders, inventory).

Endpoints (docs.clover.com):
- Authorize:  {web_base}/oauth/v2/authorize        -> redirects back with ?code=&merchant_id=
- Token:      POST {api_base}/oauth/v2/token       {client_id, client_secret, code}
- Refresh:    POST {api_base}/oauth/v2/refresh     {client_id, refresh_token}  (refresh tokens are single use)
- Orders:     GET  {api_base}/v3/merchants/{mId}/orders
- Items:      GET  {api_base}/v3/merchants/{mId}/items  (limit max 1000)
- Writes (need the Inventory / Orders *write* permissions):
  POST /v3/merchants/{mId}/items, /items/{id}, /categories, /category_items, /item_stocks/{itemId};
  POST /v3/merchants/{mId}/orders, /orders/{id}/line_items, /orders/{id} (total)

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
    OutLine,
    POSAuthError,
    POSRateLimited,
    POSWriteError,
    TokenSet,
    WebhookChange,
)
from app.money import dt_to_ms

PAGE_SIZE = 100
MAX_LINES = 200  # Clover has no quantity on a line: each unit is its own line item
PERMISSION_HELP = ("Clover didn't allow Pass to make this change. In Clover, give the app (or your test API token) "
                   "{perm} permission, then connect again in Pass Settings.")
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

    async def merchant_name(self, token: str, merchant_id: str) -> str | None:
        """The merchant's name if this token can read it (None if the merchant doesn't exist for it)."""
        data = await self._get(token, f"/v3/merchants/{merchant_id}", {})
        return (data.get("name") or merchant_id) if data else None

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

    # --- Writes ---------------------------------------------------------------------------------------

    async def _send(self, token: str, path: str, body: dict | None, perm: str, method: str = "POST",
                    params: dict | None = None) -> dict:
        url = f"{self.settings.clover_api_base}{path}"
        headers = {"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT, "Accept": "application/json"}
        for attempt in range(4):
            resp = await self._http().request(method, url, json=body, params=params, headers=headers)
            if resp.status_code == 429 and attempt < 3:
                await asyncio.sleep(min(float(resp.headers.get("Retry-After", 2 ** (attempt + 1))), 10))
                continue
            if resp.status_code in (401, 403):
                # Not POSAuthError: reading may still work, so the connection stays as it is.
                raise POSWriteError(PERMISSION_HELP.format(perm=perm))
            if resp.status_code >= 400:
                try:
                    detail = resp.json().get("message") or resp.text
                except ValueError:
                    detail = resp.text
                raise POSWriteError(f"Clover said: {str(detail)[:300]}")
            return resp.json() if resp.content else {}
        raise POSRateLimited()

    async def _category_id(self, token: str, merchant_id: str, name: str) -> str:
        cats = await self._paged(token, f"/v3/merchants/{merchant_id}/categories", {})
        for c in cats:
            if (c.get("name") or "").strip().lower() == name.strip().lower():
                return c["id"]
        made = await self._send(token, f"/v3/merchants/{merchant_id}/categories", {"name": name}, "Inventory write")
        return made["id"]

    async def _set_category(self, token: str, merchant_id: str, item_id: str, category: str | None,
                            old: str | None = None) -> None:
        if old and (old or "").lower() != (category or "").lower():
            old_id = await self._category_id(token, merchant_id, old)
            await self._send(token, f"/v3/merchants/{merchant_id}/category_items",
                             {"elements": [{"category": {"id": old_id}, "item": {"id": item_id}}]},
                             "Inventory write", params={"delete": "true"})
        if category and (old or "").lower() != category.lower():
            cat_id = await self._category_id(token, merchant_id, category)
            await self._send(token, f"/v3/merchants/{merchant_id}/category_items",
                             {"elements": [{"category": {"id": cat_id}, "item": {"id": item_id}}]}, "Inventory write")

    async def create_item(self, access_token: str, merchant_id: str, *, name: str, price_cents: int,
                          category: str | None = None, stock_qty: float | None = None) -> NormalizedItem:
        made = await self._send(access_token, f"/v3/merchants/{merchant_id}/items",
                                {"name": name, "price": price_cents, "priceType": "FIXED"}, "Inventory write")
        item_id = made["id"]
        await self._set_category(access_token, merchant_id, item_id, category)
        if stock_qty is not None:
            await self._send(access_token, f"/v3/merchants/{merchant_id}/item_stocks/{item_id}",
                             {"quantity": stock_qty}, "Inventory write")
        return NormalizedItem(external_id=item_id, name=name, category=category, price_cents=price_cents,
                              stock_qty=stock_qty)

    async def update_item(self, access_token: str, merchant_id: str, item_id: str, *, name: str, price_cents: int,
                          category: str | None, old_category: str | None, stock_qty: float | None,
                          stock_changed: bool) -> None:
        await self._send(access_token, f"/v3/merchants/{merchant_id}/items/{item_id}",
                         {"name": name, "price": price_cents}, "Inventory write")
        await self._set_category(access_token, merchant_id, item_id, category, old_category)
        if stock_changed and stock_qty is not None:
            await self._send(access_token, f"/v3/merchants/{merchant_id}/item_stocks/{item_id}",
                             {"quantity": stock_qty}, "Inventory write")

    async def create_order(self, access_token: str, merchant_id: str, lines: list[OutLine], *, title: str,
                           note: str | None = None) -> str:
        """An open order with these lines (taken and paid for at the Clover device, like a phone order)."""
        units = [ln for ln in lines for _ in range(max(1, ln.qty))]
        if not units or len(units) > MAX_LINES:
            raise POSWriteError(f"An order needs 1 to {MAX_LINES} items")
        body = {"state": "open", "title": title[:127]}
        if note:
            body["note"] = note[:500]
        order = await self._send(access_token, f"/v3/merchants/{merchant_id}/orders", body, "Orders write")
        oid = order["id"]
        for ln in units:
            line = {"item": {"id": ln.item_external_id}} if ln.item_external_id else {
                "name": ln.name[:127], "price": ln.unit_price_cents}
            await self._send(access_token, f"/v3/merchants/{merchant_id}/orders/{oid}/line_items", line, "Orders write")
        total = sum(ln.unit_price_cents for ln in units)
        await self._send(access_token, f"/v3/merchants/{merchant_id}/orders/{oid}", {"total": total}, "Orders write")
        return oid

