# Pass — AI assistant for restaurants

Owners ask questions about sales, stock, orders, tables and reviews in plain English, and get told when something needs them (bad reviews, low stock, slow days, a morning summary). Data comes from their POS (Clover first, Square next) and Google reviews.

"Pass" is a working name; rename it in `web/index.html`, `web/public/manifest.webmanifest` and `mobile/app.json`.

## What's in the repo

| Folder | What it is |
| --- | --- |
| `backend/` | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic. One codebase runs as the **API** and the **worker**. |
| `web/` | React + Vite installable web app (PWA): assistant, today, stock, orders, tables, reviews, alerts, settings. |
| `mobile/` | Expo (React Native) app on the same API: assistant, today, alerts, push notifications. |
| `infra/deploy.sh` | One script that deploys the backend to Google Cloud Run in Toronto with Cloud Scheduler. |
| `.github/workflows/ci.yml` | Backend tests on Postgres, web build, mobile type check, on every push. |

How it fits together: POS and review data is synced into Postgres (webhooks + a pull every 15 minutes) and rolled up per day. The AI agent answers from those tables through typed, tenant-scoped tools, so answers are fast and cheap and never call Clover live. See the architecture doc for the full design and cost estimate.

## Run it locally (about 5 minutes)

Needs Docker and Node 20+.

```bash
cp .env.example .env              # works as-is for local dev; add ANTHROPIC_API_KEY to enable chat
docker compose up --build         # Postgres + API on :8000 + worker
docker compose exec api python -m app.seed --email owner@example.com   # demo restaurant, 60 days of data

cd web && npm install && npm run dev                # http://localhost:5173, sign in as owner@example.com
```

Without Supabase configured, the apps use a development sign-in (any email, no password). Without `ANTHROPIC_API_KEY`, everything works except chat and AI review triage.

Mobile: `cd mobile && npm install && npx expo start`, then open it in Expo Go. Set `extra.apiUrl` in `mobile/app.json` to your computer's LAN address (for example `http://192.168.1.20:8000`) so the phone can reach the API.

## Tests

```bash
cd backend
pip install -e ".[dev]"
pytest -q                          # SQLite by default; set TEST_DATABASE_URL to run against Postgres
python -m unittest tests.test_core_logic   # the dependency-free core tests only
```

## Going live: checklist

1. **Claude API**: create a key at console.anthropic.com, add credits, set a monthly spend limit. (A Claude Pro subscription doesn't include API access.)
2. **Supabase** (Canada (Central) region, so customer data stays in Canada): create a project. Put `SUPABASE_URL` in `infra/prod.env`; in the web app set `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (build-time env vars), and in `mobile/app.json` set `extra.supabaseUrl` and `extra.supabaseAnonKey`. Enable the Email and Google sign-in providers.
3. **Clover** (sandbox first): create an app in the developer dashboard with Read permissions for Merchant, Orders, Inventory and Employees. Set the OAuth redirect to `https://<api>/oauth/clover/callback` and the webhook URL to `https://<api>/webhooks/clover`, subscribe to Orders and Inventory events, and copy the Clover Auth Code into `CLOVER_WEBHOOK_AUTH_CODE`. When you save the webhook URL, Clover sends a verification code: it is printed in the API logs; paste it back into the dashboard. For production set `CLOVER_API_BASE=https://api.clover.com` and `CLOVER_WEB_BASE=https://www.clover.com` (Clover's North America region covers Canada).
4. **Google Business Profile**: request API access from Google (approval gates the reviews feature), then create an OAuth client (Web application) with redirect `https://<api>/oauth/google/callback`.
5. **Google Cloud**: `cp .env.example infra/prod.env`, fill it in (generate `TOKEN_ENCRYPTION_KEY` and a long `INTERNAL_TOKEN`), then `PROJECT_ID=your-project ./infra/deploy.sh`.
6. **Web app hosting**: `cd web && VITE_API_URL=https://<api> npm run build` and upload `web/dist` to Firebase Hosting or Cloudflare Pages (both have free tiers). Add that URL to `CORS_ORIGINS` and `WEB_APP_URL`.
7. **Later**: Resend (email), Twilio WhatsApp sender + approved alert template, VAPID keys for browser push (`npx web-push generate-vapid-keys`), Apple and Google developer accounts for the mobile app.

## Main API routes

| Route | Purpose |
| --- | --- |
| `GET /me`, `PUT /me`, `POST /onboarding` | Account, alert channels, first restaurant |
| `GET /locations/{id}/dashboard?period=` | Headline numbers, hourly and 28-day trend, best sellers, low stock |
| `POST /locations/{id}/chat` | Ask the assistant (Server-Sent Events stream) |
| `GET /locations/{id}/inventory`, `/orders`, `/tables`, `/reviews`, `/alerts` | Data screens |
| `POST /locations/{id}/reviews/{rid}/reply` | Post an owner reply to Google |
| `GET /locations/{id}/integrations/{provider}/connect` | Start Clover/Google OAuth |
| `POST /webhooks/clover`, `POST /webhooks/twilio` | Inbound POS changes; owners texting the assistant |
| `POST /internal/tick`, `POST /internal/run-jobs` | Called by Cloud Scheduler |

Interactive docs at `http://localhost:8000/docs` in development.

## Adding another POS

Implement the `POSAdapter` interface in `backend/app/integrations/pos/` (see `clover.py`; `square.py` is the stub to fill in next), register it in `registry.py`, and add the provider to the Settings page. Sync, roll-ups, the agent and the dashboard work unchanged.

## Costs to watch

Every AI call is logged in `ai_usage` with its US$ cost, per restaurant. `MONTHLY_AI_BUDGET_USD` caps each restaurant: above it the assistant stays on the cheaper model. Query `select location_id, sum(cost_usd) from ai_usage group by 1` to see cost per customer.
