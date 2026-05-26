"""
Background scheduler lifecycle and graceful shutdown.

On Oracle Cloud the scheduler is the primary ping mechanism.
auto_ping_enabled defaults to False — enable via dashboard.
"""

from __future__ import annotations

import atexit
import json
import logging
import signal

from apscheduler.schedulers.background import BackgroundScheduler

from pulse import state
from pulse.config import (
    DISABLE_SCHEDULER,
    MAX_SERVICES,
    PORT,
    REQUEST_TIMEOUT,
    SERVICES_JSON,
    VERSION,
)
from pulse.models import create_service

logger = logging.getLogger("shellty-pulse")

scheduler: BackgroundScheduler | None = None


# ── Graceful shutdown ────────────────────────────────────────────────────────


def graceful_shutdown(signum=None, frame=None) -> None:
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
    """Parse the SERVICES environment variable and seed the service list."""
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
    """Seed services from env, restore persisted state, and start APScheduler."""
    global scheduler

    logger.info("=" * 50)
    logger.info("  Starting Shellty Pulse — Service Health Monitor")
    logger.info("  Version: %s", VERSION)
    logger.info("=" * 50)
    logger.info("Configuration:")
    logger.info("  PORT:            %d", PORT)
    logger.info("  REQUEST_TIMEOUT: %d seconds", REQUEST_TIMEOUT)
    logger.info("  MAX_SERVICES:    %d", MAX_SERVICES)

    load_services_from_env()

    from pulse.persistence import load_state

    load_state(state)

    logger.info(
        "  PING_INTERVAL:   %d seconds (%d min)",
        state.ping_interval,
        state.ping_interval // 60,
    )
    logger.info(
        "  AUTO_PING:       %s",
        "enabled" if state.auto_ping_enabled else "disabled",
    )
    logger.info(
        "  BUSINESS_HOURS:  %s (%02d:00-%02d:00)",
        "enabled" if state.business_hours_enabled else "disabled",
        state.business_hours_start,
        state.business_hours_end,
    )

    if DISABLE_SCHEDULER:
        logger.info("DISABLE_SCHEDULER=true — scheduler will not start.")
        return

    from pulse.checker import business_hours_check, scheduled_check

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=scheduled_check,
        trigger="interval",
        seconds=state.ping_interval,
        id="health_check_job",
        name="Auto-Ping Health Check",
        replace_existing=True,
    )
    scheduler.add_job(
        func=business_hours_check,
        trigger="interval",
        seconds=720,
        id="business_hours_job",
        name="Business Hours Keepalive",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started — auto_ping %s (%ds), BH keepalive %s (720s).",
        "enabled" if state.auto_ping_enabled else "disabled",
        state.ping_interval,
        "enabled" if state.business_hours_enabled else "disabled",
    )
    logger.info("=" * 50)
