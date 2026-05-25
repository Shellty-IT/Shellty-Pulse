"""
REST API blueprint — all /api/* endpoints.
"""

from __future__ import annotations

import logging
import threading

from flask import Blueprint, jsonify, request

from pulse import state
from pulse.checker import (
    check_all_services,
    check_single_service,
    is_check_running,
)
from pulse.config import (
    AVAILABLE_INTERVALS,
    BUSINESS_HOURS_TIMEZONE,
    MAX_NAME_LENGTH,
    MAX_SERVICES,
    MAX_URL_LENGTH,
)
from pulse.models import create_service, get_overall_status
from pulse.validators import validate_service_payload

logger = logging.getLogger("shellty-pulse")
api_bp = Blueprint("api", __name__, url_prefix="/api")


# ── GET /api/services ────────────────────────────────────────────────────────


@api_bp.get("/services")
def get_services():
    """List all monitored services with current status and meta."""
    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]
        current_interval = state.ping_interval
        current_auto_ping = state.auto_ping_enabled
        current_last_check = state.last_check_time
        bh_enabled = state.business_hours_enabled
        bh_start = state.business_hours_start
        bh_end = state.business_hours_end

    return jsonify(
        {
            "services": services_data,
            "meta": {
                "overall_status": get_overall_status(),
                "auto_ping_enabled": current_auto_ping,
                "ping_interval": current_interval,
                "last_check": current_last_check,
                "total_services": len(services_data),
                "check_running": is_check_running(),
                "business_hours_enabled": bh_enabled,
                "business_hours_start": bh_start,
                "business_hours_end": bh_end,
                "business_hours_timezone": BUSINESS_HOURS_TIMEZONE,
            },
        }
    )


# ── POST /api/services ───────────────────────────────────────────────────────


@api_bp.post("/services")
def add_service_route():
    """Add a new service to monitor."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    name = data.get("name", "").strip()
    url = data.get("url", "").strip()
    frontend_url = data.get("frontend_url", "").strip() or None

    error = validate_service_payload(
        name,
        url,
        frontend_url,
        MAX_NAME_LENGTH,
        MAX_URL_LENGTH,
    )
    if error:
        return jsonify({"error": error}), 400

    svc = create_service(name, url, frontend_url)

    with state.services_lock:
        if len(state.services) >= MAX_SERVICES:
            return (
                jsonify(
                    {
                        "error": f"Maximum {MAX_SERVICES} services allowed.",
                    }
                ),
                400,
            )
        state.services.append(svc)

    logger.info("Added service: %s → %s (id: %s)", name, url, svc["id"])
    return jsonify(svc), 201


# ── DELETE /api/services/<id> ────────────────────────────────────────────────


@api_bp.delete("/services/<service_id>")
def delete_service_route(service_id: str):
    """Remove a service by ID."""
    with state.services_lock:
        for i, svc in enumerate(state.services):
            if svc["id"] == service_id:
                removed = state.services.pop(i)
                logger.info(
                    "Deleted service: %s (id: %s)",
                    removed["name"],
                    service_id,
                )
                return "", 204
    return jsonify({"error": "Service not found."}), 404


# ── POST /api/services/<id>/check ────────────────────────────────────────────


@api_bp.post("/services/<service_id>/check")
def check_service_route(service_id: str):
    """Manually trigger a health check for a single service."""
    target = None
    with state.services_lock:
        for svc in state.services:
            if svc["id"] == service_id:
                target = svc
                break
    if not target:
        return jsonify({"error": "Service not found."}), 404

    if not target.get("enabled", True):
        return (
            jsonify(
                {
                    "error": "Service is disabled — enable it before checking.",
                }
            ),
            409,
        )

    check_single_service(target)

    with state.services_lock:
        return jsonify(target.copy())


# ── POST /api/services/<id>/toggle-enabled ───────────────────────────────────


@api_bp.post("/services/<service_id>/toggle-enabled")
def toggle_service_enabled_route(service_id: str):
    """Flip per-service kill switch."""
    snapshot_svc = None
    with state.services_lock:
        for svc in state.services:
            if svc["id"] == service_id:
                svc["enabled"] = not svc.get("enabled", True)
                if svc["enabled"]:
                    svc["status"] = "unknown"
                    svc["response_time_ms"] = None
                else:
                    svc["status"] = "disabled"
                    svc["response_time_ms"] = None
                snapshot_svc = svc.copy()
                break

    if snapshot_svc is None:
        return jsonify({"error": "Service not found."}), 404

    logger.info(
        "Service %s: enabled → %s",
        snapshot_svc["name"],
        snapshot_svc["enabled"],
    )
    return jsonify(snapshot_svc), 200


# ── POST /api/check-all ──────────────────────────────────────────────────────


@api_bp.post("/check-all")
def check_all_route():
    """Trigger a health check for all enabled services."""
    if is_check_running():
        return (
            jsonify(
                {
                    "message": "Check already in progress.",
                    "check_running": True,
                }
            ),
            409,
        )

    threading.Thread(
        target=check_all_services,
        daemon=True,
        name="manual-check-all",
    ).start()

    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]

    return (
        jsonify(
            {
                "message": "Health checks started in background.",
                "services": services_data,
                "overall_status": get_overall_status(),
                "check_running": True,
            }
        ),
        202,
    )


# ── POST /api/toggle-auto-ping ───────────────────────────────────────────────


@api_bp.post("/toggle-auto-ping")
def toggle_auto_ping_route():
    """Toggle automatic periodic health checking on / off."""
    with state.services_lock:
        state.auto_ping_enabled = not state.auto_ping_enabled
        new_state = state.auto_ping_enabled

    label = "enabled" if new_state else "disabled"
    logger.info("Auto-ping toggled: %s", label)
    return jsonify(
        {
            "auto_ping_enabled": new_state,
            "message": f"Auto-ping {label}.",
        }
    )


# ── POST /api/ping-interval ──────────────────────────────────────────────────


@api_bp.post("/ping-interval")
def set_ping_interval_route():
    """Change the auto-ping interval and reschedule the background job."""
    from pulse.scheduler import scheduler

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    new_interval = data.get("interval")
    if new_interval not in AVAILABLE_INTERVALS:
        valid = [f"{v} ({k}s)" for k, v in AVAILABLE_INTERVALS.items()]
        return (
            jsonify(
                {
                    "error": f"Invalid interval. Valid: {', '.join(valid)}",
                }
            ),
            400,
        )

    with state.services_lock:
        state.ping_interval = new_interval

    if scheduler and scheduler.running:
        scheduler.reschedule_job(
            "health_check_job",
            trigger="interval",
            seconds=new_interval,
        )

    label = AVAILABLE_INTERVALS[new_interval]
    logger.info("Ping interval changed to %s (%ds)", label, new_interval)
    return jsonify(
        {
            "interval": new_interval,
            "label": label,
            "message": f"Auto-ping interval set to {label}.",
        }
    )


# ── POST /api/business-hours ────────────────────────────────────────────────


@api_bp.post("/business-hours")
def set_business_hours_route():
    """Configure business hours."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "'enabled' must be a boolean."}), 400

    start = data.get("start")
    end = data.get("end")

    if not isinstance(start, int) or not isinstance(end, int):
        return jsonify({"error": "'start' and 'end' must be integers."}), 400

    if not (0 <= start <= 23) or not (0 <= end <= 23):
        return (
            jsonify(
                {
                    "error": "'start' and 'end' must be between 0 and 23.",
                }
            ),
            400,
        )

    if start == end:
        return (
            jsonify(
                {
                    "error": "'start' and 'end' must be different hours.",
                }
            ),
            400,
        )

    with state.services_lock:
        state.business_hours_enabled = enabled
        state.business_hours_start = start
        state.business_hours_end = end

    if start < end:
        label = f"{start:02d}:00 – {end:02d}:00 CET"
    else:
        label = f"{start:02d}:00 – {end:02d}:00 CET (+1d)"

    status_label = "enabled" if enabled else "disabled"
    logger.info(
        "Business hours %s: %s",
        status_label,
        label if enabled else "—",
    )

    return jsonify(
        {
            "business_hours_enabled": enabled,
            "business_hours_start": start,
            "business_hours_end": end,
            "business_hours_timezone": BUSINESS_HOURS_TIMEZONE,
            "label": label,
            "message": (
                f"Business hours {status_label}: {label}."
                if enabled
                else "Business hours disabled."
            ),
        }
    )
