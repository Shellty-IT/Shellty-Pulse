"""
Persistent state storage — saves/loads runtime settings to a JSON file.

Persists across container restarts via a Docker volume mounted at DATA_DIR.
Services are always reloaded from the SERVICES env var on start; only mutable
runtime settings (auto-ping flag, interval, business hours, per-service
disabled list) live here.

File location: DATA_DIR/state.json  (default: /data/state.json)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger("shellty-pulse")

_TESTING: bool = os.environ.get("TESTING", "").lower() in ("1", "true")
_DATA_DIR: Path = Path(os.environ.get("DATA_DIR", "/data"))
_STATE_FILE: Path = _DATA_DIR / "state.json"


def save_state(st) -> None:
    """
    Persist current runtime settings to disk.

    Reads state under the services_lock, then writes the JSON file
    outside the lock to minimise lock-hold time.
    No-op when TESTING=1.
    """
    if _TESTING:
        return

    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        with st.services_lock:
            payload = {
                "version": 1,
                "auto_ping_enabled": st.auto_ping_enabled,
                "ping_interval": st.ping_interval,
                "business_hours_enabled": st.business_hours_enabled,
                "business_hours_start": st.business_hours_start,
                "business_hours_end": st.business_hours_end,
                "disabled_service_urls": [
                    svc["url"] for svc in st.services if not svc.get("enabled", True)
                ],
            }
        _STATE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        logger.debug("State saved to %s.", _STATE_FILE)
    except Exception as exc:
        logger.warning("Could not save state to %s: %s", _STATE_FILE, exc)


def load_state(st) -> None:
    """
    Load persisted settings into state.

    Must be called AFTER services are seeded from env (so that
    per-service disabled flags can be matched by URL).
    No-op when TESTING=1 or when no state file exists yet.
    """
    if _TESTING:
        return

    if not _STATE_FILE.exists():
        logger.info("No persisted state at %s — using env defaults.", _STATE_FILE)
        return

    try:
        payload = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(
            "Could not read state from %s: %s — using env defaults.",
            _STATE_FILE,
            exc,
        )
        return

    from pulse.config import AVAILABLE_INTERVALS

    with st.services_lock:
        if "auto_ping_enabled" in payload:
            st.auto_ping_enabled = bool(payload["auto_ping_enabled"])

        if "ping_interval" in payload:
            interval = int(payload["ping_interval"])
            if interval in AVAILABLE_INTERVALS:
                st.ping_interval = interval
            else:
                logger.warning(
                    "Persisted interval %ds not in allowed list — keeping default.",
                    interval,
                )

        if "business_hours_enabled" in payload:
            st.business_hours_enabled = bool(payload["business_hours_enabled"])

        if "business_hours_start" in payload:
            st.business_hours_start = int(payload["business_hours_start"])

        if "business_hours_end" in payload:
            st.business_hours_end = int(payload["business_hours_end"])

        # Restore per-service disabled flags (keyed by URL; UUIDs change on restart)
        disabled_urls = set(payload.get("disabled_service_urls", []))
        if disabled_urls:
            for svc in st.services:
                if svc["url"] in disabled_urls:
                    svc["enabled"] = False
                    svc["status"] = "disabled"
                    svc["response_time_ms"] = None

    logger.info(
        "State loaded: auto_ping=%s, interval=%ds, BH=%s (%02d:00-%02d:00).",
        st.auto_ping_enabled,
        st.ping_interval,
        st.business_hours_enabled,
        st.business_hours_start,
        st.business_hours_end,
    )
