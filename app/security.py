"""
SSRF protection.

Two enforcement points, one policy:

1. `resolve_and_check(url)` — called from the route handler *before* creating a job.
   Uses async DNS (loop.getaddrinfo) so it does not block the event loop.
   Returns the resolved IP so the probe loop can pin to it.

2. `is_private_ip(ip)` — called from the probe loop on every request via httpx's
   transport, defending against DNS rebinding (hostname that flips to a private
   IP between validation and use).
"""

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse

from .models import BLOCKED_NETWORKS


class SSRFError(ValueError):
    """Raised when a URL targets a private/reserved address."""


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # fail closed: unparseable = reject
    return any(addr in net for net in BLOCKED_NETWORKS)


async def resolve_and_check(url: str) -> str:
    """Resolve the URL's hostname, reject if any resolved IP is private.

    Returns the first public IP found. Fails closed on DNS errors.
    """
    host = urlparse(url).hostname
    if not host:
        raise SSRFError("URL has no hostname")

    loop = asyncio.get_running_loop()
    try:
        # getaddrinfo in the default executor — non-blocking for the event loop.
        # Timeout of 3s so a slow/hostile DNS server can't stall the API.
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, type=socket.SOCK_STREAM),
            timeout=3.0,
        )
    except (socket.gaierror, asyncio.TimeoutError) as exc:
        raise SSRFError(f"DNS resolution failed for {host!r}: {exc}") from exc

    resolved_ips = {info[4][0] for info in infos}
    private = [ip for ip in resolved_ips if is_private_ip(ip)]
    if private:
        raise SSRFError(
            f"URL {host!r} resolves to private/reserved address(es): "
            f"{', '.join(private)} — probing internal hosts is not allowed"
        )
    # Prefer IPv4 if available for deterministic pinning
    for ip in resolved_ips:
        if ":" not in ip:
            return ip
    return next(iter(resolved_ips))
