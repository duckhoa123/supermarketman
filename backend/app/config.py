"""Application settings, read from environment variables (or a .env file in development)."""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.db_url import to_async_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    # --- Core -------------------------------------------------------------
    env: str = "development"  # development | test | production
    app_base_url: str = "http://localhost:8000"  # public URL of the API (OAuth redirects, webhooks)
    web_app_url: str = "http://localhost:5173"  # where to send owners back after OAuth
    cors_origins: str = "http://localhost:5173,http://localhost:8081"
    database_url: str = "postgresql+asyncpg://restaurant:restaurant@localhost:5432/restaurant"
    db_pool_size: int = 5

    # 32-byte url-safe base64 Fernet key used to encrypt POS / Google tokens at rest.
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    token_encryption_key: str = ""

    # --- Auth (Supabase) --------------------------------------------------
    supabase_url: str = ""  # e.g. https://abcd.supabase.co
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_secret: str = ""  # only for older projects that still use the legacy HS256 secret
    # Development only: accept "Authorization: Bearer dev:<email>" so the app runs with no Supabase project.
    dev_auth_enabled: bool = True

    # --- Internal endpoints (Cloud Scheduler / worker) ---------------------
    internal_token: str = "change-me"  # shared secret; in production also restricted by Cloud Run IAM

    # --- Claude API -------------------------------------------------------
    anthropic_api_key: str = ""
    model_default: str = "claude-haiku-5-5"
    model_deep: str = "claude-sonnet-5-5"
    agent_max_tool_rounds: int = 6
    agent_max_output_tokens: int = 1500
    # Lets a signed-in user add a "Demo Bistro" with sample data (onboarding and Settings). Turn off once live.
    demo_data_enabled: bool = True
    # Monthly AI budget per location in US$; above it the agent stays on the default (cheap) model.
    monthly_ai_budget_usd: float = 5.0
    # Customers and drivers chat without a restaurant, so their budget is per person.
    personal_monthly_ai_budget_usd: float = 0.5

    # --- Ecosystem: platform staff, ordering and the in-house delivery fleet --------------------
    # Emails (comma separated) allowed to invite drivers and see the fleet dashboard.
    platform_admin_emails: str = ""
    # Delivery hub and service radius. Drivers are employees, so deliveries stay inside one compact area.
    delivery_hub_lat: float = 49.2634  # Main St & Broadway, Mount Pleasant, Vancouver
    delivery_hub_lng: float = -123.1007
    delivery_radius_km: float = 20.0
    delivery_base_fee_cents: int = 399  # charged to the customer up to delivery_base_km
    delivery_base_km: float = 3.0
    delivery_per_km_cents: int = 75  # each extra (started) km beyond the base distance
    # Fee the restaurant pays per order, in basis points of the food subtotal (600 = 6%).
    # BC caps delivery-company fees to restaurants at 20% of the order value; the code never charges more.
    restaurant_fee_pickup_bp: int = 600
    restaurant_fee_delivery_bp: int = 1100
    driver_max_active_deliveries: int = 2
    # When waiting + in-progress deliveries per on-shift driver reach this, on-call drivers are alerted.
    busy_deliveries_per_driver: float = 2.5
    default_driver_wage_cents: int = 2000  # CAD/hour; BC general minimum wage is $18.25 from June 2026

    # --- Forecasting -------------------------------------------------------
    # Open-Meteo's free API is for non-commercial use; in production weather is only fetched with a paid key.
    open_meteo_api_key: str = ""

    # --- Receiving: scanned invoices ----------------------------------------
    invoice_model: str = ""  # model that reads invoices; empty = MODEL_DEFAULT
    invoice_max_mb: float = 10.0
    invoice_file_days: int = 120  # invoice images are deleted after this; the data is kept
    price_change_threshold_pct: float = 3.0  # default for the price_change alert rule

    # --- Payments (Stripe) ---------------------------------------------------
    stripe_secret_key: str = ""  # empty: orders are marked as test payments, nothing is charged

    # --- Clover -----------------------------------------------------------
    clover_app_id: str = ""
    clover_app_secret: str = ""
    # Sandbox: https://apisandbox.dev.clover.com / https://sandbox.dev.clover.com
    # Production, North America (US and Canada): https://api.clover.com / https://www.clover.com
    clover_api_base: str = "https://apisandbox.dev.clover.com"
    clover_web_base: str = "https://sandbox.dev.clover.com"
    clover_webhook_auth_code: str = ""  # "Clover Auth Code" from the app's Webhooks settings

    # --- Square (phase 2) -------------------------------------------------
    square_app_id: str = ""
    square_app_secret: str = ""

    # --- Google Business Profile -------------------------------------------
    google_client_id: str = ""
    google_client_secret: str = ""

    # --- Notifications ----------------------------------------------------
    resend_api_key: str = ""
    email_from: str = "Restaurant AI <alerts@example.com>"
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. whatsapp:+61400000000
    twilio_sms_from: str = ""
    twilio_whatsapp_content_sid: str = ""  # approved WhatsApp template for alerts (variable 1 = text)
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:alerts@example.com"

    @field_validator("database_url")
    @classmethod
    def _async_driver(cls, value: str) -> str:
        # Railway provides postgresql://...; SQLAlchemy needs the asyncpg driver name.
        return to_async_url(value)

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def platform_admins(self) -> set[str]:
        return {e.strip().lower() for e in self.platform_admin_emails.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
