"""
Health-check engine.

Performs HTTP GET requests against registered services,
measures response time, updates service records in-place (thread-safe).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from pulse import state
from pulse.config import REQUEST_TIMEOUT
from pulse.models import determine_status

logger = logging.getLogger("shellty-pulse")


def check_single_service(service: dict) -> None:
    """
    Run an HTTP GET health check on one service and update its record.

    The service dict is mutated in-place under ``state.services_lock``.
    Safe to call from multiple threads (each call locks independently).

    Args:
        service: A service record dict (element of ``state.services``).
    """
    with state.services_lock:
        url  = service["url"]
        name = service["name"]

    logger.info("Checking service: %s (%s)", name, url)

    # ── RETRY LOGIC for cold start ──
    max_retries = 2
    success = False
    status = "down"
    response_time_ms = None

    for attempt in range(max_retries):
        try:
            start    = time.time()
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            elapsed  = time.time() - start

            success          = response.status_code == 200
            status           = determine_status(elapsed, success)
            response_time_ms = round(elapsed * 1000)

            if success:
                logger.info(
                    "  ✓ %s → %s (HTTP %d, %dms)",
                    name, status, response.status_code, response_time_ms,
                )
                break  # Success - exit retry loop
            else:
                logger.warning(
                    "  ✗ %s → down (HTTP %d, %dms)",
                    name, response.status_code, response_time_ms,
                )
                # Don't retry for HTTP errors (4xx/5xx)
                break

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⏳ %s → timeout (attempt %d/%d), retrying in 5s...",
                    name, attempt + 1, max_retries
                )
                time.sleep(5)  # Wait before retry
                continue
            else:
                logger.error(
                    "  ✗ %s → down (timeout after %ds)",
                    name, REQUEST_TIMEOUT
                )
                status           = "down"
                response_time_ms = None
                success          = False

        except requests.exceptions.RequestException as exc:
            if attempt < max_retries - 1:
                logger.warning(
                    "  ⏳ %s → error (%s), retrying in 5s...",
                    name, str(exc)[:80]
                )
                time.sleep(5)
                continue
            else:
                logger.error("  ✗ %s → down (error: %s)", name, exc)
                status           = "down"
                response_time_ms = None
                success          = False

    # ── Atomic update ────────────────────────────────────────────────────────
    with state.services_lock:
        service["status"]           = status
        service["response_time_ms"] = response_time_ms
        service["last_check"]       = datetime.now(timezone.utc).isoformat()
        service["total_checks"]    += 1

        if success:
            service["successful_checks"] += 1

        if service["total_checks"] > 0:
            service["uptime_percent"] = round(
                (service["successful_checks"] / service["total_checks"]) * 100, 2
            )


def check_all_services() -> None:
    """
    Run health checks on every registered service sequentially.

    Called by the scheduler (auto-ping) and by the manual
    ``POST /api/check-all`` endpoint.
    Updates ``state.last_check_time`` after all checks complete.
    """
    with state.services_lock:
        snapshot = list(state.services)

    logger.info("=" * 50)
    logger.info(
        "Starting health check for all services (%d total)", len(snapshot)
    )

    for svc in snapshot:
        check_single_service(svc)

    with state.services_lock:
        state.last_check_time = datetime.now(timezone.utc).isoformat()

    logger.info("Health check complete.")
    logger.info("=" * 50)


def scheduled_check() -> None:
    """
    Scheduler callback — honours the ``auto_ping_enabled`` flag.

    Reads the flag under lock, then delegates to ``check_all_services``
    if auto-ping is currently active.
    """
    with state.services_lock:
        enabled = state.auto_ping_enabled

    if enabled:
        check_all_services()
    else:
        logger.info("Auto-ping disabled — skipping scheduled check.")