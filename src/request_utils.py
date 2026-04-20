"""Helpers for trusting proxy headers and validating Origin.

Why: slowapi's default `get_remote_address` returns the immediate TCP peer,
which is always the reverse proxy in production. That collapses every real
client to a single key and disables rate limiting. The helpers here read the
real client IP from `X-Forwarded-For` only when the peer is a trusted proxy,
so spoofing is rejected for non-proxy callers.
"""
from __future__ import annotations

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from fastapi import Request


def _parse_trusted(trusted: list[str]) -> list:
    nets = []
    for item in trusted or []:
        try:
            if "/" in item:
                nets.append(ip_network(item, strict=False))
            else:
                nets.append(ip_network(f"{item}/32" if ":" not in item else f"{item}/128", strict=False))
        except ValueError:
            continue
    return nets


def client_ip(request: Request) -> str:
    """Resolve the effective client IP for rate-limit bucketing.

    If the immediate peer is in `settings.security.trusted_proxies`, take the
    left-most IP from X-Forwarded-For; otherwise return the peer itself.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        settings = request.app.state.settings
        trusted_nets = _parse_trusted(settings.security.trusted_proxies)
    except AttributeError:
        trusted_nets = _parse_trusted(["127.0.0.1", "::1"])

    try:
        peer_addr = ip_address(peer)
    except ValueError:
        return peer

    if any(peer_addr in net for net in trusted_nets):
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            first = fwd.split(",", 1)[0].strip()
            if first:
                return first
    return peer


def assert_same_origin(request: Request) -> None:
    """Reject requests whose Origin/Referer does not match the server host.

    Why: SameSite=Lax cookies are still sent on cross-site fetch POSTs with
    `credentials: "include"`. For state-mutating endpoints (logout, refresh)
    this enables light CSRF: force-logout, trigger silent session renewal.
    Origin check blocks that without requiring a full CSRF token.
    """
    from src.auth import AuthError

    origin = request.headers.get("origin") or request.headers.get("referer")
    if not origin:
        # Non-browser clients (curl, CI) may omit Origin; only enforce when
        # a browser-style cookie is actually present.
        if request.cookies.get("access_token") or request.cookies.get("refresh_token"):
            raise AuthError("Missing Origin header on authenticated request", 403)
        return

    try:
        parsed = urlparse(origin)
    except ValueError:
        raise AuthError("Malformed Origin header", 403)

    origin_host = (parsed.hostname or "").lower()
    request_host = (request.url.hostname or "").lower()

    try:
        allowed = [h.strip().lower() for h in request.app.state.settings.security.allowed_origins if h.strip()]
    except AttributeError:
        allowed = []

    # Same-origin (request and declared origin match) OR host in allowed list.
    if origin_host and (origin_host == request_host or origin_host in allowed):
        return
    raise AuthError(f"Cross-origin request rejected: origin={origin_host}", 403)
