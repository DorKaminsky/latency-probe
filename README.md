# latency-probe

A REST service that measures the HTTP response latency of remote resources on a configurable polling interval.

## Quick start

### Optional: enable the AI feature

Only needed if you want the `GET /probe/{id}/analyze` endpoint (Claude Haiku 4.5 anomaly diagnosis). Everything else works without a key.

```bash
cp .env.example .env
# Edit .env and paste your key from https://console.anthropic.com/settings/keys
```

`docker compose` auto-loads `.env`. For `uv` or plain `docker run`, export it in your shell:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

### Run locally with uv (development)

```bash
curl -Lsf https://astral.sh/uv/install.sh | sh
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

### Run with Docker Compose (recommended — service + Prometheus)

```bash
docker compose up
# latency-probe → http://localhost:8000
# Prometheus     → http://localhost:9090
# OpenAPI docs   → http://localhost:8000/docs
```

### Run with Docker (single container)

```bash
# Option A: build locally
docker build -t latency-probe .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY latency-probe

# Option B: pull the CI-published image
docker run -p 8000:8000 -e ANTHROPIC_API_KEY \
  ghcr.io/dorkaminsky/latency-probe:latest
```

The `-e ANTHROPIC_API_KEY` (no value) forwards the variable from your shell. Omit it if you're not using the AI feature.

## API

```bash
# Start probing a URL every 5 seconds
curl -X POST http://localhost:8000/probe \
  -H "Content-Type: application/json" \
  -d '{"url": "https://httpbin.org/get", "interval_seconds": 5}'
# → {"job_id": "a1b2c3d4", "url": "...", "interval_seconds": 5, "status": "running"}

# List running jobs
curl http://localhost:8000/probe

# Fetch last 20 measurements for a job
curl http://localhost:8000/probe/a1b2c3d4/results

# Fetch last N measurements (max 100)
curl "http://localhost:8000/probe/a1b2c3d4/results?limit=5"

# LLM diagnosis of recent measurements (requires ANTHROPIC_API_KEY)
curl http://localhost:8000/probe/a1b2c3d4/analyze
# → {"job_id": "...", "diagnosis": "Latency is stable around 200ms with one 5.7s spike..."}

# Stop a job
curl -X DELETE http://localhost:8000/probe/a1b2c3d4

# Prometheus metrics
curl http://localhost:8000/metrics

# Health check
curl http://localhost:8000/health
```

Output is written to stdout (success) / stderr (errors) in the format:

```
job=a1b2c3d4 url=https://httpbin.org/get ts=2026-07-02T10:00:00+00:00 status=200 latency_ms=142.3 error=None
```

### AI-powered anomaly analysis

`GET /probe/{id}/analyze` feeds the ring buffer of recent measurements to
Claude Haiku 4.5 and returns a 2-3 sentence diagnosis (baseline latency,
spikes, error patterns, overall health). Cached per job for 60s.

**How to enable per run mode:**

| Run mode | Where the key goes |
|---|---|
| `uv` / `uvicorn` | `export ANTHROPIC_API_KEY=sk-ant-...` in the shell before starting uvicorn |
| `docker compose up` | Copy `.env.example` → `.env`, edit, then `docker compose up` (auto-loaded) |
| `docker run` | `docker run -p 8000:8000 -e ANTHROPIC_API_KEY latency-probe` (forwards from shell) |
| Kubernetes | Create Secret: `kubectl create secret generic latency-probe-secrets --from-literal=anthropic-api-key=sk-ant-...` — deployment.yaml already wires it in as `optional: true` |

Without the key the endpoint returns 503 and the rest of the service is unaffected.

### Security: SSRF protection

URLs targeting private/internal addresses are rejected. The defence is two-stage:

1. **Pre-flight**: `POST /probe` resolves the hostname via async `getaddrinfo`
   (3-second timeout) and rejects if any resolved IP is in RFC-1918, loopback,
   link-local (incl. `169.254.169.254` AWS metadata), or IPv6 equivalents.
2. **Per-request**: a custom httpx transport re-resolves and re-checks the IP
   on every probe attempt, defending against DNS rebinding (a hostname that
   flips to a private IP after validation).

Redirects are disabled (`follow_redirects=False`) so a 302 to an internal URL
cannot bypass the transport-level guard.

### Collect samples to a file

```bash
uvicorn app.main:app --port 8000 > samples.txt &
curl -X POST http://localhost:8000/probe \
  -d '{"url": "https://httpbin.org/get", "interval_seconds": 10}'
sleep 600 && kill %1
```

## Run tests

```bash
pytest --tb=short -v
```

## Run linting

```bash
black app/ tests/
isort app/ tests/
flake8 app/ tests/ --max-line-length=88 --extend-ignore=E203
```

## Deploy to Kubernetes

```bash
kubectl apply -f manifests/namespace.yaml
kubectl apply -f manifests/configmap.yaml
kubectl apply -f manifests/deployment.yaml
kubectl apply -f manifests/service.yaml
kubectl apply -f manifests/hpa.yaml
```

## Provision AWS infrastructure with Terraform

```bash
cd terraform
terraform init
terraform plan
terraform apply
```

## Project structure

```
latency-probe/
├── app/
│   ├── main.py        # FastAPI routes
│   ├── models.py      # Pydantic schemas + blocked-network list
│   ├── security.py    # Async SSRF check + DNS-rebinding guard transport
│   ├── analyze.py     # Claude-powered anomaly diagnosis (Haiku 4.5)
│   └── prober.py      # Async polling logic + Prometheus metrics
├── tests/
│   └── test_api.py
├── manifests/         # Kubernetes manifests (with Prometheus scrape annotations)
├── terraform/         # AWS EKS + ECR IaC
├── .github/workflows/
│   └── ci.yml         # Lint → test → build & push to GHCR
├── docker-compose.yml # Local dev: service + Prometheus
├── prometheus.yml     # Prometheus scrape config
├── .env.example       # Template for ANTHROPIC_API_KEY (optional)
├── Dockerfile
├── pyproject.toml
├── flow_diagram.md
├── Pipeline.txt
└── Difficult_part_answer.txt
```

