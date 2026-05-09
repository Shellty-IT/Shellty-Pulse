"""
REST API blueprint — all /api/* endpoints.

Changes vs previous version:
- Removed `from pulse.github_sync import sync_business_hours_to_github`
  (file does not exist). GitHub Variables sync now handled inline via
  _sync_business_hours_to_github() using direct GitHub REST API calls.
- Fixed _check_lock import (use checker_mod namespace instead of direct import)
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

import requests as http_requests
from flask import Blueprint, jsonify, request

from pulse import state
from pulse.checker import (
    check_single_service,
    is_check_running,
    set_check_running,
    fire_all,
    verify_all,
)
import pulse.checker as checker_mod

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

_WAKE_SECRET  = os.environ.get("WAKE_SECRET", "")
_GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
_GITHUB_REPO  = os.environ.get("GITHUB_REPO", "")


# ── GitHub Variables sync ────────────────────────────────────────────────────

def _sync_business_hours_to_github(enabled: bool, start: int, end: int) -> bool:
    """
    Push business-hours settings to GitHub Actions Variables via REST API.
    Requires GITHUB_TOKEN and GITHUB_REPO env vars.
    Returns True on success, False on any error.
    """
    if not _GITHUB_TOKEN or not _GITHUB_REPO:
        logger.warning("GitHub sync skipped: GITHUB_TOKEN or GITHUB_REPO not set.")
        return False

    base = f"https://api.github.com/repos/{_GITHUB_REPO}/actions/variables"
    headers = {
        "Authorization":        f"Bearer {_GITHUB_TOKEN}",
        "Accept":               "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    variables = {
        "BH_ENABLED": "true" if enabled else "false",
        "BH_START":   str(start),
        "BH_END":     str(end),
    }

    try:
        for name, value in variables.items():
            url  = f"{base}/{name}"
            resp = http_requests.patch(
                url, headers=headers,
                json={"name": name, "value": value}, timeout=10,
            )
            if resp.status_code == 404:
                resp = http_requests.post(
                    base, headers=headers,
                    json={"name": name, "value": value}, timeout=10,
                )
            if resp.status_code not in (200, 201, 204):
                logger.error(
                    "GitHub sync failed for %s: HTTP %d — %s",
                    name, resp.status_code, resp.text[:200],
                )
                return False
        return True
    except Exception as exc:
        logger.error("GitHub sync exception: %s", exc)
        return False


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

    error = validate_service_payload(name, url, frontend_url, MAX_NAME_LENGTH, MAX_URL_LENGTH)
    if error:
        return jsonify({"error": error}), 400

    svc = create_service(name, url, frontend_url)

    with state.services_lock:
        if len(state.services) >= MAX_SERVICES:
            return jsonify({"error": f"Maximum {MAX_SERVICES} services allowed."}), 400
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
                logger.info("Deleted service: %s (id: %s)", removed["name"], service_id)
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
    """Phase 1: Fire all services (kick cold starts). Returns immediately."""
    with state.services_lock:
        snapshot = [svc.copy() for svc in state.services]

    logger.info("🔥 Firing all %d services to wake cold starts...", len(snapshot))

    threading.Thread(
        target=fire_all,
        args=(snapshot,),
        daemon=True,
        name="fire-thread",
    ).start()

    return jsonify({
        "status":         "fired",
        "services_count": len(snapshot),
        "message":        "Cold starts initiated. Call /api/verify-all in 120 seconds.",
    }), 202


# ── POST /api/verify-all ─────────────────────────────────────────────────────

@api_bp.post("/verify-all")
def verify_all_route():
    """Phase 3: Verify all services. Call this 120s after /api/fire-all."""
    if is_check_running():
        return jsonify({"message": "Check already running.", "check_running": True}), 409

    with state.services_lock:
        snapshot = [svc.copy() for svc in state.services]

    logger.info("✔️ Verifying all %d services...", len(snapshot))

    def _verify_in_background():
        if not checker_mod._check_lock.acquire(blocking=False):
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
            checker_mod._check_lock.release()

    threading.Thread(target=_verify_in_background, daemon=True, name="verify-thread").start()

    return jsonify({
        "status":         "verifying",
        "services_count": len(snapshot),
        "message":        "Verification started in background.",
    }), 202


# ── POST /api/check-all ──────────────────────────────────────────────────────

@api_bp.post("/check-all")
def check_all_route():
    """Legacy endpoint — used by dashboard 'Check All Now' button."""
    from pulse.checker import check_all_services

    if is_check_running():
        logger.info("check-all: already running — returning 409")
        return jsonify({"message": "Check already in progress.", "check_running": True}), 409

    threading.Thread(
        target=check_all_services, daemon=True, name="manual-check-all-thread"
    ).start()

    logger.info("Manual check-all triggered — running in background.")

    with state.services_lock:
        services_data = [svc.copy() for svc in state.services]

    return jsonify({
        "message":        "Health checks started in background.",
        "services":       services_data,
        "overall_status": get_overall_status(),
        "check_running":  True,
    }), 202


# ── POST /api/wake-and-check ─────────────────────────────────────────────────

@api_bp.post("/wake-and-check")
def wake_and_check_route():
    """Called by GitHub Actions to wake Shellty Pulse."""
    if _WAKE_SECRET:
        if request.headers.get("X-Wake-Secret", "") != _WAKE_SECRET:
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
                        f"need {ping_interval}s, next check in ~{remaining}s"
                    )
            except (ValueError, TypeError):
                should_check = True

    if should_check:
        from pulse.checker import check_all_services
        if is_check_running():
            return jsonify({
                "status": "ok", "awake": True, "checking": True,
                "triggered_by": "github-actions",
                "message": "Skipped: check already running.",
                "auto_ping": auto_ping, "ping_interval": ping_interval,
            }), 200

        threading.Thread(
            target=check_all_services, daemon=True, name="wake-and-check-thread"
        ).start()

        return jsonify({
            "status": "ok", "awake": True, "checking": True,
            "triggered_by": "github-actions",
            "message": "Service checks started in background.",
            "auto_ping": auto_ping, "ping_interval": ping_interval,
        }), 200
    else:
        return jsonify({
            "status": "ok", "awake": True, "checking": False,
            "triggered_by": "github-actions",
            "message": f"Skipped: {skip_reason}",
            "auto_ping": auto_ping, "ping_interval": ping_interval,
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
    return jsonify({"auto_ping_enabled": new_state, "message": f"Auto-ping {label}."})


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
        return jsonify({"error": f"Invalid interval. Valid: {', '.join(valid)}"}), 400

    with state.services_lock:
        state.ping_interval = new_interval

    if scheduler and scheduler.running:
        scheduler.reschedule_job("health_check_job", trigger="interval", seconds=new_interval)

    label = AVAILABLE_INTERVALS[new_interval]
    logger.info("Ping interval changed to %s (%ds)", label, new_interval)
    return jsonify({"interval": new_interval, "label": label, "message": f"Auto-ping interval set to {label}."})


# ── POST /api/business-hours ─────────────────────────────────────────────────

@api_bp.post("/business-hours")
def set_business_hours_route():
    """Configure business hours and sync to GitHub Variables."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    enabled = data.get("enabled")
    if not isinstance(enabled, bool):
        return jsonify({"error": "'enabled' must be a boolean."}), 400

    start = data.get("start")
    end   = data.get("end")

    if not isinstance(start, int) or not isinstance(end, int):
        return jsonify({"error": "'start' and 'end' must be integers."}), 400

    if not (0 <= start <= 23) or not (0 <= end <= 23):
        return jsonify({"error": "'start' and 'end' must be between 0 and 23."}), 400

    if start >= end:
        return jsonify({"error": "'start' must be less than 'end'."}), 400

    with state.services_lock:
        state.business_hours_enabled = enabled
        state.business_hours_start   = start
        state.business_hours_end     = end

    threading.Thread(
        target=_sync_business_hours_to_github,
        args=(enabled, start, end),
        daemon=True,
        name="github-sync",
    ).start()

    label        = f"{start:02d}:00 – {end:02d}:00 CET"
    status_label = "enabled" if enabled else "disabled"
    logger.info("Business hours %s: %s", status_label, label if enabled else "—")

    return jsonify({
        "business_hours_enabled":  enabled,
        "business_hours_start":    start,
        "business_hours_end":      end,
        "business_hours_timezone": BUSINESS_HOURS_TIMEZONE,
        "label":                   label,
        "message": (
            f"Business hours {status_label}: {label}."
            if enabled else "Business hours disabled."
        ),
    })