"""Application settings, read from environment variables (or a .env file in development)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # Monthly AI budget per location in US$; above it the agent stays on the default (cheap) model.
    monthly_ai_budget_usd: float = 5.0

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

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
