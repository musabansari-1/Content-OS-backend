import threading
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable

from app.core.config import env


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


logger = logging.getLogger(__name__)


class GenerationJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        max_workers = max(1, int(env("GENERATION_JOB_WORKERS", "1") or "1"))
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="generation-job")

    def create_job(self, user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        now = _utcnow_iso()
        job = {
            "id": job_id,
            "user_id": user_id,
            "status": "queued",
            "progress_percent": 2,
            "stage": "queued",
            "message": "Queued to begin.",
            "detail": "Your request is lined up and ready to start.",
            "result": None,
            "error": None,
            "payload": payload,
            "created_at": now,
            "updated_at": now,
            "steps": [
                {"key": "source", "label": "Getting ready", "status": "pending"},
                {"key": "moments", "label": "Understanding input", "status": "pending"},
                {"key": "strategy", "label": "Preparing content", "status": "pending"},
                {"key": "execution", "label": "Creating results", "status": "pending"},
                {"key": "finalize", "label": "Wrapping up", "status": "pending"},
            ],
            "asset_progress": [],
        }
        with self._lock:
            self._jobs[job_id] = job
        return self._public_job(job)

    def start_job(self, job_id: str, runner: Callable[[], dict[str, Any]]) -> None:
        self._executor.submit(self._run_job, job_id, runner)

    def _run_job(self, job_id: str, runner: Callable[[], dict[str, Any]]) -> None:
        self.update_job(
            job_id,
            status="running",
            progress_percent=5,
            stage="starting",
            message="Generation started.",
            detail="We are setting everything up.",
        )
        try:
            result = runner()
            self.update_job(
                job_id,
                status="completed",
                progress_percent=100,
                stage="completed",
                message="Your content is ready.",
                detail="Everything finished successfully.",
                result=result,
                error=None,
                steps=[
                    {"key": "source", "label": "Getting ready", "status": "completed"},
                    {"key": "moments", "label": "Understanding input", "status": "completed"},
                    {"key": "strategy", "label": "Preparing content", "status": "completed"},
                    {"key": "execution", "label": "Creating results", "status": "completed"},
                    {"key": "finalize", "label": "Wrapping up", "status": "completed"},
                ],
            )
        except Exception as error:
            logger.exception("Generation job failed: job_id=%s", job_id)
            self.update_job(
                job_id,
                status="failed",
                stage="failed",
                message="Generation stopped before completion.",
                detail=str(error),
                error=str(error),
            )

    def update_job(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.update(changes)
            job["updated_at"] = _utcnow_iso()

    def get_job(self, job_id: str, user_id: int) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job["user_id"] != user_id:
                return None
            return self._public_job(job)

    def get_job_snapshot(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return self._public_job(job)

    def _public_job(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": job["id"],
            "status": job["status"],
            "progress_percent": job["progress_percent"],
            "stage": job["stage"],
            "message": job["message"],
            "detail": job["detail"],
            "result": job["result"],
            "error": job["error"],
            "created_at": job["created_at"],
            "updated_at": job["updated_at"],
            "steps": [dict(step) for step in job.get("steps", [])],
            "asset_progress": [dict(asset) for asset in job.get("asset_progress", [])],
        }


generation_job_store = GenerationJobStore()
