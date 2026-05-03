"""
Dashboard route — serves the main HTML page.
"""
from __future__ import annotations

from flask import Blueprint, render_template

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="../templates",   # pulse/templates/
)


@dashboard_bp.get("/")
def dashboard():
    """Serve the Shellty Pulse dashboard."""
    return render_template("dashboard.html")