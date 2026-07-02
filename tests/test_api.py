"""
API tests using httpx.AsyncClient against the FastAPI TestClient.

We test the API surface (routes, status codes, response shape) rather than
mocking internals — this way the tests catch real integration issues between
FastAPI routing, Pydantic validation, and the prober module.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.prober import _jobs


@pytest.fixture(autouse=True)
async def clear_jobs():
    """Ensure each test starts with no running jobs."""
    _jobs.clear()
    yield
    # Cancel all tasks created during the test to avoid event loop warnings
    for task in _jobs.values():
        task.cancel()
    _jobs.clear()


@pytest.fixture
async def client():
    # ASGITransport lets us drive the FastAPI app in-process without starting
    # a real HTTP server — fast and deterministic
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_create_probe_returns_201(client):
    resp = await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": 5}
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "job_id" in data
    assert data["status"] == "running"


async def test_create_probe_invalid_interval(client):
    resp = await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": -1}
    )
    assert resp.status_code == 422  # Pydantic validation error


async def test_create_probe_invalid_url(client):
    resp = await client.post(
        "/probe", json={"url": "not-a-url", "interval_seconds": 5}
    )
    assert resp.status_code == 422


async def test_list_probes(client):
    await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": 5}
    )
    resp = await client.get("/probe")
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 1


async def test_delete_probe(client):
    create = await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": 5}
    )
    job_id = create.json()["job_id"]
    resp = await client.delete(f"/probe/{job_id}")
    assert resp.status_code == 204


async def test_delete_nonexistent_probe(client):
    resp = await client.delete("/probe/does-not-exist")
    assert resp.status_code == 404
