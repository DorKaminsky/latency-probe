"""
FastAPI application entry point.

Routing design
--------------
POST   /probe                    → create a new probe job
DELETE /probe/{job_id}           → stop a running job
GET    /probe                    → list all running job IDs
GET    /probe/{job_id}/results   → last N measurements for a job
GET    /metrics                  → Prometheus metrics (scrape endpoint)
GET    /health                   → liveness check (used by k8s readinessProbe)
"""

import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .models import ProbeRequest, ProbeResponse, ProbeResult
from .prober import get_results, list_jobs, start_job, stop_job

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(
    title="latency-probe",
    version="1.0.0",
    description="Measures HTTP response latency of remote resources on a configurable interval",
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
