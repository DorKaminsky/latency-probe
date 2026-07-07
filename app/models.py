import ipaddress
import socket

from pydantic import BaseModel, HttpUrl, field_validator

# Private/loopback/link-local ranges that must never be probed.
# This service accepts arbitrary URLs from callers — without this check it
# is a classic SSRF vector: an attacker could probe 169.254.169.254 (AWS
# instance metadata), internal k8s services, or the host loopback.
_BLOCKED_NETWORKS = [
    ipaddress.ip_network(cidr)
    for cidr in (
        "127.0.0.0/8",  # loopback
        "10.0.0.0/8",  # RFC-1918 private
        "172.16.0.0/12",  # RFC-1918 private
        "192.168.0.0/16",  # RFC-1918 private
        "169.254.0.0/16",  # link-local / AWS metadata
        "::1/128",  # IPv6 loopback
        "fc00::/7",  # IPv6 ULA
        "fe80::/10",  # IPv6 link-local
    )
]


def _is_private(hostname: str) -> bool:
    try:
        addr = ipaddress.ip_address(socket.gethostbyname(hostname))
    except (socket.gaierror, ValueError):
        return False
    return any(addr in net for net in _BLOCKED_NETWORKS)


class ProbeRequest(BaseModel):
    url: HttpUrl
    interval_seconds: float

    @field_validator("interval_seconds")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("interval_seconds must be > 0")
        return v

    @field_validator("url")
    @classmethod
    def must_not_be_private(cls, v: HttpUrl) -> HttpUrl:
        # Resolves the hostname at validation time to block SSRF attempts
        # targeting private/metadata IPs (e.g. 169.254.169.254).
        # ponytail: DNS-at-validation-time; a hostname could resolve differently
        # at probe time. Good enough for SSRF mitigation, not a full solution.
        host = v.host or ""
        if _is_private(host):
            raise ValueError(
                "URL targets a private or reserved address — probing internal "
                "hosts is not allowed (SSRF protection)"
            )
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
    # error is None on success; a string description on any failure
    error: str | None
