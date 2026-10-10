#!/usr/bin/env bash
# Deploy the backend to Google Cloud Run in Sydney.
#
# Prerequisites: gcloud CLI logged in (`gcloud auth login`), a project with billing enabled,
# and infra/prod.env filled in (copy from .env.example; never commit it).
#
#   PROJECT_ID=my-project ./infra/deploy.sh            # full setup + deploy
#   PROJECT_ID=my-project ./infra/deploy.sh --code-only # rebuild and redeploy code only
#
# What it creates (idempotent where gcloud allows):
#   - Artifact Registry repo "restaurant"
#   - Secret Manager secrets, one per variable in infra/prod.env
#   - Cloud Run job  "restaurant-migrate"  (alembic upgrade head)
#   - Cloud Run svc  "restaurant-api"      public API, scales 0..3
#   - Cloud Run svc  "restaurant-worker"   private, drains the job queue when Cloud Scheduler calls it
#   - Cloud Scheduler: run-jobs every minute, tick every 15 minutes (2 jobs; the free tier covers 3)
set -euo pipefail

: "${PROJECT_ID:?Set PROJECT_ID}"
REGION="${REGION:-australia-southeast1}"
REPO="restaurant"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/backend:$(date +%Y%m%d-%H%M%S)"
ENV_FILE="${ENV_FILE:-$(dirname "$0")/prod.env}"
SA_NAME="restaurant-runtime"
SA="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
SCHED_SA_NAME="restaurant-scheduler"
SCHED_SA="${SCHED_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"
CODE_ONLY="${1:-}"

gcloud config set project "$PROJECT_ID" >/dev/null

if [[ "$CODE_ONLY" != "--code-only" ]]; then
  echo "==> Enabling APIs"
  gcloud services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
    artifactregistry.googleapis.com cloudbuild.googleapis.com

  echo "==> Artifact Registry"
  gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
    gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"

  echo "==> Service accounts"
  gcloud iam service-accounts describe "$SA" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$SA_NAME" --display-name "Restaurant AI runtime"
  gcloud iam service-accounts describe "$SCHED_SA" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$SCHED_SA_NAME" --display-name "Restaurant AI scheduler"

  echo "==> Secrets from $ENV_FILE"
  [[ -f "$ENV_FILE" ]] || { echo "Missing $ENV_FILE (copy .env.example and fill it in)"; exit 1; }
  while IFS='=' read -r key value; do
    [[ -z "$key" || "$key" =~ ^# ]] && continue
    [[ -z "$value" ]] && continue
    name="$(echo "$key" | tr '[:upper:]_' '[:lower:]-')"
    if gcloud secrets describe "$name" >/dev/null 2>&1; then
      printf '%s' "$value" | gcloud secrets versions add "$name" --data-file=- >/dev/null
    else
      printf '%s' "$value" | gcloud secrets create "$name" --replication-policy automatic --data-file=- >/dev/null
    fi
    gcloud secrets add-iam-policy-binding "$name" --member "serviceAccount:$SA" \
      --role roles/secretmanager.secretAccessor >/dev/null
  done < "$ENV_FILE"
fi

# Build --set-secrets from whatever keys exist in the env file.
SECRETS=""
while IFS='=' read -r key value; do
  [[ -z "$key" || "$key" =~ ^# || -z "$value" ]] && continue
  name="$(echo "$key" | tr '[:upper:]_' '[:lower:]-')"
  SECRETS+="${key}=${name}:latest,"
done < "$ENV_FILE"
SECRETS="${SECRETS%,}"

echo "==> Building $IMAGE"
gcloud builds submit "$(dirname "$0")/../backend" --tag "$IMAGE" --region "$REGION"

COMMON=(--image "$IMAGE" --region "$REGION" --service-account "$SA" --set-secrets "$SECRETS"
        --set-env-vars "ENV=production,DEV_AUTH_ENABLED=false")

echo "==> Migrations"
gcloud run jobs deploy restaurant-migrate "${COMMON[@]}" --command alembic --args upgrade,head --max-retries 0
gcloud run jobs execute restaurant-migrate --region "$REGION" --wait

echo "==> API"
gcloud run deploy restaurant-api "${COMMON[@]}" --allow-unauthenticated \
  --cpu 1 --memory 512Mi --min-instances "${API_MIN_INSTANCES:-0}" --max-instances 3 --concurrency 40 --timeout 120

echo "==> Worker"
gcloud run deploy restaurant-worker "${COMMON[@]}" --no-allow-unauthenticated \
  --cpu 1 --memory 512Mi --min-instances 0 --max-instances 2 --concurrency 1 --timeout 90
gcloud run services add-iam-policy-binding restaurant-worker --region "$REGION" \
  --member "serviceAccount:$SCHED_SA" --role roles/run.invoker >/dev/null

WORKER_URL="$(gcloud run services describe restaurant-worker --region "$REGION" --format 'value(status.url)')"
API_URL="$(gcloud run services describe restaurant-api --region "$REGION" --format 'value(status.url)')"
INTERNAL_TOKEN="$(grep '^INTERNAL_TOKEN=' "$ENV_FILE" | cut -d= -f2-)"

if [[ "$CODE_ONLY" != "--code-only" ]]; then
  echo "==> Cloud Scheduler"
  for job in "run-jobs|* * * * *|/internal/run-jobs?time_budget_s=55" "tick|*/15 * * * *|/internal/tick"; do
    IFS='|' read -r name schedule path <<<"$job"
    args=(--location "$REGION" --schedule "$schedule" --uri "${WORKER_URL}${path}" --http-method POST
          --headers "X-Internal-Token=${INTERNAL_TOKEN}" --oidc-service-account-email "$SCHED_SA"
          --oidc-token-audience "$WORKER_URL" --attempt-deadline 90s --time-zone "Etc/UTC")
    gcloud scheduler jobs describe "restaurant-$name" --location "$REGION" >/dev/null 2>&1 \
      && gcloud scheduler jobs update http "restaurant-$name" "${args[@]}" \
      || gcloud scheduler jobs create http "restaurant-$name" "${args[@]}"
  done
fi

cat <<EOF

Done.
  API:     $API_URL
  Worker:  $WORKER_URL (private)

Next:
  - Set APP_BASE_URL=$API_URL in infra/prod.env and re-run if it changed.
  - Clover dashboard: OAuth redirect  $API_URL/oauth/clover/callback
                      Webhook URL     $API_URL/webhooks/clover
  - Google OAuth client redirect:     $API_URL/oauth/google/callback
  - Twilio inbound webhook (WhatsApp/SMS): $API_URL/webhooks/twilio
EOF
