import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.auth import router as auth_router
from app.api.routes.billing import router as billing_router
from app.api.routes.generation import GENERATED_CLIPS_DIR, router as generation_router
from app.api.routes.integrations import router as integrations_router
from app.api.routes.system import router as system_router
from app.api.routes.voice_profiles import router as voice_profiles_router
from app.api.services import creator_voice_profile_service
from app.core.db import run_migrations


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
app.include_router(billing_router)
app.include_router(generation_router)
app.include_router(voice_profiles_router)
app.include_router(integrations_router)
app.include_router(system_router)


@app.on_event("startup")
def startup() -> None:
    run_migrations()
    creator_voice_profile_service.repairStoredPreferredDevices()
