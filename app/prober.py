"""
Probe lifecycle management.

Design notes
------------
- asyncio.create_task per job: fits the scale of this service (tens of URLs).
  Each task yields control during httpx.AsyncClient.get(), so the event loop
  stays responsive.
- httpx.AsyncClient is created per-job so each job has an independent timeout
  and can be cancelled cleanly without affecting others.
- resp.elapsed (from httpx) is the time the HTTP library measures for the
  response, which is more accurate than wrapping with time.perf_counter().
- SSRF: the caller-supplied hostname is resolved once in the route handler.
  The probe loop additionally re-checks the resolved IP on every request via
  a custom httpx transport, defending against DNS rebinding.
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
from .security import SSRFError, is_private_ip

logger = logging.getLogger(__name__)

_jobs: dict[str, asyncio.Task] = {}  # type: ignore[type-arg]
_results: dict[str, deque[ProbeResult]] = {}
_RESULTS_MAXLEN = 100

# Prometheus metrics
# Deliberately NOT labelled by job_id — that would create an unbounded label
# cardinality (new UUID per job, never cleaned up in Prometheus). URL is the
# meaningful dimension for alerting.
probe_requests_total = Counter(
    "probe_requests_total",
    "Total number of probe attempts",
    ["url", "status_code"],
)
probe_latency_seconds = Histogram(
    "probe_latency_seconds",
    "HTTP response latency per probe",
    ["url"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


class _SSRFGuardTransport(httpx.AsyncHTTPTransport):
    """httpx transport that re-checks the destination IP on every request.

    Defends against DNS rebinding: even if the hostname passed validation,
    the resolved IP could change to a private address later. We resolve
    again here (getaddrinfo is cached briefly by the OS) and refuse to send
    the request if it now resolves to a private range.
    """

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        loop = asyncio.get_running_loop()
        try:
            import socket

            infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise httpx.ConnectError(f"DNS lookup failed: {exc}") from exc
        for info in infos:
            if is_private_ip(info[4][0]):
                raise httpx.ConnectError(
                    f"blocked: {host} resolves to private IP {info[4][0]}"
                )
        return await super().handle_async_request(request)


def _emit(result: ProbeResult) -> None:
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
    async with httpx.AsyncClient(
        transport=_SSRFGuardTransport(),
        timeout=10.0,
        # follow_redirects=False: a 302 to a private IP would bypass the SSRF check
        follow_redirects=False,
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

            probe_requests_total.labels(
                url=url,
                status_code=str(status_code) if status_code else "error",
            ).inc()
            if latency_ms is not None:
                probe_latency_seconds.labels(url=url).observe(latency_ms / 1000)

            await asyncio.sleep(interval)


def start_job(url: str, interval: float) -> str:
    job_id = str(uuid.uuid4())[:8]
    _results[job_id] = deque(maxlen=_RESULTS_MAXLEN)
    task = asyncio.create_task(_probe_loop(job_id, url, interval), name=job_id)
    # If the task dies unexpectedly, clean up _results too.
    task.add_done_callback(
        lambda t: (
            _results.pop(job_id, None) if t.done() and job_id not in _jobs else None
        )
    )
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


def get_job_url(job_id: str) -> str | None:
    """Return the URL for a running job, or None if it doesn't exist."""
    buf = _results.get(job_id)
    if buf is None or not buf:
        return None
    # All results in the deque have the same url; last is cheapest to peek at
    return buf[-1].url


# Re-export for the route handler
__all__ = [
    "SSRFError",
    "get_results",
    "list_jobs",
    "start_job",
    "stop_job",
]
