"""Validation of user-supplied source URLs (basic SSRF protection)."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeURLError(ValueError):
    pass


def _blocked(ip: ipaddress._BaseAddress) -> bool:
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_source_url(url: str, *, allow_private: bool = False) -> str:
    """Return the URL if it is http(s) and resolves only to public addresses.

    This reduces, but cannot fully remove, SSRF risk (DNS can change between this check and the fetch).
    Run Reelwright workers in a network segment that cannot reach internal services.
    """
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("The URL has no host.")
    if parsed.username or parsed.password:
        raise UnsafeURLError("URLs with embedded credentials are not allowed.")
    if allow_private:
        return parsed.geturl()
    try:
        literal = ipaddress.ip_address(host)
        addresses = [literal]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise UnsafeURLError(f"Cannot resolve host {host!r}.") from exc
        addresses = [ipaddress.ip_address(info[4][0].split("%")[0]) for info in infos]
    if not addresses or any(_blocked(ip) for ip in addresses):
        raise UnsafeURLError("The URL points to a private or reserved address.")
    return parsed.geturl()
