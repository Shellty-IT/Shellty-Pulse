"""
Configuration — all constants and environment variables in one place.

Never import from other pulse modules here (avoid circular imports).
"""

from __future__ import annotations

import os

# ── Application metadata ────────────────────────────────────────────────────
VERSION = "1.0.0"

# ── Runtime settings from environment ───────────────────────────────────────
PORT = int(os.environ.get("PORT", 5000))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 90))
MAX_SERVICES = int(os.environ.get("MAX_SERVICES", 50))
PING_INTERVAL_DEFAULT = int(os.environ.get("PING_INTERVAL", 900))
SERVICES_JSON = os.environ.get("SERVICES", "[]")

# ── Scheduler control ───────────────────────────────────────────────────────
DISABLE_SCHEDULER = os.environ.get("DISABLE_SCHEDULER", "").lower() in ("1", "true")

# ── Input length limits ──────────────────────────────────────────────────────
MAX_NAME_LENGTH = 100
MAX_URL_LENGTH = 2048

# ── Available ping intervals: seconds → human label ─────────────────────────
AVAILABLE_INTERVALS: dict[int, str] = {
    600: "10 min",
    900: "15 min",
    1800: "30 min",
    3600: "1 hour",
    86400: "24 hours",
    172800: "48 hours",
}

# ── SSRF protection — blocked hostnames ─────────────────────────────────────
BLOCKED_HOSTS: frozenset[str] = frozenset(
    {
        "localhost",
        "127.0.0.1",
        "0.0.0.0",
        "169.254.169.254",
        "metadata.google.internal",
    }
)

# ── Business Hours defaults ──────────────────────────────────────────────────
BUSINESS_HOURS_ENABLED_DEFAULT: bool = os.environ.get(
    "BUSINESS_HOURS_ENABLED", "false"
).lower() in ("1", "true")
BUSINESS_HOURS_START_DEFAULT: int = int(os.environ.get("BUSINESS_HOURS_START", 8))
BUSINESS_HOURS_END_DEFAULT: int = int(os.environ.get("BUSINESS_HOURS_END", 20))
BUSINESS_HOURS_TIMEZONE: str = os.environ.get(
    "BUSINESS_HOURS_TIMEZONE", "Europe/Warsaw"
)
