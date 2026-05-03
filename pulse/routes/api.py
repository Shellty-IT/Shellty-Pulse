"""
REST API blueprint — all /api/* endpoints.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from pulse import state
from pulse.checker import check_all_services, check_single_service
from pulse.config import AVAILABLE_INTERVALS, MAX_NAME_LENGTH, MAX_SERVICES, MAX_URL_LENGTH
from pulse.models import create_service, get_overall_status
from pulse.validators import validate_service_payload

logger = logging.getLogger("shellty-pulse")
api_bp = Blueprint("api", __name__, url_prefix="/api")


# ── GET /api/services ────────────────────────────────────────────────────────

@api_bp.get("/services")
def get_services():
    """
    List all monitored services with their current status.

    Returns JSON with a ``services`` array and a ``meta`` block
    (overall status, auto-ping state, timing info).
    """
    with state.services_lock:
        services_data      = [svc.copy() for svc in state.services]
        current_interval   = state.ping_interval
        current_auto_ping  = state.auto_ping_enabled
        current_last_check = state.last_check_time

    return jsonify({
        "services": services_data,
        "meta": {
            "overall_status":    get_overall_status(),
            "auto_ping_enabled": current_auto_ping,
            "ping_interval":     current_interval,
            "last_check":        current_last_check,
            "total_services":    len(services_data),
        },
    })


# ── POST /api/services ───────────────────────────────────────────────────────

@api_bp.post("/services")
def add_service_route():
    """
    Add a new service to monitor.

    Request body (JSON)::

        {
            "name":         "Service Name",
            "url":          "https://example.com/health",
            "frontend_url": "https://example.com"          // optional
        }

    Returns the created service dict with HTTP 201.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    name         = data.get("name", "").strip()
    url          = data.get("url", "").strip()
    frontend_url = data.get("frontend_url", "").strip() or None

    error = validate_service_payload(
        name, url, frontend_url, MAX_NAME_LENGTH, MAX_URL_LENGTH
    )
    if error:
        return jsonify({"error": error}), 400

    svc = create_service(name, url, frontend_url)

    with state.services_lock:
        if len(state.services) >= MAX_SERVICES:
            return jsonify(
                {"error": f"Maximum {MAX_SERVICES} services allowed."}
            ), 400
        state.services.append(svc)

    logger.info("Added service: %s → %s (id: %s)", name, url, svc["id"])
    return jsonify(svc), 201


# ── DELETE /api/services/<id> ────────────────────────────────────────────────

@api_bp.delete("/services/<service_id>")
def delete_service_route(service_id: str):
    """
    Remove a service by ID.

    Returns HTTP 204 on success, 404 if not found.
    """
    with state.services_lock:
        for i, svc in enumerate(state.services):
            if svc["id"] == service_id:
                removed = state.services.pop(i)
                logger.info(
                    "Deleted service: %s (id: %s)", removed["name"], service_id
                )
                return "", 204

    return jsonify({"error": "Service not found."}), 404


# ── POST /api/services/<id>/check ────────────────────────────────────────────

@api_bp.post("/services/<service_id>/check")
def check_service_route(service_id: str):
    """
    Manually trigger a health check for a single service.

    Returns the updated service dict.
    """
    target = None
    with state.services_lock:
        for svc in state.services:
            if svc["id"] == service_id:
                target = svc
                break

    if not target:
        return jsonify({"error": "Service not found."}), 404

    check_single_service(target)

    with state.services_lock:
        return jsonify(target.copy())


# ── POST /api/check-all ──────────────────────────────────────────────────────

@api_bp.post("/check-all")
def check_all_route():
    """
    Manually trigger health checks for all services.

    Returns updated services list with overall status.
    """
    check_all_services()

    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]

    return jsonify({
        "message":        "All services checked.",
        "services":       services_data,
        "overall_status": get_overall_status(),
    })


# ── POST /api/toggle-auto-ping ───────────────────────────────────────────────

@api_bp.post("/toggle-auto-ping")
def toggle_auto_ping_route():
    """
    Toggle automatic periodic health checking on / off.

    Returns the new auto-ping state.
    """
    with state.services_lock:
        state.auto_ping_enabled = not state.auto_ping_enabled
        new_state = state.auto_ping_enabled

    label = "enabled" if new_state else "disabled"
    logger.info("Auto-ping toggled: %s", label)

    return jsonify({
        "auto_ping_enabled": new_state,
        "message":           f"Auto-ping {label}.",
    })


# ── POST /api/ping-interval ──────────────────────────────────────────────────

@api_bp.post("/ping-interval")
def set_ping_interval_route():
    """
    Change the auto-ping interval and reschedule the background job.

    Request body (JSON)::

        {"interval": 900}

    Valid values: 600, 900, 1800, 3600, 86400, 172800.
    """
    from pulse.scheduler import scheduler  # late import avoids circular dep

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    new_interval = data.get("interval")
    if new_interval not in AVAILABLE_INTERVALS:
        valid = [f"{v} ({k}s)" for k, v in AVAILABLE_INTERVALS.items()]
        return jsonify(
            {"error": f"Invalid interval. Valid options: {', '.join(valid)}"}
        ), 400

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

    return jsonify({
        "interval": new_interval,
        "label":    label,
        "message":  f"Auto-ping interval set to {label}.",
    })