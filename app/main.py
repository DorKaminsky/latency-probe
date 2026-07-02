"""
FastAPI application entry point.

Routing design
--------------
POST   /probe           → create a new probe job
DELETE /probe/{job_id}  → stop a running job
GET    /probe           → list all running job IDs
GET    /health          → liveness check (used by k8s readinessProbe)
"""

import logging

from fastapi import FastAPI, HTTPException

from .models import ProbeRequest, ProbeResponse
from .prober import list_jobs, start_job, stop_job

# basicConfig here means the format is applied to all loggers in the process,
# including uvicorn's access logger. The format is kept machine-parseable
# (key=value style) to make grep and log-aggregation easy.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

# FastAPI was chosen over Flask/Django because:
# 1. Async-native: plays well with asyncio probe tasks on the same event loop
# 2. Automatic OpenAPI docs at /docs — useful for demo and reviewers
# 3. Pydantic v2 validation built-in — no separate serialization layer needed
app = FastAPI(
    title="latency-probe",
    version="1.0.0",
    description="Measures HTTP response latency of remote resources on a configurable interval",
)


@app.get("/health")
async def health() -> dict:
    # Returns 200 as long as the process is alive.
    # k8s readinessProbe hits this; if it fails, traffic is routed away.
    return {"status": "ok"}


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
        # 404 instead of 200 for idempotency clarity: if the job doesn't exist,
        # the caller should know so they can update their state
        raise HTTPException(status_code=404, detail=f"job '{job_id}' not found")


@app.get("/probe")
async def get_probes() -> dict:
    """List all currently running job IDs."""
    return {"jobs": list_jobs()}
