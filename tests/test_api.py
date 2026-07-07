"""
API tests using httpx.AsyncClient against the FastAPI TestClient.

We test the API surface (routes, status codes, response shape) rather than
mocking internals — this way the tests catch real integration issues between
FastAPI routing, Pydantic validation, and the prober module.
"""

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.prober import _jobs, _results


@pytest.fixture(autouse=True)
async def clear_jobs():
    """Ensure each test starts with no running jobs."""
    _jobs.clear()
    _results.clear()
    yield
    for task in _jobs.values():
        task.cancel()
    _jobs.clear()
    _results.clear()


@pytest.fixture
async def client():
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


async def test_metrics_endpoint(client):
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "probe_requests_total" in resp.text


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
    assert resp.status_code == 422


async def test_create_probe_invalid_url(client):
    resp = await client.post("/probe", json={"url": "not-a-url", "interval_seconds": 5})
    assert resp.status_code == 422


async def test_create_probe_rejects_private_ip(client):
    # SSRF protection: private/metadata IPs must be blocked
    for url in [
        "http://169.254.169.254/latest/meta-data/",
        "http://192.168.1.1/admin",
        "http://10.0.0.1/",
        "http://127.0.0.1:8000/",
    ]:
        resp = await client.post("/probe", json={"url": url, "interval_seconds": 5})
        assert resp.status_code == 422, f"expected 422 for {url}"


async def test_ssrf_guard_rejects_private_ip_at_probe_time(monkeypatch):
    # DNS rebinding defence: _SSRFGuardTransport should reject a request whose
    # host resolves to a private IP, even if it passed the pre-flight check.
    import socket

    from app.prober import _SSRFGuardTransport

    async def fake_getaddrinfo(host, port, **kwargs):
        # Simulate a hostname that resolves to a metadata IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]

    monkeypatch.setattr(
        "asyncio.get_running_loop",
        lambda: type("L", (), {"getaddrinfo": staticmethod(fake_getaddrinfo)})(),
    )
    transport = _SSRFGuardTransport()
    request = httpx.Request("GET", "http://evil.example.com/")
    with pytest.raises(httpx.ConnectError, match="private IP"):
        await transport.handle_async_request(request)


async def test_list_probes(client):
    await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": 5}
    )
    resp = await client.get("/probe")
    assert resp.status_code == 200
    assert len(resp.json()["jobs"]) == 1


async def test_results_empty_on_new_job(client):
    create = await client.post(
        "/probe", json={"url": "https://httpbin.org/get", "interval_seconds": 5}
    )
    job_id = create.json()["job_id"]
    resp = await client.get(f"/probe/{job_id}/results")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_results_not_found(client):
    resp = await client.get("/probe/does-not-exist/results")
    assert resp.status_code == 404


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
