"""
Shellty Pulse — Flask application factory.
"""
from __future__ import annotations

from datetime import datetime, timezone
from flask import Flask, jsonify


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
        """Health check for Docker/Render."""
        from pulse import state
        with state.services_lock:
            total = len(state.services)
        return jsonify({
            "status": "healthy",
            "services_count": total,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }), 200
    # ────────────────────────────────────────────────────────────────────────

    from pulse.routes.api import api_bp
    from pulse.routes.dashboard import dashboard_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)

    return app