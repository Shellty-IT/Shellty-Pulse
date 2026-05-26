"""
In-memory application state — single source of truth.

All mutable globals live here.
Every read/write MUST happen under ``services_lock``.

Import pattern (always use module-level access):
    from pulse import state
    with state.services_lock:
        state.services.append(svc)
"""

from __future__ import annotations

import threading

from pulse.config import (
    AUTO_PING_ENABLED_DEFAULT,
    BUSINESS_HOURS_ENABLED_DEFAULT,
    BUSINESS_HOURS_END_DEFAULT,
    BUSINESS_HOURS_START_DEFAULT,
    PING_INTERVAL_DEFAULT,
)

# ── Thread synchronisation ───────────────────────────────────────────────────
services_lock = threading.Lock()

# ── Monitored services list ──────────────────────────────────────────────────
# Each element is a dict produced by pulse.models.create_service()
services: list[dict] = []

# ── Runtime settings (always access under services_lock) ─────────────────────
ping_interval: int = PING_INTERVAL_DEFAULT
auto_ping_enabled: bool = AUTO_PING_ENABLED_DEFAULT
last_check_time: str | None = None

# ── Business Hours settings ──────────────────────────────────────────────────
business_hours_enabled: bool = BUSINESS_HOURS_ENABLED_DEFAULT
business_hours_start: int = BUSINESS_HOURS_START_DEFAULT
business_hours_end: int = BUSINESS_HOURS_END_DEFAULT
