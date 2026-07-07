# syntax=docker/dockerfile:1

# ── Stage 1: dependency resolution ─────────────────────────────────────────
# A separate build stage keeps the final image lean: uv, wheel caches and
# compiler toolchains never land in the runtime layer.
FROM python:3.12-slim AS builder

# uv is the fast Rust-based package installer (100x faster than pip for cold
# installs). We pin the version so CI is deterministic.
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv

WORKDIR /build

# Copy dependency manifest first so Docker cache is invalidated only when
# dependencies change, not on every source-code edit.
COPY pyproject.toml .

# --system: install into the system Python so there's no venv to activate
# Dev extras ([dev]) are not referenced here so they are not installed
RUN uv pip install --system --no-cache -e .

# ── Stage 2: runtime ────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# Running as non-root limits blast radius if the container is compromised.
# UID 1001 avoids conflicts with system users (0=root, 1-999=system).
RUN useradd --uid 1001 --no-create-home appuser

WORKDIR /app

# Copy installed packages from builder stage (no uv or build tools included)
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn

# Copy source code last — most frequently changing layer, keeps it at the top
# of the cache so earlier layers are reused on code-only changes.
COPY app/ ./app/

USER appuser

EXPOSE 8000

# HEALTHCHECK lets the Docker daemon (and ECS/k8s) know when the container
# is ready to serve traffic without relying on an external probe.
HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# uvicorn is the ASGI server; --host 0.0.0.0 binds to all interfaces so
# k8s Service can reach it. Workers=1 because job state is in-process memory;
# multiple workers would each have their own _jobs dict.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
