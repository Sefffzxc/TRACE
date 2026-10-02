"""Application configuration.

All settings are read from environment variables (or a local .env file) so the
same code runs unchanged on a laptop (SQLite) and on a server (PostgreSQL).
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _with_scheme(value: str) -> str:
    """``lbca-ai.onrender.com`` -> ``https://lbca-ai.onrender.com``.

    These values are pasted in by hand when a blueprint is applied, and people
    reasonably paste a hostname rather than a URL. Anything that already names a
    scheme is left exactly as it is, so a local ``http://localhost:8001`` keeps
    working.
    """
    value = value.strip().rstrip("/")
    if not value or "://" in value:
        return value
    if value.startswith("localhost") or value.startswith("127.0.0.1"):
        return f"http://{value}"
    return f"https://{value}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── Identity ──────────────────────────────────────────────────────────
    APP_NAME: str = "LBCA Monitoring System API"
    APP_VERSION: str = "2.0.0"
    SCHOOL_NAME: str = "Lord's Blessing Christian Academy"
    SCHOOL_SHORT_NAME: str = "LBCA"

    # ── Database ──────────────────────────────────────────────────────────
    # SQLite by default so the project runs with zero setup.
    # For PostgreSQL set:
    #   DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/lbca
    DATABASE_URL: str = "sqlite+aiosqlite:///./lbca.db"

    # ── Security ──────────────────────────────────────────────────────────
    SECRET_KEY: str = "change-me-in-production-please-use-a-long-random-string"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Failed-login lockout ladder (minutes per successive lockout)
    MAX_LOGIN_ATTEMPTS: int = 5
    LOCKOUT_LADDER_MINUTES: str = "5,15,60"
    MAX_LOCKOUTS: int = 3

    # ── Two-factor / OTP ──────────────────────────────────────────────────
    OTP_EXPIRY_MINUTES: int = 10
    OTP_MAX_ATTEMPTS: int = 5
    TWO_FACTOR_DAYS_INTERVAL: int = 90
    # In development the OTP is returned in the response and printed to the
    # console because there is no mail server wired up.
    DEBUG_OTP: bool = True

    # ── Password reset ────────────────────────────────────────────────────
    RESET_TOKEN_EXPIRE_MINUTES: int = 30
    RESET_MAX_REQUESTS_PER_HOUR: int = 5

    # ── CORS ──────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: str = (
        "http://localhost:5173,http://127.0.0.1:5173,"
        "http://localhost:4173,http://127.0.0.1:4173,"
        "http://localhost:3000"
    )

    # ── Uploads ───────────────────────────────────────────────────────────
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_BYTES: int = 4 * 1024 * 1024  # 4 MB

    # ── AI risk-classification service ────────────────────────────────────
    # The trained model lives in its own service (see ../ai-service). This API
    # calls it over HTTP and stores the answer; if the call fails the
    # rule-based engine in app/services/risk.py takes over and the stored row
    # is marked `source="fallback"` so a fallback is never mistaken for a
    # model prediction.
    AI_ENABLED: bool = True
    # May be given as a bare hostname; see `ai_service_url`.
    AI_SERVICE_URL: str = "http://localhost:8001"
    AI_SERVICE_TOKEN: str = ""
    # Kept deliberately short: saving a PACE record must never wait on the
    # model. On Render's free tier an idle service takes ~1 minute to wake, so
    # the first save after an idle period is expected to fall back.
    AI_TIMEOUT_SECONDS: float = 6.0
    # Used only by the explicit warm-up / re-score endpoints, where the user
    # has asked for the model and a wait is acceptable.
    AI_WAKE_TIMEOUT_SECONDS: float = 90.0
    # How many records go to the model in one request. The service accepts up
    # to 500, but a free instance has 512 MB and 0.1 CPU, and a smaller request
    # keeps each round trip well inside the platform's request timeout even
    # when the whole school is re-scored at once.
    AI_BATCH_SIZE: int = 100

    @field_validator("AI_SERVICE_URL", mode="after")
    @classmethod
    def _normalise_ai_url(cls, value: str) -> str:
        """Accept a bare hostname, because that is all Render can inject."""
        return _with_scheme(value)

    @property
    def cors_origins(self) -> List[str]:
        """Allowed origins, with a scheme added where one is missing.

        This is typed in by hand at deployment time, and ``lbca-web.onrender.com``
        is the obvious thing to paste. Treating a bare host as https avoids a
        CORS failure that would otherwise look like a broken API.
        """
        return [_with_scheme(o.strip()) for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def lockout_ladder(self) -> List[int]:
        return [int(x) for x in self.LOCKOUT_LADDER_MINUTES.split(",") if x.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")

    @property
    def sqlalchemy_url(self) -> str:
        """DATABASE_URL in the form SQLAlchemy's async engine needs.

        Managed hosts (Render, Heroku, Supabase, Neon) hand out a URL like
        ``postgres://user:pass@host/db?sslmode=require``. Three things about it
        do not work here, so they are fixed once, in one place, rather than
        asking whoever deploys this to rewrite the value by hand:

        * the scheme has to name the async driver — ``postgresql+asyncpg``;
        * ``postgres://`` is an old alias SQLAlchemy no longer accepts;
        * ``sslmode`` is libpq's spelling and asyncpg rejects it, so it is
          removed here and the TLS setting is passed through connect_args
          instead (see ``db_connect_args``).
        """
        url = self.DATABASE_URL.strip()
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                url = "postgresql+asyncpg://" + url[len(prefix):]
                break
        if "+asyncpg" in url:
            url = self._strip_query_keys(url, {"sslmode", "ssl", "channel_binding"})
        return url

    @property
    def db_connect_args(self) -> dict:
        if self.is_sqlite:
            return {"check_same_thread": False}
        # Any managed Postgres reachable over the public internet requires TLS;
        # Render's internal hostname does not, but asking for it is harmless.
        if "sslmode=disable" in self.DATABASE_URL:
            return {}
        return {"ssl": True} if self._wants_ssl else {}

    @property
    def _wants_ssl(self) -> bool:
        lowered = self.DATABASE_URL.lower()
        if "sslmode=" in lowered:
            return "sslmode=disable" not in lowered
        # An internal Render hostname has no dots; a public one does.
        host = lowered.split("@")[-1].split("/")[0].split(":")[0]
        return "." in host and host not in {"localhost", "127.0.0.1"}

    @staticmethod
    def _strip_query_keys(url: str, keys: set) -> str:
        if "?" not in url:
            return url
        base, _, query = url.partition("?")
        kept = [
            part
            for part in query.split("&")
            if part and part.split("=", 1)[0].lower() not in keys
        ]
        return f"{base}?{'&'.join(kept)}" if kept else base


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Make sure the upload folders exist on import.
os.makedirs(os.path.join(settings.UPLOAD_DIR, "profile_pics"), exist_ok=True)
