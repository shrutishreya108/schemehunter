"""Settings loaded from .env (LLD §4 + v0.3 hackathon changes). Defaults are safe for local tests only."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# USD per million tokens. Check against the pricing page on platform.claude.com before the demo.
# Keys are model-id prefixes; the longest matching prefix wins (so dated ids like
# "claude-sonnet-4-5-20250929" resolve too).
PRICE_TABLE: dict[str, dict[str, float]] = {
    "claude-sonnet-4-5": {"input": 3.00, "output": 15.00, "cache_write": 3.75, "cache_read": 0.30},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00, "cache_write": 1.25, "cache_read": 0.10},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_BASE_URL: str = "http://localhost:8000"
    DATABASE_URL: str = ""
    SESSION_SECRET: str = "dev-only-session-secret-change-me"
    VAULT_KEY: str = ""
    ADMIN_PASSWORD: str = ""

    # Claude
    ANTHROPIC_API_KEY: str = ""
    MODEL_REASON: str = "claude-sonnet-4-5"
    MODEL_CHEAP: str = "claude-haiku-4-5"
    MAX_TOKENS_LOOP: int = 1200  # one agent-loop step
    MAX_TOKENS_LONG: int = 2500  # explain_match, report narrative
    MAX_STEPS_PER_RUN: int = 12
    DAILY_BUDGET_USD: float = 10.00  # over budget: only deadline-related runs proceed
    MAX_RUNS_PER_CASE_PER_DAY: int = 40
    BUDGET_TZ: str = "Asia/Kolkata"  # "today" for the budget and per-case caps
    SCAN_WITH_MODEL: bool = True  # outbound unknown-scheme check also asks MODEL_CHEAP
    CHAT_SYNC_TIMEOUT_S: float = 60
    CLAUDE_TIMEOUT_S: float = 30  # per API call; connect is capped at 5 s
    CLAUDE_MAX_RETRIES: int = 2
    CHAT_RUN_DEADLINE_S: float = 90  # wall clock for one chat run; past it the run ends as STUCK with a notice
    CLAUDE_JOB_DEADLINE_S: float = 300  # the same for worker brain_run jobs
    LOCK_TIMEOUT_S: int = 60  # waiting for the case lock; past it the run ends as ERROR with a notice
    STALE_RUN_MIN: int = 10  # a run row still open after this long is closed by housekeeping

    # Scheme discovery (myScheme, via API Setu). "demo" shows the rulebook labelled as demo data.
    # "live" uses MySchemeApiProvider, which refuses to run until every MYSCHEME_* value below is set.
    DISCOVERY_MODE: str = "demo"
    DISCOVERY_FALLBACK_TO_DEMO: bool = False  # if live fails, show demo data with the reason (never silently)
    MYSCHEME_BASE_URL: str = ""
    MYSCHEME_SEARCH_PATH: str = ""
    MYSCHEME_CLIENT_ID: str = ""
    MYSCHEME_API_KEY: str = ""
    MYSCHEME_CLIENT_ID_HEADER: str = ""  # header name for the client id: from the official documentation
    MYSCHEME_API_KEY_HEADER: str = ""  # header name for the API key: from the official documentation
    MYSCHEME_QUERY_MAP: str = (
        ""  # JSON: our profile field -> the API's parameter name (only mapped fields are sent)
    )
    MYSCHEME_RESULTS_KEY: str = ""  # the key holding the list of schemes in the response
    MYSCHEME_FIELD_MAP: str = ""  # JSON: our field (name, id, department, ...) -> the API's field name
    MYSCHEME_CONNECT_TIMEOUT_S: float = 5
    MYSCHEME_READ_TIMEOUT_S: float = 10

    DEMO_CLOCK_SCALE: float = 1

    # Email: SMTP through the Titan Business Email mailbox on our domain.
    # smtpout.secureserver.net is GoDaddy's relay for Titan mailboxes bought on godaddy.com;
    # smtp.titan.email is the alternative host. Username is the FULL email address.
    SMTP_HOST: str = "smtpout.secureserver.net"
    SMTP_PORT: int = 587
    SMTP_SECURITY: str = "starttls"  # starttls (falls back to SSL on 465) | ssl | none (local tests only)
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TIMEOUT_S: float = 20
    EMAIL_FROM: str = ""
    CONTACT_TO: str = ""
    CURATOR_ALERT_EMAIL: str = ""
    # Email sign-in (LLD signin §3)
    SIGNIN_CODE_TTL_MIN: int = 10
    SIGNIN_MAX_ATTEMPTS: int = 5
    SIGNIN_RESEND_COOLDOWN_S: int = 60
    SIGNIN_EMAILS_PER_EMAIL_PER_HOUR: int = 5
    SIGNIN_EMAILS_PER_IP_PER_HOUR: int = 10
    SIGNIN_EMAILS_PER_DAY: int = 100
    SIGNIN_ADMIN_LINK_TTL_MIN: int = 60

    FEATURE_DOMAIN_OFFER: bool = False
    GODADDY_API_KEY: str = ""
    GODADDY_API_SECRET: str = ""
    RETENTION_DAYS: int = 90

    VAULT_DIR: Path = Path("/data/vault")
    RULEBOOK_SOURCES_DIR: Path = ROOT / "rulebook" / "sources"
    WEB_FETCH_ENABLED: bool = True  # VPS sets false: it serves committed snapshots and never opens a connection
    WEB_CACHE_TTL_H: int = 24
    WEB_SNAPSHOT_WARN_DAYS: int = 30
    RUN_MIGRATIONS_ON_STARTUP: bool = True
    TRUST_PROXY: bool = True  # behind Caddy: client IP from X-Forwarded-For


@lru_cache
def get_settings() -> Settings:
    return Settings()
