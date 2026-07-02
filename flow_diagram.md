## Program Flow Diagram

```
                           ┌─────────────────────────────────────────────────────┐
                           │               latency-probe service                  │
                           │                (uvicorn / FastAPI)                   │
                           └─────────────────────────────────────────────────────┘

  Caller (curl / k8s cron)
         │
         │  POST /probe
         │  { "url": "https://target.example.com",
         │    "interval_seconds": 10 }
         │
         ▼
  ┌─────────────────┐
  │  FastAPI route  │  ← Pydantic validates url (HttpUrl) and interval (> 0)
  │  create_probe() │    Returns 422 immediately if invalid
  └────────┬────────┘
           │
           │  start_job(url, interval)
           ▼
  ┌──────────────────────┐
  │  prober.start_job()  │  Assigns a short uuid4 job_id
  │                      │  Calls asyncio.create_task() on the event loop
  │  _jobs[job_id]=task  │  Returns job_id to the caller (HTTP 201)
  └──────────────────────┘
           │
           │  (running concurrently on the asyncio event loop)
           ▼
  ┌──────────────────────────────────────────────────┐
  │  _probe_loop(job_id, url, interval)  [async task] │
  │                                                    │
  │  ┌─────────────────────────────────────────────┐  │
  │  │  loop (until task.cancel())                 │  │
  │  │                                             │  │
  │  │  1. record timestamp (UTC ISO-8601)         │  │
  │  │                                             │  │
  │  │  2. httpx.AsyncClient.get(url)              │  │──► Target URL
  │  │     ├─ success → status_code, latency_ms   │  │◄── HTTP response
  │  │     └─ failure → error string              │  │
  │  │        (TimeoutException / RequestError)   │  │
  │  │                                             │  │
  │  │  3. _emit(ProbeResult)                      │  │
  │  │     ├─ no error  → stdout + logger.info    │  │──► stdout  (samples.txt)
  │  │     └─ has error → stderr + logger.error   │  │──► stderr
  │  │                                             │  │
  │  │  4. asyncio.sleep(interval)                 │  │
  │  │     (yields event loop to other tasks)     │  │
  │  │                                             │  │
  │  └─────────────────────────────────────────────┘  │
  └──────────────────────────────────────────────────┘

  Caller
         │
         │  DELETE /probe/{job_id}
         ▼
  ┌──────────────────────┐
  │  stop_job(job_id)    │  task.cancel() → CancelledError injected at next
  │                      │  await point, httpx client closed cleanly
  └──────────────────────┘
         │
         └──► HTTP 204 (or 404 if job_id unknown)


Output line format (one per measurement):
──────────────────────────────────────────
job=<id> url=<url> ts=<ISO8601> status=<HTTP code|None> latency_ms=<float|None> error=<None|message>
```
