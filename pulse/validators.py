"""
Input validation and SSRF protection.

All functions are pure (no side effects).
"""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

from pulse.config import BLOCKED_HOSTS


def is_safe_url(url: str) -> bool:
    """
    Guard against SSRF — block internal, loopback, and metadata URLs.

    Rejects:
    - Missing or empty hostname
    - Hostnames listed in ``config.BLOCKED_HOSTS``
    - IPs in private / loopback / link-local ranges

    Args:
        url: URL string to validate.

    Returns:
        ``True`` if the URL is safe to request externally.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname

        if not hostname:
            return False

        if hostname in BLOCKED_HOSTS:
            return False

        try:
            ip = ipaddress.ip_address(hostname)
            if ip.is_private or ip.is_loopback or ip.is_link_local:
                return False
        except ValueError:
            pass  # Hostname is a domain name — not an IP, that's fine

        return True

    except Exception:
        return False


def validate_service_payload(
    name: str,
    url: str,
    frontend_url: str | None,
    max_name: int,
    max_url: int,
) -> str | None:
    """
    Validate fields for adding a new service.

    Args:
        name:         Service display name.
        url:          Health-check URL.
        frontend_url: Optional frontend URL (``None`` = not provided).
        max_name:     Maximum allowed length for ``name``.
        max_url:      Maximum allowed length for ``url`` / ``frontend_url``.

    Returns:
        An error message string if validation fails, ``None`` if all OK.
    """
    if not name or not url:
        return "Both 'name' and 'url' are required."

    if len(name) > max_name:
        return f"Name must be at most {max_name} characters."

    if len(url) > max_url:
        return f"URL must be at most {max_url} characters."

    if not url.startswith(("http://", "https://")):
        return "URL must start with http:// or https://"

    if not is_safe_url(url):
        return "URL points to a blocked or internal address."

    if frontend_url:
        if not frontend_url.startswith(("http://", "https://")):
            return "Frontend URL must start with http:// or https://"
        if len(frontend_url) > max_url:
            return f"Frontend URL must be at most {max_url} characters."

    return None