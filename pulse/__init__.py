"""
Shellty Pulse — Flask application factory.
"""
from __future__ import annotations

from flask import Flask


def create_app(testing: bool = False) -> Flask:
    """Application factory."""
    app = Flask(
        __name__,
        static_folder='static',
        static_url_path='/static',
        template_folder='templates'
    )

    # Load config
    from pulse.config import VERSION
    app.config['VERSION'] = VERSION
    app.config['TESTING'] = testing

    # Register blueprints
    from pulse.routes.api import api_bp
    from pulse.routes.dashboard import dashboard_bp

    app.register_blueprint(api_bp)
    app.register_blueprint(dashboard_bp)

    return app