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
    GITHUB_TOKEN,
    GITHUB_REPO,
    BUSINESS_HOURS_ENABLED_DEFAULT,
    BUSINESS_HOURS_START_DEFAULT,
    BUSINESS_HOURS_END_DEFAULT,
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


# ── GitHub Variables sync ────────────────────────────────────────────────────

def load_business_hours_from_github() -> None:
    """
    Read BH_ENABLED, BH_START, BH_END from GitHub Actions Variables
    and apply them to state on startup.

    This synchronises the in-memory state with GitHub Variables so that
    business hours survive container restarts on Render.com.

    Requires GITHUB_TOKEN and GITHUB_REPO env vars.
    Falls back to config.py defaults silently if unavailable.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        logger.info(
            "BH sync skipped: GITHUB_TOKEN or GITHUB_REPO not set — "
            "using defaults (enabled=%s, %d:00–%d:00).",
            BUSINESS_HOURS_ENABLED_DEFAULT,
            BUSINESS_HOURS_START_DEFAULT,
            BUSINESS_HOURS_END_DEFAULT,
        )
        return

    try:
        import requests as http_requests

        base    = f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables"
        headers = {
            "Authorization":        f"Bearer {GITHUB_TOKEN}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        def _get_var(name: str) -> str | None:
            resp = http_requests.get(
                f"{base}/{name}", headers=headers, timeout=10
            )
            if resp.status_code == 200:
                return resp.json().get("value")
            if resp.status_code == 404:
                logger.info("GitHub Variable %s not found — using default.", name)
                return None
            logger.warning(
                "GitHub Variable %s: HTTP %d — using default.",
                name, resp.status_code,
            )
            return None

        bh_enabled_raw = _get_var("BH_ENABLED")
        bh_start_raw   = _get_var("BH_START")
        bh_end_raw     = _get_var("BH_END")

        # Parse — fall back to defaults on any parse error
        bh_enabled = (
            bh_enabled_raw.lower() == "true"
            if bh_enabled_raw is not None
            else BUSINESS_HOURS_ENABLED_DEFAULT
        )
        try:
            bh_start = int(bh_start_raw) if bh_start_raw is not None else BUSINESS_HOURS_START_DEFAULT
            bh_end   = int(bh_end_raw)   if bh_end_raw   is not None else BUSINESS_HOURS_END_DEFAULT
        except ValueError:
            logger.warning(
                "Invalid BH_START/BH_END in GitHub Variables — using defaults."
            )
            bh_start = BUSINESS_HOURS_START_DEFAULT
            bh_end   = BUSINESS_HOURS_END_DEFAULT

        # Validate range
        if not (0 <= bh_start <= 23) or not (0 <= bh_end <= 23):
            logger.warning(
                "BH_START=%d or BH_END=%d out of range — using defaults.",
                bh_start, bh_end,
            )
            bh_start = BUSINESS_HOURS_START_DEFAULT
            bh_end   = BUSINESS_HOURS_END_DEFAULT

        with state.services_lock:
            state.business_hours_enabled = bh_enabled
            state.business_hours_start   = bh_start
            state.business_hours_end     = bh_end

        logger.info(
            "✅ Business hours loaded from GitHub Variables: "
            "enabled=%s, %02d:00–%02d:00 CET",
            bh_enabled, bh_start, bh_end,
        )

    except Exception as exc:
        logger.warning(
            "Failed to load BH from GitHub Variables: %s — using defaults.",
            exc,
        )


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
    Seed services from env, sync BH from GitHub Variables, start APScheduler.

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

    # Sync business hours from GitHub Variables — survives container restarts
    logger.info("Syncing business hours from GitHub Variables...")
    load_business_hours_from_github()

    # Start scheduler as fallback
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