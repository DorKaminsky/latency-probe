import ipaddress

from pydantic import BaseModel, HttpUrl, field_validator

# Private/loopback/link-local ranges that must never be probed.
# See app/security.py for the enforcement logic — this list is exported so both
# the pre-flight resolve step and per-probe re-check can share one source of truth.
BLOCKED_NETWORKS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "169.254.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
]


class ProbeRequest(BaseModel):
    url: HttpUrl
    interval_seconds: float

    @field_validator("interval_seconds")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("interval_seconds must be > 0")
        return v


class ProbeResponse(BaseModel):
    job_id: str
    url: str
    interval_seconds: float
    status: str


class ProbeResult(BaseModel):
    job_id: str
    url: str
    timestamp: str
    status_code: int | None
    latency_ms: float | None
    error: str | None
