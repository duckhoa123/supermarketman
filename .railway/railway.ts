// Railway infrastructure: Postgres + API + worker, all in US East (Virginia, the closest region to Canada).
//
// Apply with the Railway CLI from the repo root:
//   railway config plan     # preview
//   railway config apply    # create or update services
//
// Secrets are never stored here: every `preserve()` keeps the value you set in the Railway dashboard
// (service > Variables). See README "Deploy on Railway".
import { defineRailway, github, postgres, preserve, project, service } from "railway/iac";

const REPO = "duckhoa123/supermarketman";
const REGION = "us-east4-eqdc4a"; // US East Metal (Virginia)

export default defineRailway(() => {
  const db = postgres("postgres");

  // Same settings for the API and the worker; they run the same image.
  const env = {
    ENV: "production",
    DEV_AUTH_ENABLED: "false",
    DATABASE_URL: db.env.DATABASE_URL, // postgresql://... on Railway's private network; the app adds the asyncpg driver
    MODEL_DEFAULT: "claude-haiku-5-5",
    MODEL_DEEP: "claude-sonnet-5-5",
    MONTHLY_AI_BUDGET_USD: "5",
    CLOVER_API_BASE: "https://api.clover.com", // North America region: US and Canada
    CLOVER_WEB_BASE: "https://www.clover.com",
    // Set these in the dashboard; `preserve()` keeps whatever is there.
    APP_BASE_URL: preserve(),
    WEB_APP_URL: preserve(),
    CORS_ORIGINS: preserve(),
    TOKEN_ENCRYPTION_KEY: preserve(),
    INTERNAL_TOKEN: preserve(),
    SUPABASE_URL: preserve(),
    SUPABASE_JWT_SECRET: preserve(),
    ANTHROPIC_API_KEY: preserve(),
    CLOVER_APP_ID: preserve(),
    CLOVER_APP_SECRET: preserve(),
    CLOVER_WEBHOOK_AUTH_CODE: preserve(),
    GOOGLE_CLIENT_ID: preserve(),
    GOOGLE_CLIENT_SECRET: preserve(),
    RESEND_API_KEY: preserve(),
    EMAIL_FROM: preserve(),
    TWILIO_ACCOUNT_SID: preserve(),
    TWILIO_AUTH_TOKEN: preserve(),
    TWILIO_WHATSAPP_FROM: preserve(),
    TWILIO_SMS_FROM: preserve(),
    TWILIO_WHATSAPP_CONTENT_SID: preserve(),
    VAPID_PUBLIC_KEY: preserve(),
    VAPID_PRIVATE_KEY: preserve(),
    VAPID_SUBJECT: preserve(),
  };

  const api = service("api", {
    source: github(REPO, { branch: "main", rootDirectory: "backend" }), // builds backend/Dockerfile
    preDeploy: "alembic upgrade head", // migrations run before each new version goes live
    healthcheck: "/health",
    healthcheckTimeout: 60,
    replicas: { [REGION]: 1 },
    env,
  });

  // Always-on worker: drains the job queue and queues the 15-minute syncs, review polls and morning digests.
  const worker = service("worker", {
    source: github(REPO, { branch: "main", rootDirectory: "backend" }),
    start: "python -m app.worker --schedule",
    replicas: { [REGION]: 1 }, // keep at 1: it also acts as the scheduler
    env,
  });

  return project("supermarketman", { resources: [db, api, worker] });
});
