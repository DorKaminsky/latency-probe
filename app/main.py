"""
FastAPI application entry point.

Routing design
--------------
POST   /probe                    → create a new probe job
DELETE /probe/{job_id}           → stop a running job
GET    /probe                    → list all running job IDs
GET    /probe/{job_id}/results   → last N measurements for a job
GET    /probe/{job_id}/analyze   → LLM diagnosis of recent measurements
GET    /metrics                  → Prometheus metrics (scrape endpoint)
GET    /health                   → liveness check (used by k8s readinessProbe)
"""

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .analyze import AnalyzeError, analyze
from .models import ProbeRequest, ProbeResponse, ProbeResult
from .prober import get_job_url, get_results, list_jobs, start_job, stop_job
from .security import SSRFError, resolve_and_check

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="latency-probe",
    version="1.0.0",
    description=(
        "Measures HTTP response latency of remote resources on a configurable interval"
    ),
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics() -> PlainTextResponse:
    """Prometheus scrape endpoint."""
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/probe", response_model=ProbeResponse, status_code=201)
async def create_probe(req: ProbeRequest) -> ProbeResponse:
    """Start a new latency measurement job."""
    url = str(req.url)
    try:
        await resolve_and_check(url)
    except SSRFError as exc:
        # 422 keeps validation errors consistent with Pydantic
        raise HTTPException(status_code=422, detail=str(exc))
    job_id = start_job(url, req.interval_seconds)
    return ProbeResponse(
        job_id=job_id,
        url=url,
        interval_seconds=req.interval_seconds,
        status="running",
    )


@app.delete("/probe/{job_id}", status_code=204)
async def delete_probe(job_id: str) -> None:
    """Stop a running probe job."""
    if not stop_job(job_id):
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")


@app.get("/probe")
async def get_probes() -> dict:
    """List all currently running job IDs."""
    return {"jobs": list_jobs()}


@app.get("/probe/{job_id}/results", response_model=list[ProbeResult])
async def probe_results(
    job_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ProbeResult]:
    """Return the last N measurements for a running job."""
    results = get_results(job_id, limit)
    if results is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    return results


@app.get("/probe/{job_id}/analyze")
async def probe_analyze(job_id: str) -> dict:
    """Return an LLM-generated diagnosis of the job's recent measurements.

    Requires ANTHROPIC_API_KEY. Results cached for 60s per job.
    """
    url = get_job_url(job_id)
    results = get_results(job_id, limit=100)
    if url is None or results is None:
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")
    try:
        diagnosis = await analyze(job_id, url, results)
    except AnalyzeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return {
        "job_id": job_id,
        "url": url,
        "sample_size": len(results),
        "diagnosis": diagnosis,
    }
