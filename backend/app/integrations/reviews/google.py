"""Google Business Profile: OAuth, list locations, list reviews, post owner replies.

Access to the Business Profile APIs must be requested from Google and approved before these calls work.
Scope: https://www.googleapis.com/auth/business.manage

- Accounts:  GET https://mybusinessaccountmanagement.googleapis.com/v1/accounts
- Locations: GET https://mybusinessbusinessinformation.googleapis.com/v1/{account}/locations?readMask=name,title
- Reviews:   GET https://mybusiness.googleapis.com/v4/{account}/{location}/reviews  (verified locations only)
- Reply:     PUT https://mybusiness.googleapis.com/v4/{review name}/reply  {"comment": "..."}
"""

from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from app.config import get_settings
from app.integrations.pos.base import POSAuthError, TokenSet
from app.integrations.reviews.parse import NormalizedReview, normalize_google_review

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/business.manage"
ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"
V4_BASE = "https://mybusiness.googleapis.com/v4"


class GoogleReviewsClient:
    source = "google"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self.settings = get_settings()
        self._client = client or httpx.AsyncClient(timeout=20.0)

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return AUTH_URL + "?" + urlencode(
            {
                "client_id": self.settings.google_client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPE,
                "access_type": "offline",  # we need a refresh token for background polling
                "prompt": "consent",
                "state": state,
            }
        )

    async def _token(self, data: dict) -> TokenSet:
        resp = await self._client.post(
            TOKEN_URL,
            data=data
            | {"client_id": self.settings.google_client_id, "client_secret": self.settings.google_client_secret},
        )
        if resp.status_code in (400, 401):
            raise POSAuthError(f"Google token request failed: {resp.text[:200]}")
        resp.raise_for_status()
        body = resp.json()
        return TokenSet(
            access_token=body["access_token"],
            refresh_token=body.get("refresh_token") or data.get("refresh_token"),
            access_expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 3600))),
        )

    async def exchange_code(self, code: str, redirect_uri: str) -> TokenSet:
        return await self._token({"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri})

    async def refresh(self, refresh_token: str) -> TokenSet:
        return await self._token({"refresh_token": refresh_token, "grant_type": "refresh_token"})

    async def _get(self, token: str, url: str, params: dict | None = None) -> dict:
        resp = await self._client.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401:
            raise POSAuthError("Google access token rejected")
        resp.raise_for_status()
        return resp.json()

    async def list_locations(self, token: str) -> list[dict]:
        """Returns [{"name": "accounts/1/locations/2", "title": "Cafe Example"}, ...]."""
        out = []
        accounts = (await self._get(token, ACCOUNTS_URL)).get("accounts", [])
        for account in accounts:
            data = await self._get(
                token, f"{INFO_BASE}/{account['name']}/locations", {"readMask": "name,title", "pageSize": 100}
            )
            for loc in data.get("locations", []):
                # Business Information returns "locations/2"; the v4 reviews API wants the account prefix.
                out.append({"name": f"{account['name']}/{loc['name']}", "title": loc.get("title")})
        return out

    async def list_reviews(self, token: str, location_name: str, max_pages: int = 3) -> list[NormalizedReview]:
        """Newest first. Polling stops after a few pages; older reviews were stored on earlier polls."""
        reviews: list[NormalizedReview] = []
        page_token = None
        for _ in range(max_pages):
            params = {"pageSize": 50, "orderBy": "updateTime desc"}
            if page_token:
                params["pageToken"] = page_token
            data = await self._get(token, f"{V4_BASE}/{location_name}/reviews", params)
            reviews += [normalize_google_review(r) for r in data.get("reviews", [])]
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return reviews

    async def post_reply(self, token: str, review_name: str, comment: str) -> None:
        resp = await self._client.put(
            f"{V4_BASE}/{review_name}/reply",
            json={"comment": comment},
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            raise POSAuthError("Google access token rejected")
        resp.raise_for_status()


_override: GoogleReviewsClient | None = None


def get_google_client() -> GoogleReviewsClient:
    return _override or GoogleReviewsClient()


def set_google_client(client: GoogleReviewsClient | None) -> None:
    global _override
    _override = client
