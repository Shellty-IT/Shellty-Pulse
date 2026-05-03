from flask import jsonify
from pulse.routes.api import api_bp
from pulse.routes.dashboard import dashboard_bp

__all__ = ["api_bp", "dashboard_bp", "register_routes"]

def register_routes(app):
    """Register all application routes"""
    app.register_blueprint(api_bp, url_prefix='/api')
    app.register_blueprint(dashboard_bp)

    # Health check endpoint
    @app.route('/health', methods=['GET'])
    def health():
        return jsonify({
            'status': 'healthy',
            'service': 'Shellty Pulse'
        }), 200