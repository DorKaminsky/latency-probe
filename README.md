# latency-probe

A REST service that measures the HTTP response latency of remote resources on a configurable polling interval.

## Quick start

### Run locally with uv

```bash
# Install uv (if not already installed)
curl -Lsf https://astral.sh/uv/install.sh | sh

# Create virtualenv and install dependencies
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"

# Start the service
uvicorn app.main:app --reload --port 8000
```

### Run with Docker

```bash
docker build -t latency-probe .
docker run -p 8000:8000 latency-probe
```

### API

```bash
# Start probing a URL every 5 seconds
curl -X POST http://localhost:8000/probe \
  -H "Content-Type: application/json" \
  -d '{"url": "https://httpbin.org/get", "interval_seconds": 5}'
# → {"job_id": "a1b2c3d4", "url": "...", "interval_seconds": 5, "status": "running"}

# List running jobs
curl http://localhost:8000/probe

# Stop a job
curl -X DELETE http://localhost:8000/probe/a1b2c3d4

# Health check
curl http://localhost:8000/health
```

Output is written to stdout (success) / stderr (errors) in the format:

```
job=a1b2c3d4 url=https://httpbin.org/get ts=2026-07-02T10:00:00+00:00 status=200 latency_ms=142.3 error=None
```

### Collect samples to a file

```bash
# Redirect stdout to samples.txt while keeping stderr visible
uvicorn app.main:app --port 8000 > samples.txt &
curl -X POST http://localhost:8000/probe \
  -d '{"url": "https://httpbin.org/get", "interval_seconds": 10}'
# Let it run for 10 minutes, then kill
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
# Apply all manifests in order
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
│   ├── models.py      # Pydantic schemas
│   └── prober.py      # Async polling logic
├── tests/
│   └── test_api.py
├── manifests/         # Kubernetes manifests
├── terraform/         # AWS EKS + ECR IaC
├── .github/workflows/
│   └── ci.yml         # Lint → test → docker build
├── Dockerfile
├── pyproject.toml
├── flow_diagram.md
├── Pipeline.txt
└── Difficult_part_answer.txt
```
