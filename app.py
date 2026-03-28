"""
Shellty Pulse — Service Health Monitor

Narzędzie do monitorowania dostępności usług webowych.
Pełni jednocześnie funkcję keep-alive dla backendów na Render Free Tier
(które usypiają po 15 min bez ruchu).

Features:
    - Pings registered URLs every X minutes (configurable)
    - Measures response time and records status
    - Displays HTML dashboard on the main page
    - Provides REST API for service management
    - Configurable ping interval from dashboard (10m / 15m / 30m / 1h)

Author: Shellty IT
"""

# ============================================
# Imports
# ============================================
import os
import json
import uuid
import time
import logging
import threading
from datetime import datetime, timezone

import requests as http_requests
from flask import Flask, jsonify, request, render_template_string
from apscheduler.schedulers.background import BackgroundScheduler

# ============================================
# Logging Configuration
# ============================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("shellty-pulse")

# ============================================
# Configuration from Environment Variables
# ============================================
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", 10))    # timeout per request in seconds
SERVICES_JSON = os.environ.get("SERVICES", "[]")                # preloaded services JSON

# Mutable ping interval — can be changed via API at runtime
ping_interval: int = int(os.environ.get("PING_INTERVAL", 600))

# Available intervals for the dashboard selector (seconds → label)
AVAILABLE_INTERVALS = {
    600: "10 min",
    900: "15 min",
    1800: "30 min",
    3600: "1 hour",
}

# ============================================
# In-Memory Data Store
# ============================================
services: list[dict] = []
services_lock = threading.Lock()
auto_ping_enabled: bool = True
last_check_time: str | None = None

# Global scheduler reference (initialized in start_app)
scheduler: BackgroundScheduler | None = None

# ============================================
# Dashboard HTML Template
# ============================================
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Shellty Pulse</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>💓</text></svg>">
    <style>
        /* === Reset & Base === */
        * { margin: 0; padding: 0; box-sizing: border-box; }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #0d1117;
            color: #c9d1d9;
            min-height: 100vh;
            padding: 2rem;
        }

        .container { max-width: 920px; margin: 0 auto; }

        /* === Header === */
        header { text-align: center; margin-bottom: 2rem; }

        header h1 {
            font-size: 2.2rem;
            color: #f0f6fc;
            margin-bottom: 0.3rem;
        }

        .pulse-icon {
            display: inline-block;
            animation: pulse 2s ease-in-out infinite;
        }

        @keyframes pulse {
            0%, 100% { transform: scale(1); }
            50% { transform: scale(1.2); }
        }

        .subtitle { color: #8b949e; font-size: 0.95rem; }

        /* === Overall Status Banner === */
        .overall-status {
            text-align: center;
            padding: 1rem 2rem;
            border-radius: 12px;
            margin-bottom: 1rem;
            font-size: 1.1rem;
            font-weight: 600;
            border: 1px solid #30363d;
        }

        .overall-status.operational { background: #0d1f0d; border-color: #238636; color: #3fb950; }
        .overall-status.degraded    { background: #1f1d0d; border-color: #9e6a03; color: #d29922; }
        .overall-status.slow        { background: #1f160d; border-color: #bd5a00; color: #db6d28; }
        .overall-status.down        { background: #1f0d0d; border-color: #da3633; color: #f85149; }
        .overall-status.unknown     { background: #161b22; border-color: #30363d; color: #8b949e; }

        /* === Status Legend === */
        .status-legend {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem 1.25rem;
            justify-content: center;
            align-items: center;
            margin-bottom: 1.5rem;
            padding: 0.6rem 1rem;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            font-size: 0.8rem;
        }

        .legend-item {
            display: flex;
            align-items: center;
            gap: 0.35rem;
            color: #8b949e;
        }

        .legend-dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            display: inline-block;
        }

        .legend-dot.operational { background: #3fb950; }
        .legend-dot.degraded    { background: #d29922; }
        .legend-dot.slow        { background: #db6d28; }
        .legend-dot.down        { background: #f85149; }
        .legend-dot.unknown     { background: #484f58; }

        .legend-desc { color: #6e7681; }

        /* === Control Buttons === */
        .controls {
            display: flex;
            flex-wrap: wrap;
            gap: 0.75rem;
            align-items: center;
            justify-content: center;
            margin-bottom: 1.5rem;
        }

        .btn {
            padding: 0.6rem 1.2rem;
            border: 1px solid #30363d;
            border-radius: 8px;
            background: #21262d;
            color: #c9d1d9;
            cursor: pointer;
            font-size: 0.9rem;
            transition: all 0.2s;
        }

        .btn:hover          { background: #30363d; border-color: #58a6ff; }
        .btn:disabled        { opacity: 0.5; cursor: not-allowed; }
        .btn.primary         { background: #238636; border-color: #238636; color: #fff; }
        .btn.primary:hover   { background: #2ea043; }
        .btn.active          { background: #1f6feb; border-color: #1f6feb; color: #fff; }
        .btn.inactive        { background: #21262d; border-color: #f85149; color: #f85149; }

        /* === Interval Selector === */
        .interval-selector {
            display: flex;
            align-items: center;
            gap: 0.4rem;
            flex-wrap: wrap;
        }

        .interval-label {
            color: #8b949e;
            font-size: 0.8rem;
            margin-right: 0.2rem;
        }

        .interval-btn {
            padding: 0.3rem 0.65rem;
            border: 1px solid #30363d;
            border-radius: 6px;
            background: #21262d;
            color: #8b949e;
            cursor: pointer;
            font-size: 0.78rem;
            transition: all 0.2s;
        }

        .interval-btn:hover        { border-color: #58a6ff; color: #c9d1d9; }
        .interval-btn.active        { background: #1f6feb; border-color: #1f6feb; color: #fff; }
        .interval-btn:disabled       { opacity: 0.4; cursor: not-allowed; }
        .interval-btn:disabled:hover { border-color: #30363d; color: #8b949e; }

        .check-info {
            color: #8b949e;
            font-size: 0.85rem;
            text-align: center;
            width: 100%;
        }

        /* === Service Cards === */
        .services-grid {
            display: flex;
            flex-direction: column;
            gap: 0.75rem;
            margin-bottom: 2rem;
        }

        .service-card {
            display: flex;
            align-items: center;
            gap: 1rem;
            padding: 1rem 1.25rem;
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            transition: border-color 0.2s;
        }

        .service-card:hover { border-color: #58a6ff; }
        .status-icon        { font-size: 1.5rem; flex-shrink: 0; }

        .service-info { flex: 1; min-width: 0; }

        .service-name {
            font-weight: 600;
            color: #f0f6fc;
            font-size: 1rem;
            margin-bottom: 0.2rem;
        }

        .service-url {
            color: #58a6ff;
            font-size: 0.8rem;
            word-break: break-all;
        }

        .service-meta {
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 0.3rem;
            flex-shrink: 0;
        }

        .response-time { font-size: 0.85rem; font-weight: 600; color: #8b949e; }
        .response-time.fast    { color: #3fb950; }
        .response-time.medium  { color: #d29922; }
        .response-time.slow    { color: #db6d28; }
        .response-time.timeout { color: #f85149; }

        .uptime-bar {
            width: 80px; height: 6px;
            background: #21262d;
            border-radius: 3px;
            overflow: hidden;
        }

        .uptime-fill {
            height: 100%;
            border-radius: 3px;
            transition: width 0.3s;
        }

        .uptime-text { font-size: 0.7rem; color: #8b949e; }

        .service-actions { display: flex; gap: 0.4rem; flex-shrink: 0; }

        .btn-icon {
            width: 36px; height: 36px;
            display: flex; align-items: center; justify-content: center;
            border: 1px solid #30363d;
            border-radius: 8px;
            background: #21262d;
            color: #c9d1d9;
            cursor: pointer;
            font-size: 1rem;
            transition: all 0.2s;
        }

        .btn-icon:hover        { background: #30363d; border-color: #58a6ff; }
        .btn-icon.delete:hover { border-color: #f85149; color: #f85149; }

        .btn-icon.spinning { animation: spin 1s linear infinite; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }

        /* === Add Service Form === */
        .add-service {
            background: #161b22;
            border: 1px solid #30363d;
            border-radius: 10px;
            padding: 1.25rem;
        }

        .add-service h3 { color: #f0f6fc; margin-bottom: 1rem; font-size: 1rem; }

        .form-row { display: flex; gap: 0.75rem; flex-wrap: wrap; }

        .form-row input {
            flex: 1; min-width: 150px;
            padding: 0.5rem 0.75rem;
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 6px;
            color: #c9d1d9;
            font-size: 0.9rem;
        }

        .form-row input:focus       { outline: none; border-color: #58a6ff; }
        .form-row input::placeholder { color: #484f58; }

        .add-note {
            margin-top: 0.6rem;
            font-size: 0.75rem;
            color: #6e7681;
            font-style: italic;
        }

        /* === Footer === */
        footer {
            text-align: center;
            padding-top: 2rem;
            color: #484f58;
            font-size: 0.8rem;
        }

        .loading { text-align: center; padding: 2rem; color: #8b949e; }

        /* === Responsive === */
        @media (max-width: 600px) {
            body { padding: 1rem; }
            header h1 { font-size: 1.6rem; }
            .service-card { flex-wrap: wrap; }
            .service-meta { flex-direction: row; align-items: center; width: 100%; }
            .service-actions { width: 100%; justify-content: flex-end; }
            .status-legend { font-size: 0.72rem; gap: 0.5rem 0.75rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <!-- Header -->
        <header>
            <h1><span class="pulse-icon">💓</span> Shellty Pulse</h1>
            <p class="subtitle">Service Health Monitor</p>
        </header>

        <!-- Overall Status Banner -->
        <div id="overall-status" class="overall-status unknown">Loading...</div>

        <!-- Status Legend -->
        <div class="status-legend">
            <span class="legend-item"><span class="legend-dot operational"></span> Operational <span class="legend-desc">(&lt; 1s)</span></span>
            <span class="legend-item"><span class="legend-dot degraded"></span> Degraded <span class="legend-desc">(1-3s)</span></span>
            <span class="legend-item"><span class="legend-dot slow"></span> Slow <span class="legend-desc">(&gt; 3s)</span></span>
            <span class="legend-item"><span class="legend-dot down"></span> Down <span class="legend-desc">(error/timeout)</span></span>
            <span class="legend-item"><span class="legend-dot unknown"></span> Unknown <span class="legend-desc">(not checked)</span></span>
        </div>

        <!-- Control Buttons -->
        <div class="controls">
            <button class="btn primary" onclick="checkAll(this)">⟳ Check All Now</button>
            <button id="auto-ping-btn" class="btn active" onclick="toggleAutoPing()">
                ⏱ Auto-Ping: ON
            </button>
            <div class="interval-selector">
                <span class="interval-label">Interval:</span>
                <button class="interval-btn" data-interval="600" onclick="setPingInterval(600)">10 min</button>
                <button class="interval-btn" data-interval="900" onclick="setPingInterval(900)">15 min</button>
                <button class="interval-btn" data-interval="1800" onclick="setPingInterval(1800)">30 min</button>
                <button class="interval-btn" data-interval="3600" onclick="setPingInterval(3600)">1 hour</button>
            </div>
            <div id="check-info" class="check-info">Loading...</div>
        </div>

        <!-- Services List -->
        <div id="services-grid" class="services-grid">
            <div class="loading">Loading services...</div>
        </div>

        <!-- Add Service Form -->
        <div class="add-service">
            <h3>➕ Add New Service</h3>
            <div class="form-row">
                <input type="text" id="new-name" placeholder="Service name" />
                <input type="text" id="new-url" placeholder="Health check URL (https://...)" />
                <button class="btn primary" onclick="addService()">Add</button>
            </div>
            <p class="add-note">⚠ Services added here are stored in memory and will be reset on container restart.
            To add permanently, update the SERVICES variable in docker-compose.yml.</p>
        </div>

        <footer>Shellty Pulse v1.0 &mdash; Service Health Monitor by Shellty IT</footer>
    </div>

    <script>
        /* ========================================
         * Constants & Status Helpers
         * ======================================== */
        var REFRESH_INTERVAL = 15000;

        var STATUS_CONFIG = {
            operational: { icon: '🟢', label: 'All Systems Operational' },
            degraded:    { icon: '🟡', label: 'Performance Degraded' },
            slow:        { icon: '🟠', label: 'Slow Response Detected' },
            down:        { icon: '🔴', label: 'Service Outage Detected' },
            unknown:     { icon: '⚪', label: 'Status Unknown' }
        };

        function getStatusConfig(status) {
            return STATUS_CONFIG[status] || STATUS_CONFIG.unknown;
        }

        function rtClass(ms) {
            if (ms === null || ms === undefined) return 'timeout';
            if (ms < 1000) return 'fast';
            if (ms <= 3000) return 'medium';
            return 'slow';
        }

        function rtText(ms) {
            if (ms === null || ms === undefined) return '—';
            return ms < 1000 ? ms + 'ms' : (ms / 1000).toFixed(2) + 's';
        }

        function uptimeColor(pct) {
            if (pct >= 95) return '#3fb950';
            if (pct >= 80) return '#d29922';
            if (pct >= 50) return '#db6d28';
            return '#f85149';
        }

        function timeAgo(iso) {
            if (!iso) return 'never';
            var diff = (Date.now() - new Date(iso).getTime()) / 1000;
            if (diff < 60) return Math.round(diff) + 's ago';
            if (diff < 3600) return Math.round(diff / 60) + ' min ago';
            return Math.round(diff / 3600) + 'h ago';
        }

        function escapeHtml(text) {
            var d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        }

        function intervalLabel(seconds) {
            if (seconds >= 3600) return Math.round(seconds / 3600) + 'h';
            return Math.round(seconds / 60) + ' min';
        }

        /* ========================================
         * API Communication (fetch, no reload)
         * ======================================== */
        async function fetchServices() {
            try {
                var res = await fetch('/api/services');
                var data = await res.json();
                renderDashboard(data);
            } catch (err) {
                console.error('Failed to fetch services:', err);
            }
        }

        async function checkService(id) {
            var btn = document.getElementById('check-btn-' + id);
            if (btn) btn.classList.add('spinning');
            try {
                await fetch('/api/services/' + id + '/check', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Check failed:', err);
            }
            if (btn) btn.classList.remove('spinning');
        }

        async function checkAll(btn) {
            btn.disabled = true;
            btn.textContent = '⟳ Checking...';
            try {
                await fetch('/api/check-all', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Check all failed:', err);
            }
            btn.disabled = false;
            btn.textContent = '⟳ Check All Now';
        }

        async function toggleAutoPing() {
            try {
                await fetch('/api/toggle-auto-ping', { method: 'POST' });
                await fetchServices();
            } catch (err) {
                console.error('Toggle failed:', err);
            }
        }

        async function setPingInterval(seconds) {
            try {
                var res = await fetch('/api/ping-interval', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interval: seconds })
                });
                if (res.ok) {
                    await fetchServices();
                } else {
                    var errData = await res.json();
                    alert(errData.error || 'Failed to set interval.');
                }
            } catch (err) {
                console.error('Set interval failed:', err);
            }
        }

        async function addService() {
            var nameVal = document.getElementById('new-name').value.trim();
            var urlVal  = document.getElementById('new-url').value.trim();
            if (!nameVal || !urlVal) { alert('Enter both name and URL.'); return; }

            try {
                var res = await fetch('/api/services', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ name: nameVal, url: urlVal })
                });
                if (res.ok) {
                    document.getElementById('new-name').value = '';
                    document.getElementById('new-url').value = '';
                    await fetchServices();
                } else {
                    var errData = await res.json();
                    alert(errData.error || 'Failed to add service.');
                }
            } catch (err) {
                console.error('Add failed:', err);
            }
        }

        async function deleteService(id) {
            if (!confirm('Delete this service?')) return;
            try {
                await fetch('/api/services/' + id, { method: 'DELETE' });
                await fetchServices();
            } catch (err) {
                console.error('Delete failed:', err);
            }
        }

        /* ========================================
         * Render Dashboard (DOM update)
         * ======================================== */
        function renderDashboard(data) {
            var services = data.services;
            var meta     = data.meta;

            /* Overall status banner */
            var oel = document.getElementById('overall-status');
            var ocfg = getStatusConfig(meta.overall_status);
            oel.className = 'overall-status ' + meta.overall_status;
            oel.textContent = ocfg.icon + ' ' + ocfg.label;

            /* Auto-ping button */
            var apb = document.getElementById('auto-ping-btn');
            if (meta.auto_ping_enabled) {
                apb.className = 'btn active';
                apb.textContent = '⏱ Auto-Ping: ON';
            } else {
                apb.className = 'btn inactive';
                apb.textContent = '⏱ Auto-Ping: OFF';
            }

            /* Interval buttons — highlight active, disable when auto-ping off */
            var intervalBtns = document.querySelectorAll('.interval-btn');
            intervalBtns.forEach(function(btn) {
                var btnInterval = parseInt(btn.getAttribute('data-interval'));
                btn.classList.toggle('active', btnInterval === meta.ping_interval);
                btn.disabled = !meta.auto_ping_enabled;
            });

            /* Check info line */
            var ci = document.getElementById('check-info');
            var intLabel = intervalLabel(meta.ping_interval);
            if (meta.last_check) {
                var ago = timeAgo(meta.last_check);
                if (meta.auto_ping_enabled) {
                    var elapsed = (Date.now() - new Date(meta.last_check).getTime()) / 1000;
                    var remain  = Math.max(0, Math.round((meta.ping_interval - elapsed) / 60));
                    ci.textContent = 'Last check: ' + ago + ' · Next in: ~' + remain + ' min · Auto-ping every ' + intLabel;
                } else {
                    ci.textContent = 'Last check: ' + ago + ' · Auto-ping disabled';
                }
            } else {
                ci.textContent = 'No checks yet · Auto-ping every ' + intLabel;
            }

            /* Services grid */
            var grid = document.getElementById('services-grid');
            if (services.length === 0) {
                grid.innerHTML = '<div class="loading">No services configured. Add one below.</div>';
                return;
            }

            grid.innerHTML = services.map(function(svc) {
                var cfg = getStatusConfig(svc.status);
                var upPct   = svc.uptime_percent !== null ? svc.uptime_percent.toFixed(1) : '—';
                var upColor = svc.uptime_percent !== null ? uptimeColor(svc.uptime_percent) : '#484f58';
                var upWidth = svc.uptime_percent !== null ? svc.uptime_percent : 0;

                return '<div class="service-card">' +
                    '<div class="status-icon">' + cfg.icon + '</div>' +
                    '<div class="service-info">' +
                        '<div class="service-name">' + escapeHtml(svc.name) + '</div>' +
                        '<div class="service-url">' + escapeHtml(svc.url) + '</div>' +
                    '</div>' +
                    '<div class="service-meta">' +
                        '<div class="response-time ' + rtClass(svc.response_time_ms) + '">' + rtText(svc.response_time_ms) + '</div>' +
                        '<div class="uptime-bar"><div class="uptime-fill" style="width:' + upWidth + '%;background:' + upColor + '"></div></div>' +
                        '<div class="uptime-text">Uptime: ' + upPct + '%</div>' +
                    '</div>' +
                    '<div class="service-actions">' +
                        '<button id="check-btn-' + svc.id + '" class="btn-icon" onclick="checkService(\\'' + svc.id + '\\')" title="Check now">⟳</button>' +
                        '<button class="btn-icon delete" onclick="deleteService(\\'' + svc.id + '\\')" title="Delete">✕</button>' +
                    '</div>' +
                '</div>';
            }).join('');
        }

        /* ========================================
         * Initialization — fetch + auto-refresh
         * ======================================== */
        fetchServices();
        setInterval(fetchServices, REFRESH_INTERVAL);
    </script>
</body>
</html>"""


# ============================================
# Flask Application
# ============================================
app = Flask(__name__)


# ============================================
# Helper Functions
# ============================================

def generate_id():
    """Generate a short unique ID for a service."""
    return uuid.uuid4().hex[:8]


def determine_status(response_time_seconds, success):
    """
    Determine service status based on response time and HTTP success.

    Rules:
        HTTP 200 + < 1s   → operational
        HTTP 200 + 1-3s   → degraded
        HTTP 200 + > 3s   → slow
        HTTP error/timeout → down

    Args:
        response_time_seconds: float, elapsed time in seconds
        success: bool, True if HTTP 200

    Returns:
        str: status string
    """
    if not success:
        return "down"
    if response_time_seconds < 1.0:
        return "operational"
    if response_time_seconds <= 3.0:
        return "degraded"
    return "slow"


def get_overall_status():
    """
    Determine overall status — worst status among all services.

    Priority (highest = worst):
        down > slow > degraded > operational > unknown
    """
    priority = {
        "unknown": 0,
        "operational": 1,
        "degraded": 2,
        "slow": 3,
        "down": 4,
    }

    with services_lock:
        if not services:
            return "unknown"

        worst = "unknown"
        for svc in services:
            status = svc.get("status", "unknown")
            if priority.get(status, 0) > priority.get(worst, 0):
                worst = status
        return worst


def create_service(name, url):
    """
    Create a new service record with default values.

    Args:
        name: display name
        url:  full health check URL

    Returns:
        dict: service record
    """
    return {
        "id": generate_id(),
        "name": name,
        "url": url,
        "status": "unknown",
        "response_time_ms": None,
        "last_check": None,
        "total_checks": 0,
        "successful_checks": 0,
        "uptime_percent": None,
    }


def check_single_service(service):
    """
    Perform HTTP GET health check on a single service.

    Measures response time, determines status, updates service record in-place.

    Args:
        service: dict — service record (modified in-place)
    """
    url = service["url"]
    name = service["name"]
    logger.info("Checking service: %s (%s)", name, url)

    try:
        start = time.time()
        response = http_requests.get(url, timeout=REQUEST_TIMEOUT)
        elapsed = time.time() - start

        success = response.status_code == 200
        status = determine_status(elapsed, success)
        response_time_ms = round(elapsed * 1000)

        if success:
            logger.info("  ✓ %s → %s (HTTP %d, %dms)", name, status, response.status_code, response_time_ms)
        else:
            logger.warning("  ✗ %s → down (HTTP %d, %dms)", name, response.status_code, response_time_ms)

    except http_requests.exceptions.Timeout:
        logger.error("  ✗ %s → down (timeout after %ds)", name, REQUEST_TIMEOUT)
        status = "down"
        response_time_ms = None
        success = False

    except http_requests.exceptions.RequestException as exc:
        logger.error("  ✗ %s → down (error: %s)", name, str(exc))
        status = "down"
        response_time_ms = None
        success = False

    # Thread-safe update of service record
    with services_lock:
        service["status"] = status
        service["response_time_ms"] = response_time_ms
        service["last_check"] = datetime.now(timezone.utc).isoformat()
        service["total_checks"] += 1
        if success:
            service["successful_checks"] += 1
        # Recalculate uptime percentage
        if service["total_checks"] > 0:
            service["uptime_percent"] = round(
                (service["successful_checks"] / service["total_checks"]) * 100, 2
            )


def check_all_services():
    """
    Run health check on all registered services.

    Called by scheduler (auto-ping) or manually via API.
    Updates global last_check_time.
    """
    global last_check_time

    logger.info("=" * 50)
    logger.info("Starting health check for all services (%d total)", len(services))

    # Snapshot list (shallow — same dict objects, so updates apply)
    with services_lock:
        snapshot = list(services)

    for svc in snapshot:
        check_single_service(svc)

    last_check_time = datetime.now(timezone.utc).isoformat()
    logger.info("Health check complete.")
    logger.info("=" * 50)


def scheduled_check():
    """Scheduler wrapper — respects auto_ping_enabled flag."""
    if auto_ping_enabled:
        check_all_services()
    else:
        logger.info("Auto-ping disabled — skipping scheduled check.")


def load_services_from_env():
    """
    Parse SERVICES environment variable and preload services.

    Expected format: JSON array of {"name": "...", "url": "..."} objects.
    Silently skips invalid entries.
    """
    try:
        parsed = json.loads(SERVICES_JSON)
        if not isinstance(parsed, list):
            logger.error("SERVICES env var is not a JSON array — ignoring.")
            return

        for item in parsed:
            if isinstance(item, dict) and "name" in item and "url" in item:
                svc = create_service(item["name"], item["url"])
                services.append(svc)
                logger.info("  Preloaded: %s → %s", item["name"], item["url"])
            else:
                logger.warning("  Skipping invalid entry: %s", item)

        logger.info("Loaded %d services from environment.", len(services))

    except json.JSONDecodeError as exc:
        logger.error("Failed to parse SERVICES env var: %s", str(exc))


# ============================================
# Flask Routes
# ============================================

@app.route("/")
def dashboard():
    """Serve the main dashboard HTML page."""
    return render_template_string(DASHBOARD_HTML)


@app.route("/health")
def health():
    """
    Self health check endpoint.

    Always returns HTTP 200 with JSON status.
    Used by Docker HEALTHCHECK and external monitors.
    """
    return jsonify({
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "shellty-pulse",
    }), 200


@app.route("/api/services", methods=["GET"])
def get_services():
    """
    List all monitored services with their current statuses.

    Returns JSON with services array and meta information
    (overall status, auto-ping state, timing info).
    """
    with services_lock:
        services_data = [svc.copy() for svc in services]

    return jsonify({
        "services": services_data,
        "meta": {
            "overall_status": get_overall_status(),
            "auto_ping_enabled": auto_ping_enabled,
            "ping_interval": ping_interval,
            "last_check": last_check_time,
            "total_services": len(services_data),
        },
    })


@app.route("/api/services", methods=["POST"])
def add_service():
    """
    Add a new service to monitor.

    Expects JSON: {"name": "Service Name", "url": "https://example.com/health"}
    Returns created service with HTTP 201.
    """
    data = request.get_json(silent=True)

    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    name = data.get("name", "").strip()
    url = data.get("url", "").strip()

    if not name or not url:
        return jsonify({"error": "Both 'name' and 'url' are required."}), 400

    if not url.startswith(("http://", "https://")):
        return jsonify({"error": "URL must start with http:// or https://"}), 400

    svc = create_service(name, url)

    with services_lock:
        services.append(svc)

    logger.info("Added service: %s → %s (id: %s)", name, url, svc["id"])
    return jsonify(svc), 201


@app.route("/api/services/<service_id>", methods=["DELETE"])
def delete_service(service_id):
    """
    Remove a service by its ID.

    Returns HTTP 204 on success, 404 if not found.
    """
    with services_lock:
        for i, svc in enumerate(services):
            if svc["id"] == service_id:
                removed = services.pop(i)
                logger.info("Deleted service: %s (id: %s)", removed["name"], service_id)
                return "", 204

    return jsonify({"error": "Service not found."}), 404


@app.route("/api/services/<service_id>/check", methods=["POST"])
def check_service_endpoint(service_id):
    """
    Manually trigger health check for a single service.

    Returns updated service data.
    """
    target = None
    with services_lock:
        for svc in services:
            if svc["id"] == service_id:
                target = svc
                break

    if not target:
        return jsonify({"error": "Service not found."}), 404

    check_single_service(target)

    with services_lock:
        return jsonify(target.copy())


@app.route("/api/check-all", methods=["POST"])
def check_all_endpoint():
    """
    Manually trigger health check for all services.

    Returns updated services list with overall status.
    """
    check_all_services()

    with services_lock:
        services_data = [svc.copy() for svc in services]

    return jsonify({
        "message": "All services checked.",
        "services": services_data,
        "overall_status": get_overall_status(),
    })


@app.route("/api/toggle-auto-ping", methods=["POST"])
def toggle_auto_ping():
    """
    Toggle automatic periodic health checking on/off.

    Returns current auto-ping state.
    """
    global auto_ping_enabled
    auto_ping_enabled = not auto_ping_enabled
    state = "enabled" if auto_ping_enabled else "disabled"
    logger.info("Auto-ping toggled: %s", state)

    return jsonify({
        "auto_ping_enabled": auto_ping_enabled,
        "message": f"Auto-ping {state}.",
    })


@app.route("/api/ping-interval", methods=["POST"])
def set_ping_interval():
    """
    Change the auto-ping interval.

    Accepts JSON: {"interval": 600}
    Valid values: 600 (10 min), 900 (15 min), 1800 (30 min), 3600 (1 hour).
    Reschedules the background job immediately.
    """
    global ping_interval

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be valid JSON."}), 400

    new_interval = data.get("interval")
    if new_interval not in AVAILABLE_INTERVALS:
        valid = [f"{v} ({k}s)" for k, v in AVAILABLE_INTERVALS.items()]
        return jsonify({
            "error": f"Invalid interval. Valid options: {', '.join(valid)}"
        }), 400

    ping_interval = new_interval

    # Reschedule background job with new interval
    if scheduler:
        scheduler.reschedule_job(
            "health_check_job",
            trigger="interval",
            seconds=ping_interval,
        )

    label = AVAILABLE_INTERVALS[ping_interval]
    logger.info("Ping interval changed to %s (%ds)", label, ping_interval)

    return jsonify({
        "interval": ping_interval,
        "label": label,
        "message": f"Auto-ping interval set to {label}.",
    })


# ============================================
# Application Entry Point
# ============================================

def start_app():
    """Initialize services, start scheduler, run Flask server."""
    global scheduler

    logger.info("=" * 50)
    logger.info("  Starting Shellty Pulse — Service Health Monitor")
    logger.info("=" * 50)
    logger.info("Configuration:")
    logger.info("  PING_INTERVAL:   %d seconds (%d min)", ping_interval, ping_interval // 60)
    logger.info("  REQUEST_TIMEOUT: %d seconds", REQUEST_TIMEOUT)

    # Load services from SERVICES env var
    load_services_from_env()

    # Start APScheduler for periodic health checks
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=scheduled_check,
        trigger="interval",
        seconds=ping_interval,
        id="health_check_job",
        name="Periodic Health Check",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Scheduler started — checking every %d seconds.", ping_interval)

    # Run initial check in background (non-blocking for fast startup)
    threading.Thread(target=check_all_services, daemon=True).start()
    logger.info("Initial health check started in background.")

    logger.info("Dashboard: http://0.0.0.0:5000")
    logger.info("=" * 50)

    # Start Flask (0.0.0.0 for Docker compatibility)
    app.run(host="0.0.0.0", port=5000, debug=False)


if __name__ == "__main__":
    start_app()