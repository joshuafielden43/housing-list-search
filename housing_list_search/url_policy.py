"""
Outbound URL policy for the housing-list-search trust boundary.

Every HTTP fetch (polite_get, robots.txt, Bloom API posts) should pass through
validate_http_url() before leaving the process. Blocks SSRF targets such as
private networks, loopback, link-local, and cloud metadata endpoints.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ALLOWED_SCHEMES = ("http", "https")

# Hostnames that must never be fetched, even if DNS would return a public IP.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata.goog",
})

# Substrings that indicate link-local / metadata hosts.
_BLOCKED_HOST_SUBSTRINGS = (
    ".local",
    ".internal",
    "169.254.",
)


class URLPolicyError(ValueError):
    """Raised when a URL fails outbound policy checks."""


def _hostname_is_blocked(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES:
        return True
    if any(part in host for part in _BLOCKED_HOST_SUBSTRINGS):
        return True
    return False


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_resolved_addresses(hostname: str) -> None:
    """Resolve hostname and reject if any address is non-public."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLPolicyError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    if not infos:
        raise URLPolicyError(f"DNS resolution returned no addresses for {hostname!r}")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not _ip_is_public(ip):
            raise URLPolicyError(
                f"URL host {hostname!r} resolves to non-public address {addr}"
            )


def validate_http_url(url: str, *, resolve_dns: bool = True) -> str:
    """
    Validate that url is safe to fetch over HTTP(S).

    Returns the original url string on success.
    Raises URLPolicyError on policy violation.
    """
    if not url or not str(url).strip():
        raise URLPolicyError("URL is empty")

    url = str(url).strip()
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise URLPolicyError(f"Disallowed URL scheme: {parsed.scheme!r}")

    if parsed.username or parsed.password:
        raise URLPolicyError("URLs with embedded credentials are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise URLPolicyError("URL has no hostname")

    if _hostname_is_blocked(hostname):
        raise URLPolicyError(f"Blocked hostname: {hostname!r}")

    # Literal IP in the URL
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not _ip_is_public(ip):
            raise URLPolicyError(f"Non-public IP address in URL: {hostname}")
    elif resolve_dns:
        _check_resolved_addresses(hostname)

    return url


def is_safe_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    """Non-raising policy check for sanitizers."""
    try:
        validate_http_url(url, resolve_dns=resolve_dns)
        return True
    except URLPolicyError as exc:
        logger.debug("URL policy rejected %r: %s", url, exc)
        return False