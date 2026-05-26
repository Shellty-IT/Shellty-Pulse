"""
Health-check engine.

On Oracle Cloud all checks happen locally via APScheduler or manual triggers.
No fire/verify phases needed (services respond immediately on a 24/7 server).
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pytz
import requests

from pulse import state
from pulse.config import BUSINESS_HOURS_TIMEZONE, REQUEST_TIMEOUT
from pulse.models import determine_status

logger = logging.getLogger("shellty-pulse")

_RETRY_MAX: int = 3
_RETRY_WAIT: int = 10

_check_lock = threading.Lock()
_check_running = False

_HEADERS = {
    "User-Agent": "ShelltyPulse/1.0",
    "Accept": "application/json, text/html, */*",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
}


def is_check_running() -> bool:
    return _check_running


def set_check_running(value: bool) -> None:
    global _check_running
    _check_running = value


def _check_single(service: dict) -> None:
    """Full health check for one service with retries."""
    with state.services_lock:
        url = service["url"]
        name = service["name"]
        service_id = service["id"]

    success = False
    status = "down"
    response_time_ms = None

    for attempt in range(_RETRY_MAX):
        try:
            start = time.time()
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT,
                headers=_HEADERS,
                allow_redirects=True,
            )
            elapsed = time.time() - start

            if response.status_code in (429, 502, 503):
                if attempt < _RETRY_MAX - 1:
                    logger.warning(
                        "  %s → HTTP %d, retry %d/%d in %ds...",
                        name,
                        response.status_code,
                        attempt + 1,
                        _RETRY_MAX,
                        _RETRY_WAIT,
                    )
                    time.sleep(_RETRY_WAIT)
                    continue
                status = "down"
                response_time_ms = round(elapsed * 1000)
                break

            success = response.status_code == 200
            status = determine_status(elapsed, success)
            response_time_ms = round(elapsed * 1000)
            if success:
                logger.info(
                    "  %s → %s (HTTP %d, %dms)",
                    name,
                    status,
                    response.status_code,
                    response_time_ms,
                )
            else:
                logger.warning(
                    "  %s → down (HTTP %d, %dms)",
                    name,
                    response.status_code,
                    response_time_ms,
                )
            break

        except requests.exceptions.Timeout:
            if attempt < _RETRY_MAX - 1:
                logger.warning(
                    "  %s → timeout, retry %d/%d...",
                    name,
                    attempt + 1,
                    _RETRY_MAX,
                )
                time.sleep(_RETRY_WAIT)
            else:
                logger.error("  %s → timeout after %d retries", name, _RETRY_MAX)
                status = "down"

        except requests.exceptions.RequestException as exc:
            if attempt < _RETRY_MAX - 1:
                logger.warning(
                    "  %s → error (%s), retry %d/%d...",
                    name,
                    str(exc)[:80],
                    attempt + 1,
                    _RETRY_MAX,
                )
                time.sleep(_RETRY_WAIT)
            else:
                logger.error("  %s → error after retries: %s", name, exc)
                status = "down"

    with state.services_lock:
        for svc in state.services:
            if svc["id"] == service_id:
                svc["status"] = status
                svc["response_time_ms"] = response_time_ms
                svc["last_check"] = datetime.now(timezone.utc).isoformat()
                svc["total_checks"] += 1
                if success:
                    svc["successful_checks"] += 1
                if svc["total_checks"] > 0:
                    svc["uptime_percent"] = round(
                        (svc["successful_checks"] / svc["total_checks"]) * 100,
                        2,
                    )
                break


def check_single_service(service: dict) -> None:
    """Manual single-service check from the dashboard."""
    if not service.get("enabled", True):
        logger.info("  Skipping %s — service disabled.", service.get("name", "?"))
        return
    _check_single(service)


def check_all_services() -> None:
    """Check all enabled services in parallel."""
    global _check_running

    if not _check_lock.acquire(blocking=False):
        logger.warning("check_all_services() already running — skipping.")
        return

    try:
        _check_running = True

        with state.services_lock:
            snapshot = [svc.copy() for svc in state.services]

        active = [svc for svc in snapshot if svc.get("enabled", True)]
        skipped = len(snapshot) - len(active)

        if not active:
            logger.info("No active services to check.")
            return

        logger.info(
            "Checking %d services (%d skipped)...",
            len(active),
            skipped,
        )

        with ThreadPoolExecutor(max_workers=len(active)) as ex:
            futures = {ex.submit(_check_single, svc): svc["name"] for svc in active}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    logger.warning("Check error for %s: %s", futures[f], exc)

        with state.services_lock:
            state.last_check_time = datetime.now(timezone.utc).isoformat()

        logger.info("All checks complete.")

    finally:
        _check_running = False
        _check_lock.release()


def scheduled_check() -> None:
    """Auto-ping scheduler callback — runs at ping_interval if auto_ping_enabled."""
    with state.services_lock:
        enabled = state.auto_ping_enabled

    if not enabled:
        logger.debug("Auto-ping disabled — skipping scheduled check.")
        return

    check_all_services()


def business_hours_check() -> None:
    """Business hours keepalive callback — runs every 12 min, independent of auto-ping.

    Pings all enabled services during the configured BH window.
    Does nothing outside the window or when BH is disabled.
    """
    with state.services_lock:
        bh_enabled = state.business_hours_enabled
        bh_start = state.business_hours_start
        bh_end = state.business_hours_end

    if not bh_enabled:
        return

    try:
        tz = pytz.timezone(BUSINESS_HOURS_TIMEZONE)
        now = datetime.now(tz)
        cur = now.hour * 60 + now.minute
        ws = bh_start * 60
        we = bh_end * 60

        if bh_start < bh_end:
            in_window = ws <= cur < we
        else:
            in_window = cur >= ws or cur < we

        if not in_window:
            logger.debug(
                "BH keepalive: outside window (%02d:00-%02d:00 %s, now %02d:%02d) — skipping.",
                bh_start,
                bh_end,
                BUSINESS_HOURS_TIMEZONE,
                now.hour,
                now.minute,
            )
            return

        logger.debug(
            "BH keepalive: within window (%02d:00-%02d:00 %s, now %02d:%02d) — pinging.",
            bh_start,
            bh_end,
            BUSINESS_HOURS_TIMEZONE,
            now.hour,
            now.minute,
        )
    except Exception as exc:
        logger.warning("BH timezone check failed (%s) — skipping BH keepalive.", exc)
        return

    check_all_services()
