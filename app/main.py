import logging
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.auth import router as auth_router
from app.api.routes.generation import GENERATED_CLIPS_DIR, router as generation_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.system import router as system_router
from app.api.routes.voice_profiles import router as voice_profiles_router
from app.api.services import creator_voice_profile_service
from app.voice_engine.db import run_migrations


BACKEND_DIR = Path(__file__).resolve().parents[1]


def _load_env_file() -> None:
    env_path = BACKEND_DIR / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _get_allowed_origins() -> list[str]:
    _load_env_file()
    allowed_origins = {
        "http://localhost:5173",
        "http://localhost:3000",
        "https://content-os-frontend.vercel.app",
    }

    configured_origins = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
    if configured_origins:
        allowed_origins.update(
            origin.strip() for origin in configured_origins.split(",") if origin.strip()
        )

    frontend_url = os.getenv("FRONTEND_URL", "").strip()
    if frontend_url:
        allowed_origins.add(frontend_url)

    uptime_robot_url = os.getenv("UPTIMEROBOT_URL", "").strip()
    if uptime_robot_url:
        allowed_origins.add(uptime_robot_url)

    return sorted(allowed_origins)


def _get_allowed_origin_regex() -> str | None:
    return r"^https://.*\.vercel\.app$|^https://.*\.onrender\.com$|^http://localhost:\d+$"


app = FastAPI()
app.mount("/generated-clips", StaticFiles(directory=str(GENERATED_CLIPS_DIR)), name="generated-clips")

logging.getLogger(__name__).warning("CORS middleware is configured to allow all origins.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(generation_router)
app.include_router(voice_profiles_router)
app.include_router(integrations_router)
app.include_router(system_router)


@app.on_event("startup")
def startup() -> None:
    run_migrations()
    creator_voice_profile_service.repairStoredPreferredDevices()
