"""
Health-check engine.

Cold-start strategy (Render free tier):
  Split into two phases executed by GitHub Actions:
    Phase 1 (fire)   — Send GET to all services simultaneously (kick cold start)
    Phase 2 (wait)   — GitHub Actions sleeps 120s
    Phase 3 (verify) — Check all services with short retry loops

  Total: ~2.5 min for any number of services.
  GitHub Actions controls timing — no worker blocking.
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from pulse import state
from pulse.config import REQUEST_TIMEOUT
from pulse.models import determine_status

logger = logging.getLogger("shellty-pulse")

# ── Tuning constants ─────────────────────────────────────────────────────────
_VERIFY_MAX_RETRIES: int = 4
_VERIFY_429_WAIT:    int = 20
_VERIFY_502_WAIT:    int = 15

# ── Concurrency guard ────────────────────────────────────────────────────────
_check_lock    = threading.Lock()
_check_running = False

# ── Public API ───────────────────────────────────────────────────────────────
__all__ = [
    "check_all_services",
    "check_single_service",
    "is_check_running",
    "fire_all",
    "verify_all",
]


def is_check_running() -> bool:
    """Return True if check is currently active."""
    return _check_running


def set_check_running(value: bool) -> None:
    """Set check running state (called by API routes)."""
    global _check_running
    _check_running = value


# ── Shared HTTP headers ───────────────────────────────────────────────────────
_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control":   "no-cache",
    "Connection":      "keep-alive",
}


# ── Phase 1: Fire ─────────────────────────────────────────────────────────────

def _fire_single(service: dict) -> None:
    """
    Send one GET to kick the service's cold start. Ignores the response —
    any status (429/502/503) means Render received it and is booting.
    """
    with state.services_lock:
        url  = service["url"]
        name = service["name"]
    try:
        r = requests.get(url, timeout=10, headers=_HEADERS, allow_redirects=True)
        logger.info("  🔥 %s → fire (HTTP %d)", name, r.status_code)
    except Exception as exc:
        logger.info("  🔥 %s → fire (no response yet: %s)", name, str(exc)[:60])


def fire_all(snapshot: list[dict]) -> None:
    """Kick cold start on all services simultaneously."""
    logger.info("Phase 1 — firing all %d services in parallel...", len(snapshot))
    with ThreadPoolExecutor(max_workers=len(snapshot) or 1) as ex:
        futures = {ex.submit(_fire_single, svc): svc["name"] for svc in snapshot}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                logger.warning("  fire error for %s: %s", futures[f], exc)
    logger.info("Phase 1 done — all fire requests sent.")


# ── Phase 3: Verify ───────────────────────────────────────────────────────────

def _verify_single(service: dict) -> None:
    """
    Full health check with retry. Called after cold start wait — services
    should be warm. Updates service state atomically when done.
    """
    with state.services_lock:
        url        = service["url"]
        name       = service["name"]
        service_id = service["id"]

    logger.info("  ✔ Verifying: %s", name)

    success          = False
    status           = "down"
    response_time_ms = None

    for attempt in range(_VERIFY_MAX_RETRIES):
        try:
            start    = time.time()
            response = requests.get(
                url, timeout=REQUEST_TIMEOUT, headers=_HEADERS, allow_redirects=True
            )
            elapsed = time.time() - start

            if response.status_code == 429:
                if attempt < _VERIFY_MAX_RETRIES - 1:
                    logger.warning(
                        "    ⏳ %s → 429, waiting %ds (attempt %d/%d)...",
                        name, _VERIFY_429_WAIT, attempt + 1, _VERIFY_MAX_RETRIES,
                    )
                    time.sleep(_VERIFY_429_WAIT)
                    continue
                logger.error("    ✗ %s → still 429 after verify retries", name)
                status = "down"
                break

            if response.status_code in (502, 503):
                if attempt < _VERIFY_MAX_RETRIES - 1:
                    logger.warning(
                        "    ⏳ %s → %d (still cold?), waiting %ds (attempt %d/%d)...",
                        name, response.status_code, _VERIFY_502_WAIT,
                        attempt + 1, _VERIFY_MAX_RETRIES,
                    )
                    time.sleep(_VERIFY_502_WAIT)
                    continue
                logger.error("    ✗ %s → still %d after verify retries", name, response.status_code)
                status           = "down"
                response_time_ms = round(elapsed * 1000)
                break

            success          = response.status_code == 200
            status           = determine_status(elapsed, success)
            response_time_ms = round(elapsed * 1000)
            if success:
                logger.info("    ✓ %s → %s (HTTP %d, %dms)", name, status, response.status_code, response_time_ms)
            else:
                logger.warning("    ✗ %s → down (HTTP %d, %dms)", name, response.status_code, response_time_ms)
            break

        except requests.exceptions.Timeout:
            if attempt < _VERIFY_MAX_RETRIES - 1:
                logger.warning("    ⏳ %s → timeout (attempt %d/%d), retrying in 15s...", name, attempt + 1, _VERIFY_MAX_RETRIES)
                time.sleep(15)
            else:
                logger.error("    ✗ %s → timeout after verify retries", name)
                status = "down"

        except requests.exceptions.RequestException as exc:
            if attempt < _VERIFY_MAX_RETRIES - 1:
                logger.warning("    ⏳ %s → error (%s), retrying in 15s...", name, str(exc)[:80])
                time.sleep(15)
            else:
                logger.error("    ✗ %s → error after verify retries: %s", name, exc)
                status = "down"

    with state.services_lock:
        for svc in state.services:
            if svc["id"] == service_id:
                svc["status"]           = status
                svc["response_time_ms"] = response_time_ms
                svc["last_check"]       = datetime.now(timezone.utc).isoformat()
                svc["total_checks"]    += 1
                if success:
                    svc["successful_checks"] += 1
                if svc["total_checks"] > 0:
                    svc["uptime_percent"] = round(
                        (svc["successful_checks"] / svc["total_checks"]) * 100, 2
                    )
                break


def verify_all(snapshot: list[dict]) -> None:
    """Verify all services in parallel after cold-start wait."""
    logger.info("Phase 3 — verifying all %d services in parallel...", len(snapshot))
    with ThreadPoolExecutor(max_workers=len(snapshot) or 1) as ex:
        futures = {ex.submit(_verify_single, svc): svc["name"] for svc in snapshot}
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                logger.warning("  verify error for %s: %s", futures[f], exc)
    logger.info("Phase 3 done — all services verified.")


# ── Single-service manual check (dashboard ⟳ button per service) ─────────────

def check_single_service(service: dict) -> None:
    """
    Manual single-service check from the dashboard row button.
    Goes straight to verify (no fire phase) — user triggered, service
    likely already warm or they want the raw current state.
    """
    _verify_single(service)


# ── Legacy check-all (for backwards compatibility) ────────────────────────────

def check_all_services() -> None:
    """
    DEPRECATED: Use fire_all() + wait + verify_all() instead.

    This function still exists for backwards compatibility but is NOT
    recommended — it blocks the worker for verify phase.
    """
    global _check_running

    if not _check_lock.acquire(blocking=False):
        logger.warning("check_all_services() already running — skipping.")
        return

    try:
        _check_running = True

        with state.services_lock:
            snapshot = list(state.services)

        logger.warning(
            "check_all_services() called — DEPRECATED, use fire_all + verify_all"
        )
        logger.info("Checking %d services (legacy mode)...", len(snapshot))

        if not snapshot:
            logger.info("No services configured.")
            return

        # Just verify — no fire/wait phases
        verify_all(snapshot)

        with state.services_lock:
            state.last_check_time = datetime.now(timezone.utc).isoformat()

        logger.info("Legacy check complete.")

    finally:
        _check_running = False
        _check_lock.release()


def scheduled_check() -> None:
    """Scheduler callback — honours the auto_ping_enabled flag."""
    with state.services_lock:
        enabled = state.auto_ping_enabled
    if enabled:
        check_all_services()
    else:
        logger.info("Auto-ping disabled — skipping scheduled check.")