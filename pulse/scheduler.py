"""
Background scheduler lifecycle and graceful shutdown.

NOTE: On Render.com the APScheduler cannot reliably wake a sleeping app.
      GitHub Actions is used instead as the external wake mechanism.
      The scheduler is kept as a local fallback only (auto_ping_enabled=False
      by default so it does nothing unless explicitly enabled via dashboard).
"""
from __future__ import annotations

import atexit
import json
import logging
import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler

from pulse import state
from pulse.checker import check_all_services, scheduled_check
from pulse.config import (
    PING_INTERVAL_DEFAULT,
    PORT,
    REQUEST_TIMEOUT,
    MAX_SERVICES,
    SERVICES_JSON,
)
from pulse.models import create_service

logger = logging.getLogger("shellty-pulse")

# Module-level scheduler reference — set by ``start_background_services()``.
scheduler: BackgroundScheduler | None = None


# ── Graceful shutdown ────────────────────────────────────────────────────────

def graceful_shutdown(signum=None, frame=None) -> None:
    """
    Stop the background scheduler cleanly on SIGTERM / SIGINT / process exit.
    """
    logger.info("Shutting down gracefully...")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    logger.info("Shutdown complete.")


atexit.register(graceful_shutdown)

try:
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT, graceful_shutdown)
except (ValueError, OSError):
    pass


# ── Environment pre-loading ──────────────────────────────────────────────────

def load_services_from_env() -> None:
    """
    Parse the ``SERVICES`` environment variable and seed the service list.
    """
    try:
        parsed = json.loads(SERVICES_JSON)
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse SERVICES env var: %s", exc)
        return

    if not isinstance(parsed, list):
        logger.error("SERVICES env var is not a JSON array — ignoring.")
        return

    for item in parsed:
        if isinstance(item, dict) and "name" in item and "url" in item:
            svc = create_service(
                name=item["name"],
                url=item["url"],
                frontend_url=item.get("frontend_url"),
            )
            with state.services_lock:
                state.services.append(svc)
            logger.info("  Preloaded: %s → %s", item["name"], item["url"])
        else:
            logger.warning("  Skipping invalid entry: %s", item)

    with state.services_lock:
        count = len(state.services)
    logger.info("Loaded %d services from environment.", count)


# ── Public entry point ───────────────────────────────────────────────────────

def start_background_services() -> None:
    """
    Seed services from env, start the APScheduler (as local fallback).

    IMPORTANT: auto_ping_enabled defaults to False — the scheduler will
    run its job on schedule but scheduled_check() will skip execution
    unless auto_ping_enabled is True.

    On Render.com, GitHub Actions is the primary wake mechanism.
    The scheduler here acts only as a local/dev fallback.
    """
    global scheduler

    from pulse.config import VERSION

    logger.info("=" * 50)
    logger.info("  Starting Shellty Pulse — Service Health Monitor")
    logger.info("  Version: %s", VERSION)
    logger.info("=" * 50)
    logger.info("Configuration:")
    logger.info("  PORT:            %d", PORT)
    logger.info(
        "  PING_INTERVAL:   %d seconds (%d min)",
        PING_INTERVAL_DEFAULT,
        PING_INTERVAL_DEFAULT // 60,
    )
    logger.info("  REQUEST_TIMEOUT: %d seconds", REQUEST_TIMEOUT)
    logger.info("  MAX_SERVICES:    %d", MAX_SERVICES)
    logger.info("  AUTO_PING:       disabled by default (GitHub Actions mode)")

    load_services_from_env()

    # Start scheduler as fallback — but auto_ping_enabled=False means
    # scheduled_check() will log "disabled — skipping" and do nothing.
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=scheduled_check,
        trigger="interval",
        seconds=state.ping_interval,
        id="health_check_job",
        name="Periodic Health Check (local fallback)",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started (local fallback) — auto_ping disabled by default."
    )
    logger.info("Primary wake mechanism: GitHub Actions")
    logger.info("=" * 50)