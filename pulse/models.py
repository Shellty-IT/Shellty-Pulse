"""
Service data model and overall status calculation.
"""

from __future__ import annotations

import uuid

from pulse import state


def create_service(
    name: str,
    url: str,
    frontend_url: str | None = None,
) -> dict:
    """Create a new service record with default values."""
    return {
        "id": str(uuid.uuid4()),
        "name": name,
        "url": url,
        "frontend_url": frontend_url,
        "status": "unknown",
        "response_time_ms": None,
        "last_check": None,
        "total_checks": 0,
        "successful_checks": 0,
        "uptime_percent": None,
        "enabled": True,
    }


def determine_status(elapsed_seconds: float, success: bool) -> str:
    """Determine service status based on response time."""
    if not success:
        return "down"
    if elapsed_seconds < 1.0:
        return "operational"
    elif elapsed_seconds <= 3.0:
        return "degraded"
    else:
        return "slow"


def get_overall_status() -> str:
    """Calculate overall system status based on all services."""
    from pulse.checker import is_check_running

    if is_check_running():
        return "checking"

    with state.services_lock:
        active = [svc for svc in state.services if svc.get("enabled", True)]
        if not active:
            return "unknown"

        statuses = [svc["status"] for svc in active]

        if all(s == "unknown" for s in statuses):
            return "unknown"

        if "down" in statuses:
            return "down"
        if "slow" in statuses:
            return "slow"
        if "degraded" in statuses:
            return "degraded"
        if all(s == "operational" for s in statuses):
            return "operational"

        return "operational"
