"""
Service model — factory function and status helpers.

Pure functions, no side effects, no I/O.
"""
from __future__ import annotations

import uuid


# ── Status priority (higher = worse) ────────────────────────────────────────
_STATUS_PRIORITY: dict[str, int] = {
    "unknown":     0,
    "operational": 1,
    "degraded":    2,
    "slow":        3,
    "down":        4,
}


def generate_id() -> str:
    """Return a short random hex ID (8 chars)."""
    return uuid.uuid4().hex[:8]


def create_service(
    name: str,
    url: str,
    frontend_url: str | None = None,
) -> dict:
    """
    Build a new service record with default/empty values.

    Args:
        name:         Display name shown on the dashboard.
        url:          Backend health-check URL (the URL that gets pinged).
        frontend_url: Optional link to the user-facing application.

    Returns:
        Service dict ready to be appended to ``state.services``.
    """
    return {
        "id":                generate_id(),
        "name":              name,
        "url":               url,
        "frontend_url":      frontend_url,
        "status":            "unknown",
        "response_time_ms":  None,
        "last_check":        None,
        "total_checks":      0,
        "successful_checks": 0,
        "uptime_percent":    None,
    }


def determine_status(response_time_seconds: float, success: bool) -> str:
    """
    Map response time + HTTP success to a status string.

    Rules:
        HTTP 200 + <  1 s  → operational
        HTTP 200 + 1–3 s   → degraded
        HTTP 200 + >  3 s  → slow
        HTTP error/timeout → down

    Args:
        response_time_seconds: Elapsed wall-clock time in seconds.
        success:               True when HTTP status code is 200.

    Returns:
        One of: ``operational`` | ``degraded`` | ``slow`` | ``down``
    """
    if not success:
        return "down"
    if response_time_seconds < 1.0:
        return "operational"
    if response_time_seconds <= 3.0:
        return "degraded"
    return "slow"


def get_overall_status() -> str:
    """
    Return the worst status across all currently monitored services.

    Priority (highest = worst): down > slow > degraded > operational > unknown

    Must be called **without** holding ``state.services_lock``
    (acquires it internally).
    """
    from pulse import state  # local import — avoids circular dependency

    with state.services_lock:
        if not state.services:
            return "unknown"

        worst = "unknown"
        for svc in state.services:
            status = svc.get("status", "unknown")
            if _STATUS_PRIORITY.get(status, 0) > _STATUS_PRIORITY.get(worst, 0):
                worst = status
        return worst