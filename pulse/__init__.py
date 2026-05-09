"""
Shellty Pulse — Flask application factory.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from flask import Flask, jsonify

# Track app start time for uptime calculation
_start_time = time.time()


def create_app(testing: bool = False) -> Flask:
    """Application factory."""
    app = Flask(
        __name__,
        static_folder='static',
        static_url_path='/static',
        template_folder='templates'
    )

    from pulse.config import VERSION
    app.config['VERSION'] = VERSION
    app.config['TESTING'] = testing

    @app.route('/health')
    def health_check():
        """Health check for Docker/Render and CI tests."""
        from pulse import state
        from pulse.scheduler import scheduler

        with state.services_lock:
            total = len(state.services)

        # Check if scheduler is running
        scheduler_running = False
        if scheduler is not None:
            scheduler_running = scheduler.running

        uptime = int(time.time() - _start_time)

        return jsonify({
            "status": "ok",
            "service": "shellty-pulse",
            "version": VERSION,
            "uptime_seconds": uptime,
            "monitored_services": total,         # ← DODANE
            "scheduler_running": scheduler_running,  # ← DODANE
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200

    from pulse.routes.api import api_bp
    from pulse.routes.dashboard import dashboard_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)

    return app