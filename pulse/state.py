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
from pulse.config import PING_INTERVAL_DEFAULT

# ── Thread synchronisation ───────────────────────────────────────────────────
services_lock = threading.Lock()

# ── Monitored services list ──────────────────────────────────────────────────
# Each element is a dict produced by pulse.models.create_service()
services: list[dict] = []

# ── Runtime settings (always access under services_lock) ─────────────────────
ping_interval: int = PING_INTERVAL_DEFAULT
auto_ping_enabled: bool = True
last_check_time: str | None = None