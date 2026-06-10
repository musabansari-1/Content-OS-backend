from __future__ import annotations

import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_LOADED = False


def load_env_file() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return

    env_path = BACKEND_DIR / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())

    _ENV_LOADED = True


def env(name: str, default: str | None = None) -> str | None:
    load_env_file()
    return os.getenv(name, default)


def require_env(name: str, message: str | None = None) -> str:
    value = env(name)
    if value is None or not value.strip():
        raise RuntimeError(message or f"{name} is not set.")
    return value.strip()


def get_database_url() -> str:
    database_url = env("DATABASE_URL") or env("NEON_DATABASE_URL")
    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Neon Postgres connection string to backend/.env."
        )
    return database_url


def get_allowed_origins() -> list[str]:
    allowed_origins = {
        "http://localhost:5173",
        "http://localhost:3000",
        "https://content-os-frontend.vercel.app",
    }

    configured_origins = (env("CORS_ALLOWED_ORIGINS", "") or "").strip()
    if configured_origins:
        allowed_origins.update(
            origin.strip() for origin in configured_origins.split(",") if origin.strip()
        )

    frontend_url = (env("FRONTEND_URL", "") or "").strip()
    if frontend_url:
        allowed_origins.add(frontend_url)

    uptime_robot_url = (env("UPTIMEROBOT_URL", "") or "").strip()
    if uptime_robot_url:
        allowed_origins.add(uptime_robot_url)

    return sorted(allowed_origins)


def get_allowed_origin_regex() -> str | None:
    return r"^https://.*\.vercel\.app$|^https://.*\.onrender\.com$|^http://localhost:\d+$"


load_env_file()
