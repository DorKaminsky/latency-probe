"""
Probe lifecycle management.

Design notes
------------
- asyncio.create_task per job: fits the scale of this service (tens of URLs,
  not millions). Each task yields control during httpx.AsyncClient.get(), so
  the event loop stays responsive. A heavier approach (Celery + Redis) would
  add operational overhead for no gain here.

- httpx.AsyncClient is created per-job rather than shared: each job has an
  independent timeout and can be cancelled cleanly without affecting others.

- resp.elapsed (from httpx) is the time spent waiting for the response body,
  measured by the HTTP library itself — more accurate than wrapping with
  time.perf_counter() because it excludes Python interpreter overhead.
"""

import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone

import httpx

from .models import ProbeResult

logger = logging.getLogger(__name__)

# Module-level dict: job_id -> asyncio.Task
# Simple enough for this scale; would move to Redis if we needed multi-instance
# job tracking or persistence across restarts.
_jobs: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]


def _emit(result: ProbeResult) -> None:
    """Write a structured log line to stdout (success) or stderr (error)."""
    line = (
        f"job={result.job_id} "
        f"url={result.url} "
        f"ts={result.timestamp} "
        f"status={result.status_code} "
        f"latency_ms={result.latency_ms} "
        f"error={result.error}"
    )
    if result.error:
        # Errors go to stderr so they can be separated in log aggregators
        # (e.g. CloudWatch, Loki) without regex filtering
        print(line, file=sys.stderr, flush=True)
        logger.error(line)
    else:
        print(line, file=sys.stdout, flush=True)
        logger.info(line)


async def _probe_loop(job_id: str, url: str, interval: float) -> None:
    # A single AsyncClient per job reuses the underlying TCP connection
    # (HTTP keep-alive) across measurements, reducing connection overhead
    # and giving more accurate application-level latency numbers.
    async with httpx.AsyncClient(
        timeout=10.0,  # hard ceiling so a hung target doesn't block the task forever
        follow_redirects=True,
    ) as client:
        while True:
            ts = datetime.now(timezone.utc).isoformat()
            status_code = None
            latency_ms = None
            error = None

            try:
                resp = await client.get(url)
                # resp.elapsed is set by httpx after the full response is received
                latency_ms = round(resp.elapsed.total_seconds() * 1000, 3)
                status_code = resp.status_code
            except httpx.TimeoutException:
                error = "timeout"
            except httpx.RequestError as exc:
                # Catches DNS failures, connection refused, SSL errors, etc.
                error = type(exc).__name__ + ": " + str(exc)

            _emit(
                ProbeResult(
                    job_id=job_id,
                    url=url,
                    timestamp=ts,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    error=error,
                )
            )

            # asyncio.sleep yields back to the event loop, allowing other
            # tasks (other probe jobs, incoming HTTP requests) to run
            await asyncio.sleep(interval)


def start_job(url: str, interval: float) -> str:
    job_id = str(uuid.uuid4())[:8]
    # create_task schedules the coroutine on the *running* event loop.
    # FastAPI/uvicorn provides that loop; we don't manage it ourselves.
    task = asyncio.create_task(_probe_loop(job_id, url, interval), name=job_id)
    _jobs[job_id] = task
    logger.info("started job=%s url=%s interval=%ss", job_id, url, interval)
    return job_id


def stop_job(job_id: str) -> bool:
    task = _jobs.pop(job_id, None)
    if task is None:
        return False
    # task.cancel() sends CancelledError into the coroutine at its next
    # await point (asyncio.sleep or httpx await), allowing clean teardown
    task.cancel()
    logger.info("stopped job=%s", job_id)
    return True


def list_jobs() -> list[str]:
    return list(_jobs.keys())
