"""
Shellty Pulse — Service Health Monitor.

Public API of the package:
    from pulse import create_app
"""
from __future__ import annotations

import logging
import os
import time

from flask import Flask, jsonify
from datetime import datetime, timezone

from pulse.config import VERSION
from pulse.models import get_overall_status
from pulse import state

# ── Logging (configure once at package import) ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

APP_START_TIME = time.time()


def create_app(testing: bool = False) -> Flask:
    """
    Flask application factory.

    Args:
        testing: When ``True`` the scheduler and initial health checks
                 are **not** started.  Use this in unit / integration tests::

                     app = create_app(testing=True)
                     client = app.test_client()

    Returns:
        A fully configured Flask application instance.
    """
    from pulse.routes import api_bp, dashboard_bp
    from pulse.scheduler import scheduler as _scheduler

    app = Flask(
        __name__,
        template_folder="templates",  # pulse/templates/
    )

    # ── Register blueprints ──────────────────────────────────────────────────
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(api_bp)

    # ── Self health-check endpoint ───────────────────────────────────────────
    @app.get("/health")
    def health():
        """
        Liveness probe — always returns HTTP 200.

        Used by Docker HEALTHCHECK, load balancers, and external monitors.
        """
        with state.services_lock:
            total = len(state.services)

        return jsonify({
            "status":              "ok",
            "timestamp":           datetime.now(timezone.utc).isoformat(),
            "service":             "shellty-pulse",
            "version":             VERSION,
            "uptime_seconds":      round(time.time() - APP_START_TIME),
            "monitored_services":  total,
            "scheduler_running":   _scheduler.running if _scheduler else False,
        }), 200

    return app