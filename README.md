# Pass — a food ecosystem run by three AI agents

One platform for Vancouver with three connected agents. Everyone picks an avatar and can rename and restyle it:

| Agent | For | What it does |
| --- | --- | --- |
| **Restaurant Manager** | Restaurant owners and managers | Answers about sales, stock, orders, tables, labour and food cost from the POS (Clover first, Square next); forecasts the coming days from sales history, BC holidays and the weather, with a prep list; end-of-day, weekly and monthly reports; low-stock alerts that draft supplier orders for approval; bookings and a consented guest list; marketing ideas; replies to Google reviews; takes app orders. |
| **Customer agent** | People ordering food | Finds restaurants nearby that fit what they want (dish, diet, calories, open now); places pickup or delivery orders and books tables (the customer confirms in the app); order updates and tipping; a nutrition log against their own goals. |
| **Driver Assistant** | Drivers we employ (invite only) | Jobs with pickup, drop-off, route link and ETA; nearest-driver dispatch inside a 20 km service area, batching orders from the same restaurant; busy alerts to on-call drivers; deliveries per hour, on-time rate, wages, tips and a weekly goal. |

**Receiving.** When a delivery arrives, staff photograph the supplier invoice (or upload the PDF). Claude reads the supplier, invoice number, date and every line; the app matches each line to a stock item (remembered from earlier invoices, otherwise by name) and shows the price change against the last delivery. After a person checks and confirms, the lines go into the receiving log sheet (filter, search, download as CSV), tracked stock goes up (with pack sizes, e.g. 24 bottles per case), and any price change of 3% or more is announced by the Restaurant Manager, with what it costs per week at the usual order volume. The agent can also answer "which supplier prices went up?" from the same data.

One order touches all three: a customer orders, the restaurant accepts and marks it ready, the nearest on-shift driver gets the job, the customer is kept updated and can tip, the meal lands in their nutrition log, and the restaurant's reports count it. Agents prepare actions, but anything that sends, posts, orders, books or charges waits for a person to tap confirm.

"Pass" is a working name; rename it in `web/index.html`, `web/public/manifest.webmanifest` and `mobile/app.json`.

## What's in the repo

| Folder | What it is |
| --- | --- |
| `backend/` | Python 3.12, FastAPI, SQLAlchemy 2 (async), Alembic. One codebase runs as the **API** and the **worker**. |
| `web/` | React + Vite installable web app (PWA) with a home for each agent; each agent is a button in the bottom-right corner that opens a chat window on every screen. Restaurant: today, plan (forecast, reports, labour, marketing), app orders, bookings and guests, stock, purchasing, reviews, alerts, settings. Customer: discover and order, orders and bookings, food log, profile. Driver: jobs, earnings; plus the fleet dashboard for dispatchers. |
| `mobile/` | Expo (React Native) app on the same API: Restaurant Manager assistant, today, alerts, push notifications. |
| `.railway/railway.ts` | Railway setup: Postgres, the API and the worker in US East. Deploys on every push to `main`. |
| `infra/gcp/deploy.sh` | Alternative: deploy to Google Cloud Run in Toronto if you later need data hosted in Canada. |
| `.github/workflows/ci.yml` | Backend tests on Postgres, web build, mobile type check, on every push. |

How it fits together: POS and review data is synced into Postgres (webhooks + a pull every 15 minutes) and rolled up per day. The AI agent answers from those tables through typed, tenant-scoped tools, so answers are fast and cheap and never call Clover live. See the architecture doc for the full design and cost estimate.

## Run it locally (about 5 minutes)

Needs Docker and Node 20+.

```bash
cp .env.example .env              # works as-is for local dev; add ANTHROPIC_API_KEY to enable chat
docker compose up --build         # Postgres + API on :8000 + worker
docker compose exec api python -m app.seed --email owner@example.com   # demo restaurant, 60 days of data
docker compose exec api python -m app.seed --driver-email owner@example.com   # optional: also make you a driver

cd web && npm install && npm run dev                # http://localhost:5173, sign in as owner@example.com
```

Without Supabase configured, the apps use a development sign-in (any email, no password). Without `ANTHROPIC_API_KEY`, everything works except chat and AI review triage.

To try all three agents as one person: seed the demo, invite yourself as a driver (above), and set `PLATFORM_ADMIN_EMAILS=owner@example.com` for the Fleet dashboard. Use "Switch agent" at the top of the side bar. The demo restaurant is listed in your own customer app, so you can order from it, accept it as the restaurant, and deliver it as the driver. Demo restaurants are hidden from every other customer.

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
2. **Supabase** (used for sign-in only, so the Free plan is enough; pick the Canada (Central) region): create a project. Put `SUPABASE_URL` in the Railway variables (below), plus `SUPABASE_JWT_SECRET` only if your project still signs tokens with the legacy JWT secret; in the web app set `VITE_SUPABASE_URL` and `VITE_SUPABASE_ANON_KEY` (build-time env vars), and in `mobile/app.json` set `extra.supabaseUrl` and `extra.supabaseAnonKey`. Enable the Email and Google sign-in providers.
3. **Clover** (sandbox first): create an app in the developer dashboard with Read permissions for Merchant, Orders, Inventory and Employees. In REST Configuration set the **Site URL** to `https://<api>` and the **Alternate Launch Path** to `https://<api>/oauth/clover/launch` (where Clover sends merchants who open or install the app from Clover; Pass then asks them to connect it to their restaurant). The OAuth redirect `https://<api>/oauth/clover/callback` sits under the Site URL and the webhook URL to `https://<api>/webhooks/clover`, subscribe to Orders and Inventory events, and copy the Clover Auth Code into `CLOVER_WEBHOOK_AUTH_CODE`. When you save the webhook URL, Clover sends a verification code: it is printed in the API logs; paste it back into the dashboard. For production set `CLOVER_API_BASE=https://api.clover.com` and `CLOVER_WEB_BASE=https://www.clover.com` (Clover's North America region covers Canada).
4. **Google Business Profile**: request API access from Google (approval gates the reviews feature), then create an OAuth client (Web application) with redirect `https://<api>/oauth/google/callback`.
5. **Railway**: see [Deploy on Railway](#deploy-on-railway) below.
6. **Web app hosting**: `cd web && VITE_API_URL=https://<api> npm run build` and upload `web/dist` to Cloudflare Pages or Netlify (both have free tiers). Add that URL to `CORS_ORIGINS` and `WEB_APP_URL`.
7. **Later**: Resend (email), Twilio WhatsApp sender + approved alert template, VAPID keys for browser push (`npx web-push generate-vapid-keys`), Apple and Google developer accounts for the mobile app.

## Deploy on Railway

The backend runs as three Railway services in one project, all in **US East (Virginia)**: `postgres`, `api` (public) and `worker` (always on; it also queues the 15-minute syncs and morning digests, so no separate scheduler is needed). Every push to `main` redeploys both services, and the API runs `alembic upgrade head` before each new version goes live.

1. Create a Railway account on the **Pro** plan (US$20/month, which includes US$20 of usage).
2. Install the CLI and log in: `npm i -g @railway/cli`, then `railway login`.
3. From the repo root: `cd .railway && npm install && cd ..`, then `railway config plan` to preview and `railway config apply` to create the services.
4. In the Railway dashboard, generate a public domain for `api` (Settings > Networking). That's your `<api>` URL.
5. Open `api` > Variables and set the secrets marked `preserve()` in `.railway/railway.ts`. Copy the same values to `worker` (or use Railway shared variables). The ones needed to start:
   - `APP_BASE_URL` = `https://<api>`; `WEB_APP_URL` and `CORS_ORIGINS` = your web app URL
   - `TOKEN_ENCRYPTION_KEY`: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `INTERNAL_TOKEN`: any long random string
   - `SUPABASE_URL`, `ANTHROPIC_API_KEY`, `CLOVER_APP_ID`, `CLOVER_APP_SECRET`, `CLOVER_WEBHOOK_AUTH_CODE`
6. Redeploy both services. Check that `postgres` also sits in US East (its service settings), next to the API, and that `https://<api>/health` returns `{"ok": true}`.
7. Point the outside services at it: Clover OAuth redirect `https://<api>/oauth/clover/callback` and webhook `https://<api>/webhooks/clover`; Google OAuth redirect `https://<api>/oauth/google/callback`; Twilio inbound `https://<api>/webhooks/twilio`.

Watch the worker's logs once after deploying: you should see a `tick:` line within a minute, then `ran N job(s)` as work comes in. Keep the worker at one replica.

Supabase's Free plan pauses a project after about a week with little activity. Once restaurants sign in daily that won't happen, but during a quiet pilot keep an eye on Supabase's warning emails, or move to Supabase Pro if a pause would hurt.

## Web app on Railway

The `web` folder deploys as a fourth Railway service (it has its own `Dockerfile` and `Caddyfile`).

1. In the Railway project: **+ New → GitHub Repo → duckhoa123/supermarketman**. Name it `web`; Settings → Root Directory `web`.
2. Variables on `web` (they're baked in at build time, so redeploy after changing them):
   - `VITE_API_URL` = `https://<api address>`
   - `VITE_SUPABASE_URL` = your Supabase project URL
   - `VITE_SUPABASE_PUBLISHABLE_KEY` = Supabase → Settings → API Keys → publishable key (`sb_publishable_...`)
   - `PORT` = `8080`
   - Optional: `VITE_GOOGLE_SIGNIN=true` once the Google provider is enabled in Supabase
3. Settings → Networking → **+ HTTP Proxy**, port `8080`. That's the address people open.
4. On `api`, set `WEB_APP_URL` and `CORS_ORIGINS` to `https://<web address>` and redeploy `api`.
5. In Supabase → Authentication → URL Configuration, set **Site URL** to `https://<web address>` and add it under **Redirect URLs**, so sign-in links come back to the app.

New accounts can click **Explore a demo restaurant first** to get "Demo Bistro" with sample data. Set `DEMO_DATA_ENABLED=false` on `api` to hide it once real restaurants are using the app.

## Ecosystem settings (api and worker)

| Variable | What it does |
| --- | --- |
| `PLATFORM_ADMIN_EMAILS` | Comma-separated emails that can invite drivers and open the Fleet dashboard. Invite a driver by email; that account gets the Driver Assistant when it signs in. |
| `DELIVERY_HUB_LAT`, `DELIVERY_HUB_LNG`, `DELIVERY_RADIUS_KM` | The delivery hub (default Main St & Broadway) and service radius (default 20 km). Restaurants and drop-offs outside it get pickup only. |
| `DELIVERY_BASE_FEE_CENTS`, `DELIVERY_BASE_KM`, `DELIVERY_PER_KM_CENTS` | Customer delivery fee: $3.99 up to 3 km, then $0.75 per started km. |
| `RESTAURANT_FEE_PICKUP_BP`, `RESTAURANT_FEE_DELIVERY_BP` | Platform fee to the restaurant (6% pickup, 11% delivery). The code never charges more than BC's 20% cap. |
| `DEFAULT_DRIVER_WAGE_CENTS` | Starting hourly wage for invited drivers ($20.00; the BC minimum is $18.25 from June 2026). |
| `OPEN_METEO_API_KEY` | Weather in the sales forecast. Open-Meteo's free API is non-commercial, so production fetches weather only with a paid key. |
| `STRIPE_SECRET_KEY` | Card payments. Empty means test mode: orders are marked "test" and nothing is charged. Add Stripe's webhook before going live. |

## Restaurant websites

Each restaurant can build its own site under **Website** in the app.

- **13 templates.** Eight photo-led ones (Gourmet, Trattoria, Smokehouse, Zen, Café, Coastal, Spice, Patisserie) and five simple ones (Bistro, Street, Minimal, Night, Garden). Switching template changes colours, fonts, header, footer, section backgrounds and layouts, and adds the template's sections; the owner's words and photos stay.
- **Make it their own:** four colours (accent, background, text, dark bands), heading, text and handwritten fonts, corner style, header style (solid, dark, or see-through over the welcome photo), a top strip with address/phone/hours, a logo, and a simple or multi-column footer.
- **13 section types**, each with its own layouts and a background (page, soft tint, dark, accent): big welcome, announcement bar, highlights with icons, our story (two photos, round badge, handwritten line), signature dishes (picked from the menu, or the best sellers by default), menu (columns, tabs or cards), events and occasions, booking form with a reviews slider, meet the chef, gallery (grid, mosaic or strip), reviews (cards or slider), hours and location, and an order-or-book banner. Sections can be reordered, hidden or removed. "Write the words for me" drafts the tagline and story from the restaurant's own data.
- **Booking form.** Guests pick a date, time and party size on the site and confirm in the web app, which opens with those filled in. It needs bookings turned on (Settings → Customer app); until then the form shows the phone number.
- **Draft and publish.** Every edit saves to a draft that only the restaurant's team sees in the preview (desktop or phone). **Publish** makes it live; **Discard changes** goes back to what's live. The last 30 published versions can be restored.
- **Menu page (optional).** Owners can turn on a separate menu page at `/sites/<web address>/menu` (or `/menu` on their own domain), with a heading, links to each category, and a choice of layout and categories. The header's "Menu" link and the "View the menu" buttons then open it. The editor only sets how it looks: dishes, prices and descriptions are changed in Stock or the POS, never on the website.
- **Always current.** Menu, prices, hours and reviews are read live, so a price change in the POS shows on the site within a minute without republishing.
- **Safe by design.** Owners never enter HTML or CSS: the server builds the page from checked content and escapes everything. Pages use no JavaScript (tabs, slider and phone menu are plain CSS).
- Each section type's fields are declared once in `backend/app/services/site_templates.py`; the same declaration validates saved content and draws the editor's forms. Add a template there and its CSS in `site_render.py`.

Live sites are served by `api` at `https://<api address>/sites/<web address>`. To use the restaurant's own domain (e.g. `www.demobistro.ca`), enter it in Website → Settings, add a CNAME record at the domain registrar pointing to the `api` address, and add the same domain on the `api` service in Railway (Settings → Networking → Custom domain). The site's buttons open ordering and bookings in the web app (`WEB_APP_URL`), so set that on `api`.

## Main API routes

| Route | Purpose |
| --- | --- |
| `GET /me`, `PUT /me`, `POST /onboarding` | Account, alert channels, first restaurant |
| `GET /locations/{id}/dashboard?period=` | Headline numbers, hourly and 28-day trend, best sellers, low stock |
| `POST /locations/{id}/chat` | Ask the Restaurant Manager (Server-Sent Events stream) |
| `GET /agents`, `PUT /agents/{agent}/profile` | Avatars: name, icon, colour, tone |
| `POST /agents/customer/chat`, `POST /agents/driver/chat` | Ask the Customer agent or the Driver Assistant |
| `GET /locations/{id}/forecast`, `/reports/{day,week,month}`, `/labour`, `/food-cost`, `/marketing` | Forecast and prep list, reports, cost control, marketing facts |
| `/locations/{id}/suppliers`, `/purchase-orders` (`/draft`, `/{po}/message`, `/{po}/send`, `/{po}/received`), `/order-template` | Suppliers, orders drafted from low stock, and the message sent to the supplier (by email, text, or by the manager via WhatsApp/their own email) from the restaurant's template |
| `/locations/{id}/bookings`, `/guests`, `/shifts`, `/profile`, `/app-orders` | Bookings, guest list, staff shifts, public listing, orders from the customer app |
| `/customer/restaurants`, `/customer/orders` (`/quote`, `/{id}/tip`), `/customer/bookings`, `/customer/nutrition`, `/customer/profile` | Customer app |
| `/driver/me`, `/driver/shift/{start,end}`, `/driver/location`, `/driver/deliveries/{id}/status`, `/driver/stats` | Driver app |
| `/fleet/overview`, `/fleet/drivers`, `/fleet/deliveries/{id}/assign` | Dispatcher and platform admin |
| `GET /me/alerts` | Personal notifications (order, booking and job updates) |
| `POST /locations/{id}/invoices` (photo or PDF), `GET/PUT /invoices/{scan}`, `/invoices/{scan}/confirm` | Scan, check and confirm a delivery invoice |
| `GET /locations/{id}/receiving-log` (`?format=csv`), `/price-changes`, `/price-history` | Receiving log sheet and supplier price tracking |
| `/locations/{id}/website` (`/draft`, `/template`, `/preview`, `/publish`, `/discard`, `/versions`, `/settings`, `/images`, `/write-copy`) | Website editor |
| `GET /sites/{slug}` | A restaurant's live website |
| `GET /locations/{id}/inventory`, `/orders`, `/tables`, `/reviews`, `/alerts` | Data screens |
| `POST /locations/{id}/reviews/{rid}/reply` | Post an owner reply to Google |
| `GET /locations/{id}/integrations/{provider}/connect` | Start Clover/Google OAuth |
| `POST /webhooks/clover`, `POST /webhooks/twilio` | Inbound POS changes; owners texting the assistant |
| `POST /internal/tick`, `POST /internal/run-jobs` | Manual triggers for operators (the worker schedules itself) |

Interactive docs at `http://localhost:8000/docs` in development.

## Adding another POS

Implement the `POSAdapter` interface in `backend/app/integrations/pos/` (see `clover.py`; `square.py` is the stub to fill in next), register it in `registry.py`, and add the provider to the Settings page. Sync, roll-ups, the agent and the dashboard work unchanged.

## Costs to watch

Every AI call is logged in `ai_usage` with its US$ cost, per restaurant. `MONTHLY_AI_BUDGET_USD` caps each restaurant: above it the assistant stays on the cheaper model. Query `select location_id, sum(cost_usd) from ai_usage group by 1` to see cost per customer.
