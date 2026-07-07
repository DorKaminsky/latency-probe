"""
LLM-powered anomaly summary for probe results.

Feeds recent measurements from a job's ring buffer to Claude Haiku 4.5 and
returns a short natural-language diagnosis. Results cached per-job for 60s.

Design notes
------------
- Haiku is the right tier: <100 data points, ~200-token response,
  latency-sensitive (called on-demand from a UI). Opus would be overkill.
- Cache is in-process (dict); same scale-out story as the job registry.
- Failure isolation: if ANTHROPIC_API_KEY is missing or the API is down,
  the endpoint returns 503 with a clear message — probing keeps working.
- Cache invalidation on stop_job is skipped — 60s TTL naturally handles it.
"""

import logging
import os
import time

from anthropic import APIError, AsyncAnthropic

from .models import ProbeResult

logger = logging.getLogger(__name__)

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 300
_CACHE_TTL_SECONDS = 60
_MAX_INPUT_RESULTS = 100

# job_id -> (expires_at, response_text)
_cache: dict[str, tuple[float, str]] = {}


class AnalyzeError(Exception):
    """Raised when the LLM call fails or is unavailable."""


def _client() -> AsyncAnthropic:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise AnalyzeError(
            "ANTHROPIC_API_KEY not set — /analyze requires an Anthropic API key"
        )
    return AsyncAnthropic()


def _format_results(results: list[ProbeResult]) -> str:
    lines = ["timestamp,status,latency_ms,error"]
    for r in results[-_MAX_INPUT_RESULTS:]:
        lines.append(
            f"{r.timestamp},{r.status_code or 'N/A'},"
            f"{r.latency_ms if r.latency_ms is not None else 'N/A'},"
            f"{r.error or ''}"
        )
    return "\n".join(lines)


_SYSTEM_PROMPT = (
    "You are a site reliability engineer analysing HTTP latency probe results. "
    "Given a CSV of recent measurements, produce a 2-3 sentence diagnosis. "
    "Call out: overall latency baseline, notable spikes or outliers, error "
    "patterns, and whether the pattern looks healthy, degraded, or broken. "
    "Be terse — this is going into an operator dashboard, not a report."
)


async def analyze(job_id: str, url: str, results: list[ProbeResult]) -> str:
    """Return an LLM summary of the job's recent measurements.

    Cached per-job for 60s. Raises AnalyzeError on API failure or missing key.
    """
    if not results:
        raise AnalyzeError(
            "no measurements yet for this job — wait for the probe to run"
        )

    cached = _cache.get(job_id)
    if cached and cached[0] > time.time():
        return cached[1]

    prompt = (
        f"URL under test: {url}\n"
        f"Measurements ({len(results)} rows):\n\n{_format_results(results)}"
    )

    try:
        response = await _client().messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
    except APIError as exc:
        logger.error("anthropic API error for job=%s: %s", job_id, exc)
        raise AnalyzeError(f"LLM call failed: {exc}") from exc

    text = next(
        (block.text for block in response.content if block.type == "text"),
        "",
    ).strip()
    if not text:
        raise AnalyzeError("LLM returned no text content")

    _cache[job_id] = (time.time() + _CACHE_TTL_SECONDS, text)
    logger.info(
        "analyzed job=%s tokens_in=%d tokens_out=%d",
        job_id,
        response.usage.input_tokens,
        response.usage.output_tokens,
    )
    return text


def clear_cache() -> None:
    """Test helper — reset the in-process cache."""
    _cache.clear()
