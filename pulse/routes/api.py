"""
REST API blueprint — all /api/* endpoints.

New endpoints for split fire-verify strategy:
  POST /api/fire-all   — Phase 1: Kick cold starts
  POST /api/verify-all — Phase 3: Verify services (call after 120s)
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from pulse import state
from pulse.checker import (
    check_single_service,
    is_check_running,
    set_check_running,
    fire_all,
    verify_all,
    _check_lock,
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

_WAKE_SECRET = os.environ.get("WAKE_SECRET", "")


# ── GET /api/services ────────────────────────────────────────────────────────

@api_bp.get("/services")
def get_services():
    """List all monitored services with current status and meta."""
    with state.services_lock:
        services_data      = [svc.copy() for svc in state.services]
        current_interval   = state.ping_interval
        current_auto_ping  = state.auto_ping_enabled
        current_last_check = state.last_check_time
        bh_enabled         = state.business_hours_enabled
        bh_start           = state.business_hours_start
        bh_end             = state.business_hours_end

    return jsonify({
        "services": services_data,
        "meta": {
            "overall_status":          get_overall_status(),
            "auto_ping_enabled":       current_auto_ping,
            "ping_interval":           current_interval,
            "last_check":              current_last_check,
            "total_services":          len(services_data),
            "check_running":           is_check_running(),
            "business_hours_enabled":  bh_enabled,
            "business_hours_start":    bh_start,
            "business_hours_end":      bh_end,
            "business_hours_timezone": BUSINESS_HOURS_TIMEZONE,
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
                    "Deleted service: %s (id: %s)",
                    removed["name"], service_id,
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


# ── POST /api/fire-all ───────────────────────────────────────────────────────

@api_bp.post("/fire-all")
def fire_all_route():
    """
    Phase 1: Fire all services (kick cold starts).
    Returns immediately — use /api/verify-all after 120s.
    """
    with state.services_lock:
        snapshot = [svc.copy() for svc in state.services]

    logger.info("🔥 Firing all %d services to wake cold starts...", len(snapshot))

    def _fire_in_background():
        fire_all(snapshot)
        logger.info("🔥 Fire complete — wait 120s then call /api/verify-all")

    threading.Thread(
        target=_fire_in_background,
        daemon=True,
        name="fire-thread"
    ).start()

    return jsonify({
        "status":         "fired",
        "services_count": len(snapshot),
        "message":        "Cold starts initiated. Call /api/verify-all in 120 seconds.",
    }), 202


# ── POST /api/verify-all ─────────────────────────────────────────────────────

@api_bp.post("/verify-all")
def verify_all_route():
    """
    Phase 3: Verify all services (after cold starts).
    Call this 120s after /api/fire-all.
    """
    if is_check_running():
        return jsonify({
            "message":       "Check already running.",
            "check_running": True,
        }), 409

    with state.services_lock:
        snapshot = [svc.copy() for svc in state.services]

    logger.info("✔️ Verifying all %d services...", len(snapshot))

    def _verify_in_background():
        if not _check_lock.acquire(blocking=False):
            logger.warning("verify-all: lock already held")
            return

        try:
            set_check_running(True)
            verify_all(snapshot)
            with state.services_lock:
                state.last_check_time = datetime.now(timezone.utc).isoformat()
            logger.info("✔️ Verification complete.")
        finally:
            set_check_running(False)
            _check_lock.release()

    threading.Thread(
        target=_verify_in_background,
        daemon=True,
        name="verify-thread"
    ).start()

    return jsonify({
        "status":         "verifying",
        "services_count": len(snapshot),
        "message":        "Verification started in background.",
    }), 202


# ── POST /api/check-all (DEPRECATED) ─────────────────────────────────────────

@api_bp.post("/check-all")
def check_all_route():
    """
    DEPRECATED: Use /api/fire-all + wait 120s + /api/verify-all instead.

    This endpoint still works for backwards compatibility but may cause
    Gunicorn worker timeouts on Render.
    """
    from pulse.checker import check_all_services

    if is_check_running():
        logger.info("check-all: already running — returning 409")
        return jsonify({
            "message":       "Check already in progress. Wait for it to finish.",
            "check_running": True,
        }), 409

    threading.Thread(
        target=check_all_services,
        daemon=True,
        name="manual-check-all-thread"
    ).start()

    logger.warning("check-all endpoint called — DEPRECATED, use fire-all + verify-all")

    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]

    return jsonify({
        "message":        "Health checks started in background (DEPRECATED).",
        "services":       services_data,
        "overall_status": get_overall_status(),
        "check_running":  True,
    }), 202


# ── POST /api/wake-and-check ─────────────────────────────────────────────────

@api_bp.post("/wake-and-check")
def wake_and_check_route():
    """
    DEPRECATED: Called by old GitHub Actions workflow.
    Use /api/fire-all + /api/verify-all instead.
    """
    if _WAKE_SECRET:
        incoming = request.headers.get("X-Wake-Secret", "")
        if incoming != _WAKE_SECRET:
            logger.warning("wake-and-check: invalid or missing X-Wake-Secret")
            return jsonify({"error": "Unauthorized"}), 401

    with state.services_lock:
        auto_ping     = state.auto_ping_enabled
        ping_interval = state.ping_interval
        last_check    = state.last_check_time

    now          = datetime.now(timezone.utc)
    should_check = False
    skip_reason  = ""

    if not auto_ping:
        skip_reason = "auto_ping disabled"
    else:
        if last_check is None:
            should_check = True
        else:
            try:
                last_dt = datetime.fromisoformat(last_check)
                elapsed = (now - last_dt).total_seconds()
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
                should_check = True

    if should_check:
        from pulse.checker import check_all_services

        if is_check_running():
            logger.info("wake-and-check: check already running — skipping spawn")
            return jsonify({
                "status":        "ok",
                "awake":         True,
                "checking":      True,
                "triggered_by":  "github-actions",
                "message":       "Skipped: check already running in background.",
                "auto_ping":     auto_ping,
                "ping_interval": ping_interval,
            }), 200

        logger.warning("wake-and-check: DEPRECATED — use fire-all + verify-all")
        threading.Thread(
            target=check_all_services,
            daemon=True,
            name="wake-and-check-thread"
        ).start()

        return jsonify({
            "status":        "ok",
            "awake":         True,
            "checking":      True,
            "triggered_by":  "github-actions",
            "message":       "Service checks started in background (DEPRECATED).",
            "auto_ping":     auto_ping,
            "ping_interval": ping_interval,
        }), 200
    else:
        logger.info("wake-and-check: skipping — %s", skip_reason)
        return jsonify({
            "status":        "ok",
            "awake":         True,
            "checking":      False,
            "triggered_by":  "github-actions",
            "message":       f"Skipped: {skip_reason}",
            "auto_ping":     auto_ping,
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
    """Change the auto-ping interval and reschedule the background job."""
    from pulse.scheduler import scheduler

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    new_interval = data.get("interval")
    if new_interval not in AVAILABLE_INTERVALS:
        valid = [f"{v} ({k}s)" for k, v in AVAILABLE_INTERVALS.items()]
        return jsonify(
            {"error": f"Invalid interval. Valid: {', '.join(valid)}"}
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
    """Configure business hours settings and sync to GitHub Variables."""
    from pulse.github_sync import sync_business_hours_to_github

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "'enabled' must be a boolean."}), 400

    start = data.get("start")
    end   = data.get("end")

    if not isinstance(start, int) or not isinstance(end, int):
        return jsonify(
            {"error": "'start' and 'end' must be integers."}
        ), 400

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

    def _sync():
        ok = sync_business_hours_to_github(enabled, start, end)
        if ok:
            logger.info("GitHub Variables synced successfully.")
        else:
            logger.warning("GitHub Variables sync failed.")

    threading.Thread(target=_sync, daemon=True, name="github-sync").start()

    label        = f"{start:02d}:00 – {end:02d}:00 CET"
    status_label = "enabled" if enabled else "disabled"

    logger.info(
        "Business hours %s: %s",
        status_label,
        label if enabled else "—",
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