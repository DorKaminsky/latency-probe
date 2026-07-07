## Program Flow Diagram

```
                           ┌─────────────────────────────────────────────────────┐
                           │               latency-probe service                  │
                           │                (uvicorn / FastAPI)                   │
                           │                                                      │
                           │  Endpoints: POST/GET/DELETE /probe                   │
                           │             GET /probe/{id}/results                  │
                           │             GET /metrics    (Prometheus scrape)      │
                           │             GET /health     (k8s liveness)           │
                           └─────────────────────────────────────────────────────┘

  Caller (curl / k8s cron)
         │
         │  POST /probe
         │  { "url": "https://target.example.com",
         │    "interval_seconds": 10 }
         │
         ▼
  ┌─────────────────────────┐
  │  FastAPI route          │  ← Pydantic validates url (HttpUrl) and interval (> 0)
  │  create_probe()         │    Returns 422 immediately if invalid
  └────────┬────────────────┘
           │
           │  await resolve_and_check(url)          ┌─────────────────────────────┐
           ├──────────────────────────────────────► │  security.py — pre-flight   │
           │                                        │  async loop.getaddrinfo()   │
           │                                        │  (3s timeout, non-blocking) │
           │                                        │                             │
           │  raises SSRFError → 422                │  Rejects if any resolved    │
           │◄───────────────────────────────────────│  IP ∈ RFC-1918 / loopback / │
           │                                        │  link-local / IPv6 private  │
           │                                        └─────────────────────────────┘
           │
           │  start_job(url, interval)
           ▼
  ┌──────────────────────┐
  │  prober.start_job()  │  Assigns 8-char uuid4 job_id
  │                      │  Allocates deque(maxlen=100) for results
  │  _jobs[job_id]=task  │  asyncio.create_task(_probe_loop, ...)
  │                      │  Returns job_id to the caller (HTTP 201)
  └──────────────────────┘
           │
           │  (running concurrently on the asyncio event loop)
           ▼
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  _probe_loop(job_id, url, interval)   [async task]                          │
  │                                                                              │
  │  httpx.AsyncClient(transport=_SSRFGuardTransport(), follow_redirects=False) │
  │                                                                              │
  │  ┌───────────────────────────────────────────────────────────────────────┐ │
  │  │  loop (until task.cancel())                                            │ │
  │  │                                                                        │ │
  │  │  1. record timestamp (UTC ISO-8601)                                    │ │
  │  │                                                                        │ │
  │  │  2. client.get(url)                                                    │ │
  │  │     │                                                                  │ │
  │  │     ▼                                                                  │ │
  │  │  ┌──────────────────────────────────────┐                              │ │
  │  │  │  _SSRFGuardTransport                 │                              │ │
  │  │  │  Re-resolves hostname on EACH        │                              │ │
  │  │  │  request; refuses connect if the     │                              │ │
  │  │  │  IP now falls in a blocked range     │                              │ │
  │  │  │  (defends against DNS rebinding)     │                              │ │
  │  │  └───────────────────┬──────────────────┘                              │ │
  │  │                      │                                                 │ │
  │  │                      ▼                                                 │ │
  │  │  ┌──────────────────────────────────────┐                              │ │
  │  │  │  Target URL (public IP only)         │                              │ │
  │  │  └──────────────────────────────────────┘                              │ │
  │  │     │                                                                  │ │
  │  │     ├─ success → status_code, elapsed → latency_ms                     │ │
  │  │     └─ failure → error string (Timeout / RequestError / ConnectError)  │ │
  │  │                                                                        │ │
  │  │  3. ProbeResult built                                                  │ │
  │  │     ├─ _emit()  → stdout (success) / stderr (error) — key=value        │ │
  │  │     ├─ _results[job_id].append(result)  — ring buffer, last 100        │ │
  │  │     └─ Prometheus counters/histograms labelled by url + status_code    │ │
  │  │                                                                        │ │
  │  │  4. await asyncio.sleep(interval)                                      │ │
  │  │     (yields event loop to other tasks)                                 │ │
  │  │                                                                        │ │
  │  └───────────────────────────────────────────────────────────────────────┘ │
  └────────────────────────────────────────────────────────────────────────────┘

  Caller
         │
         │  GET /probe/{job_id}/results?limit=N       ─► returns last N from ring buffer
         │  GET /metrics                              ─► Prometheus text exposition
         │
         │  DELETE /probe/{job_id}
         ▼
  ┌──────────────────────┐
  │  stop_job(job_id)    │  task.cancel() → CancelledError injected at next
  │                      │  await point; httpx client and results deque are
  │                      │  cleaned up on task teardown
  └──────────────────────┘
         │
         └──► HTTP 204 (or 404 if job_id unknown)


Output line format (one per measurement):
──────────────────────────────────────────
job=<id> url=<url> ts=<ISO8601> status=<HTTP code|None> latency_ms=<float|None> error=<None|message>

Prometheus metrics (labels: url, status_code):
──────────────────────────────────────────────
probe_requests_total{url="...",status_code="200"}   Counter
probe_latency_seconds{url="..."}                    Histogram (buckets: 0.05..10s)
```
