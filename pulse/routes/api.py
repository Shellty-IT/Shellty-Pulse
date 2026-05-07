"""
REST API blueprint — all /api/* endpoints.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from pulse import state
from pulse.checker import check_all_services, check_single_service
from pulse.config import (
    AVAILABLE_INTERVALS,
    MAX_NAME_LENGTH,
    MAX_SERVICES,
    MAX_URL_LENGTH,
    BUSINESS_HOURS_TIMEZONE,
)
from pulse.models import create_service, get_overall_status
from pulse.validators import validate_service_payload

logger = logging.getLogger("shellty-pulse")
api_bp = Blueprint("api", __name__, url_prefix="/api")


# ── GET /api/services ────────────────────────────────────────────────────────

@api_bp.get("/services")
def get_services():
    """
    List all monitored services with their current status.
    Includes business hours settings in meta.
    """
    with state.services_lock:
        services_data           = [svc.copy() for svc in state.services]
        current_interval        = state.ping_interval
        current_auto_ping       = state.auto_ping_enabled
        current_last_check      = state.last_check_time
        bh_enabled              = state.business_hours_enabled
        bh_start                = state.business_hours_start
        bh_end                  = state.business_hours_end

    return jsonify({
        "services": services_data,
        "meta": {
            "overall_status":           get_overall_status(),
            "auto_ping_enabled":        current_auto_ping,
            "ping_interval":            current_interval,
            "last_check":               current_last_check,
            "total_services":           len(services_data),
            "business_hours_enabled":   bh_enabled,
            "business_hours_start":     bh_start,
            "business_hours_end":       bh_end,
            "business_hours_timezone":  BUSINESS_HOURS_TIMEZONE,
        },
    })


# ── POST /api/services ───────────────────────────────────────────────────────

@api_bp.post("/services")
def add_service_route():
    """Add a new service to monitor."""
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
    """Remove a service by ID."""
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
    """Manually trigger a health check for a single service."""
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
    """Manually trigger health checks for all services."""
    check_all_services()

    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]

    return jsonify({
        "message":        "All services checked.",
        "services":       services_data,
        "overall_status": get_overall_status(),
    })


# ── POST /api/wake-and-check ─────────────────────────────────────────────────

@api_bp.post("/wake-and-check")
def wake_and_check_route():
    """
    Called by GitHub Actions to wake Shellty Pulse.

    Returns 200 OK immediately (app is awake).
    Then checks in background:
      - auto_ping_enabled must be True
      - enough time must have passed since last_check (>= ping_interval)
    If both conditions met → runs check_all_services() in background thread.
    """
    # Read state under lock
    with state.services_lock:
        auto_ping       = state.auto_ping_enabled
        ping_interval   = state.ping_interval
        last_check      = state.last_check_time

    now = datetime.now(timezone.utc)

    # Determine if we should trigger a check
    should_check = False
    skip_reason  = ""

    if not auto_ping:
        skip_reason = "auto_ping disabled"
    else:
        if last_check is None:
            # Never checked before — check now
            should_check = True
        else:
            try:
                last_dt  = datetime.fromisoformat(last_check)
                elapsed  = (now - last_dt).total_seconds()
                if elapsed >= ping_interval:
                    should_check = True
                else:
                    remaining   = int(ping_interval - elapsed)
                    skip_reason = (
                        f"too soon — {int(elapsed)}s elapsed, "
                        f"need {ping_interval}s, "
                        f"next check in ~{remaining}s"
                    )
            except (ValueError, TypeError):
                # Malformed last_check — check now to be safe
                should_check = True

    if should_check:
        logger.info(
            "wake-and-check: conditions met — starting background check"
        )
        threading.Thread(
            target=check_all_services,
            daemon=True,
            name="wake-and-check-thread",
        ).start()

        return jsonify({
            "status":       "ok",
            "awake":        True,
            "checking":     True,
            "message":      "Service checks started in background.",
            "auto_ping":    auto_ping,
            "ping_interval": ping_interval,
        }), 200

    else:
        logger.info("wake-and-check: skipping check — %s", skip_reason)
        return jsonify({
            "status":       "ok",
            "awake":        True,
            "checking":     False,
            "message":      f"Skipped: {skip_reason}",
            "auto_ping":    auto_ping,
            "ping_interval": ping_interval,
        }), 200


# ── POST /api/toggle-auto-ping ───────────────────────────────────────────────

@api_bp.post("/toggle-auto-ping")
def toggle_auto_ping_route():
    """Toggle automatic periodic health checking on / off."""
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
    Valid values: 600, 900, 1800, 3600, 86400, 172800.
    """
    from pulse.scheduler import scheduler

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


# ── POST /api/business-hours ─────────────────────────────────────────────────

@api_bp.post("/business-hours")
def set_business_hours_route():
    """
    Configure business hours settings.

    Request body (JSON)::

        {
            "enabled": true,
            "start":   9,
            "end":     15
        }

    Hours are in CET (Europe/Warsaw) timezone.
    GitHub Actions will only wake Shellty Pulse within these hours.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    # Validate enabled flag
    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "'enabled' must be a boolean."}), 400

    # Validate start/end hours
    start = data.get("start")
    end   = data.get("end")

    if not isinstance(start, int) or not isinstance(end, int):
        return jsonify({"error": "'start' and 'end' must be integers."}), 400

    if not (0 <= start <= 23) or not (0 <= end <= 23):
        return jsonify(
            {"error": "'start' and 'end' must be between 0 and 23."}
        ), 400

    if start >= end:
        return jsonify(
            {"error": "'start' must be less than 'end'."}
        ), 400

    with state.services_lock:
        state.business_hours_enabled = enabled
        state.business_hours_start   = start
        state.business_hours_end     = end

    label = f"{start:02d}:00 – {end:02d}:00 CET"
    status_label = "enabled" if enabled else "disabled"

    logger.info(
        "Business hours %s: %s", status_label, label if enabled else "—"
    )

    return jsonify({
        "business_hours_enabled":  enabled,
        "business_hours_start":    start,
        "business_hours_end":      end,
        "business_hours_timezone": BUSINESS_HOURS_TIMEZONE,
        "label":                   label,
        "message": (
            f"Business hours {status_label}: {label}."
            if enabled
            else "Business hours disabled."
        ),
    })