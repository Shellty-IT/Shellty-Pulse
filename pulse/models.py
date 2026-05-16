"""
Service data model and overall status calculation.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pulse import state


def create_service(
    name: str,
    url: str,
    frontend_url: str | None = None,
) -> dict:
    """
    Create a new service record with default values.

    Args:
        name: Display name of the service
        url: Health check endpoint URL
        frontend_url: Optional user-facing URL

    Returns:
        Service dict with all fields initialized
    """
    return {
        "id":                 str(uuid.uuid4()),
        "name":               name,
        "url":                url,
        "frontend_url":       frontend_url,
        "status":             "unknown",
        "response_time_ms":   None,
        "last_check":         None,
        "total_checks":       0,
        "successful_checks":  0,
        "uptime_percent":     None,
        # Per-service kill switch — when False, service is skipped by
        # all wake/check paths (auto-ping, business-hours wake, manual
        # check-all, per-service ⟳ button, GitHub Actions workflow).
        # Persisted across container restarts via DISABLED_SERVICES
        # GitHub Variable (keyed by URL — id is a fresh UUID each boot).
        "enabled":            True,
    }


def determine_status(elapsed_seconds: float, success: bool) -> str:
    """
    Determine service status based on response time.

    Args:
        elapsed_seconds: Response time in seconds
        success: Whether the check succeeded (HTTP 200)

    Returns:
        Status string: operational, degraded, slow, or down
    """
    if not success:
        return "down"

    if elapsed_seconds < 1.0:
        return "operational"
    elif elapsed_seconds <= 3.0:
        return "degraded"
    else:
        return "slow"


def get_overall_status() -> str:
    """
    Calculate overall system status based on all services.

    Returns:
        - "checking": if check is currently running
        - "down": if any service is down
        - "slow": if any service is slow (but none down)
        - "degraded": if any service is degraded (but none down/slow)
        - "operational": if all services are operational
        - "unknown": if no services configured or none checked yet
    """
    from pulse.checker import is_check_running

    # Jeśli check w toku → status "checking"
    if is_check_running():
        return "checking"

    with state.services_lock:
        # Wyłączone serwisy nie wpływają na overall status — pomijamy.
        active = [svc for svc in state.services if svc.get("enabled", True)]
        if not active:
            # Brak aktywnych serwisów (wszystkie wyłączone lub lista pusta).
            return "unknown"

        statuses = [svc["status"] for svc in active]

        # Jeśli wszystkie unknown → nie było jeszcze checku
        if all(s == "unknown" for s in statuses):
            return "unknown"

        # Priorytet: down > slow > degraded > operational
        if "down" in statuses:
            return "down"
        if "slow" in statuses:
            return "slow"
        if "degraded" in statuses:
            return "degraded"
        if all(s == "operational" for s in statuses):
            return "operational"

        # Mixed unknown + operational → partial operational
        return "operational"