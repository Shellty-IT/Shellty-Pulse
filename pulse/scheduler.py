"""
Background scheduler lifecycle and graceful shutdown.

Keeps the APScheduler instance and all signal/atexit wiring
out of the application factory.
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
from pulse.config import PING_INTERVAL_DEFAULT, PORT, REQUEST_TIMEOUT, MAX_SERVICES, SERVICES_JSON
from pulse.models import create_service

logger = logging.getLogger("shellty-pulse")

# Module-level scheduler reference — set by ``start_background_services()``.
scheduler: BackgroundScheduler | None = None


# ── Graceful shutdown ────────────────────────────────────────────────────────

def graceful_shutdown(signum=None, frame=None) -> None:
    """
    Stop the background scheduler cleanly on SIGTERM / SIGINT / process exit.

    Registered via ``atexit`` and ``signal.signal`` so it is called
    regardless of how the process terminates.
    """
    logger.info("Shutting down gracefully...")
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
    logger.info("Shutdown complete.")


atexit.register(graceful_shutdown)

try:
    signal.signal(signal.SIGTERM, graceful_shutdown)
    signal.signal(signal.SIGINT,  graceful_shutdown)
except (ValueError, OSError):
    # signal.signal() may only be called from the main thread.
    pass


# ── Environment pre-loading ──────────────────────────────────────────────────

def load_services_from_env() -> None:
    """
    Parse the ``SERVICES`` environment variable and seed the service list.

    Expected format — JSON array::

        [
          {"name": "App", "url": "https://api.example.com/health"},
          {"name": "Site", "url": "https://site.example.com/health",
           "frontend_url": "https://site.example.com"}
        ]

    Invalid or malformed entries are skipped with a warning.
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
            if item.get("frontend_url"):
                logger.info("    Frontend: %s", item["frontend_url"])
        else:
            logger.warning("  Skipping invalid entry: %s", item)

    with state.services_lock:
        count = len(state.services)
    logger.info("Loaded %d services from environment.", count)


# ── Public entry point ───────────────────────────────────────────────────────

def start_background_services() -> None:
    """
    Seed services from env, start the APScheduler, kick off first ping.

    Intentionally **not** called inside ``create_app()`` so that the
    test suite can instantiate the Flask app without making real HTTP
    requests or starting background threads.

    Note:
        gunicorn MUST be started with ``--workers 1`` because the scheduler
        and in-memory state are process-local.
    """
    global scheduler

    from pulse.config import VERSION  # avoid top-level circular risk

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

    load_services_from_env()

    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=scheduled_check,
        trigger="interval",
        seconds=state.ping_interval,
        id="health_check_job",
        name="Periodic Health Check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — checking every %d seconds.", state.ping_interval)

    # First check runs in background so startup is non-blocking
    threading.Thread(target=check_all_services, daemon=True).start()
    logger.info("Initial health check started in background.")
    logger.info("=" * 50)