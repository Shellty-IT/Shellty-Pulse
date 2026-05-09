"""
Shellty Pulse — Flask application factory.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from flask import Flask, jsonify

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
        with state.services_lock:
            total = len(state.services)

        uptime = int(time.time() - _start_time)

        return jsonify({
            "status": "ok",                    # ← zmienione z "healthy"
            "service": "shellty-pulse",        # ← dodane
            "version": VERSION,                # ← dodane
            "uptime_seconds": uptime,          # ← dodane
            "services_count": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200

    from pulse.routes.api import api_bp
    from pulse.routes.dashboard import dashboard_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)

    return app