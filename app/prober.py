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
from collections import deque
from datetime import datetime, timezone

import httpx
from prometheus_client import Counter, Histogram

from .models import ProbeResult

logger = logging.getLogger(__name__)

# Module-level dicts: job_id -> asyncio.Task / recent results
# Simple enough for this scale; would move to Redis if we needed multi-instance
# job tracking or persistence across restarts.
_jobs: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
_results: dict[str, deque[ProbeResult]] = {}
_RESULTS_MAXLEN = 100

# Prometheus metrics — labelled by job_id so operators can alert per-target
probe_requests_total = Counter(
    "probe_requests_total",
    "Total number of probe attempts",
    ["job_id", "url", "status_code"],
)
probe_latency_seconds = Histogram(
    "probe_latency_seconds",
    "HTTP response latency per probe",
    ["job_id", "url"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


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
        timeout=10.0,
        follow_redirects=True,
    ) as client:
        while True:
            ts = datetime.now(timezone.utc).isoformat()
            status_code = None
            latency_ms = None
            error = None

            try:
                resp = await client.get(url)
                latency_ms = round(resp.elapsed.total_seconds() * 1000, 3)
                status_code = resp.status_code
            except httpx.TimeoutException:
                error = "timeout"
            except httpx.RequestError as exc:
                error = type(exc).__name__ + ": " + str(exc)

            result = ProbeResult(
                job_id=job_id,
                url=url,
                timestamp=ts,
                status_code=status_code,
                latency_ms=latency_ms,
                error=error,
            )
            _emit(result)
            if job_id in _results:
                _results[job_id].append(result)

            # Update Prometheus metrics
            probe_requests_total.labels(
                job_id=job_id,
                url=url,
                status_code=str(status_code) if status_code else "error",
            ).inc()
            if latency_ms is not None:
                probe_latency_seconds.labels(job_id=job_id, url=url).observe(
                    latency_ms / 1000
                )

            await asyncio.sleep(interval)


def start_job(url: str, interval: float) -> str:
    job_id = str(uuid.uuid4())[:8]
    _results[job_id] = deque(maxlen=_RESULTS_MAXLEN)
    task = asyncio.create_task(_probe_loop(job_id, url, interval), name=job_id)
    _jobs[job_id] = task
    logger.info("started job=%s url=%s interval=%ss", job_id, url, interval)
    return job_id


def stop_job(job_id: str) -> bool:
    task = _jobs.pop(job_id, None)
    if task is None:
        return False
    _results.pop(job_id, None)
    task.cancel()
    logger.info("stopped job=%s", job_id)
    return True


def list_jobs() -> list[str]:
    return list(_jobs.keys())


def get_results(job_id: str, limit: int = 20) -> list[ProbeResult] | None:
    buf = _results.get(job_id)
    if buf is None:
        return None
    return list(buf)[-limit:]
