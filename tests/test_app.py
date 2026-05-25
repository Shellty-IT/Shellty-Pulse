"""
Shellty Pulse — unit tests.

All external HTTP requests are mocked. No real services are contacted.
Run: pytest tests/ --tb=short -v
"""

from __future__ import annotations

import json
import os

import pytest

os.environ["TESTING"] = "1"
os.environ["DISABLE_SCHEDULER"] = "true"

from pulse import create_app, state
from pulse.config import AVAILABLE_INTERVALS


@pytest.fixture(autouse=True)
def reset_state():
    """Reset in-memory state before each test."""
    with state.services_lock:
        state.services.clear()
        state.auto_ping_enabled = False
        state.ping_interval = 900
        state.last_check_time = None
        state.business_hours_enabled = False
        state.business_hours_start = 9
        state.business_hours_end = 15
    yield


@pytest.fixture
def app():
    application = create_app(testing=True)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()


# ── Health endpoint ──────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.get_json()
        assert data["status"] == "ok"
        assert data["service"] == "shellty-pulse"

    def test_health_contains_required_fields(self, client):
        data = client.get("/health").get_json()
        for field in (
            "version",
            "uptime_seconds",
            "monitored_services",
            "scheduler_running",
            "timestamp",
        ):
            assert field in data, f"Missing field: {field}"


# ── Dashboard ────────────────────────────────────────────────────────────────


class TestDashboard:
    def test_dashboard_returns_html(self, client):
        r = client.get("/")
        assert r.status_code == 200
        assert r.content_type.startswith("text/html")

    def test_dashboard_contains_title(self, client):
        r = client.get("/")
        assert b"Shellty Pulse" in r.data


# ── GET /api/services ────────────────────────────────────────────────────────


class TestGetServices:
    def test_empty_services(self, client):
        r = client.get("/api/services")
        assert r.status_code == 200
        data = r.get_json()
        assert "services" in data
        assert "meta" in data
        assert data["meta"]["total_services"] == 0

    def test_meta_fields(self, client):
        data = client.get("/api/services").get_json()
        meta = data["meta"]
        for field in (
            "overall_status",
            "auto_ping_enabled",
            "ping_interval",
            "total_services",
            "check_running",
            "business_hours_enabled",
            "business_hours_start",
            "business_hours_end",
            "business_hours_timezone",
        ):
            assert field in meta, f"Missing meta field: {field}"


# ── POST /api/services ───────────────────────────────────────────────────────


class TestAddService:
    def test_add_valid_service(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Test Service",
                "url": "https://example.com/health",
            },
        )
        assert r.status_code == 201
        data = r.get_json()
        assert data["name"] == "Test Service"
        assert data["url"] == "https://example.com/health"
        assert data["status"] == "unknown"
        assert "id" in data

    def test_add_service_with_frontend_url(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Full Service",
                "url": "https://api.example.com/health",
                "frontend_url": "https://example.com",
            },
        )
        assert r.status_code == 201
        assert r.get_json()["frontend_url"] == "https://example.com"

    def test_reject_empty_name(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "",
                "url": "https://example.com",
            },
        )
        assert r.status_code == 400

    def test_reject_empty_url(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Test",
                "url": "",
            },
        )
        assert r.status_code == 400

    def test_reject_invalid_url_scheme(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Bad",
                "url": "ftp://example.com",
            },
        )
        assert r.status_code == 400

    def test_reject_missing_body(self, client):
        r = client.post("/api/services", content_type="application/json")
        assert r.status_code == 400

    def test_ssrf_block_metadata(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "SSRF",
                "url": "http://169.254.169.254/latest/meta-data/",
            },
        )
        assert r.status_code == 400

    def test_ssrf_block_localhost(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "SSRF",
                "url": "http://127.0.0.1:6379/",
            },
        )
        assert r.status_code == 400


# ── DELETE /api/services/<id> ────────────────────────────────────────────────


class TestDeleteService:
    def test_delete_existing(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "ToDelete",
                "url": "https://example.com/health",
            },
        )
        svc_id = r.get_json()["id"]
        r = client.delete(f"/api/services/{svc_id}")
        assert r.status_code == 204

    def test_delete_nonexistent(self, client):
        r = client.delete("/api/services/nonexistent-id")
        assert r.status_code == 404

    def test_delete_removes_from_list(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Temp",
                "url": "https://example.com/health",
            },
        )
        svc_id = r.get_json()["id"]
        client.delete(f"/api/services/{svc_id}")
        services = client.get("/api/services").get_json()["services"]
        assert all(s["id"] != svc_id for s in services)


# ── POST /api/toggle-auto-ping ───────────────────────────────────────────────


class TestToggleAutoPing:
    def test_toggle_on(self, client):
        r = client.post("/api/toggle-auto-ping")
        assert r.status_code == 200
        data = r.get_json()
        assert data["auto_ping_enabled"] is True

    def test_toggle_off(self, client):
        client.post("/api/toggle-auto-ping")
        r = client.post("/api/toggle-auto-ping")
        assert r.get_json()["auto_ping_enabled"] is False


# ── POST /api/ping-interval ──────────────────────────────────────────────────


class TestPingInterval:
    def test_set_valid_interval(self, client):
        r = client.post("/api/ping-interval", json={"interval": 900})
        assert r.status_code == 200
        data = r.get_json()
        assert data["interval"] == 900
        assert data["label"] == "15 min"

    def test_reject_invalid_interval(self, client):
        r = client.post("/api/ping-interval", json={"interval": 999})
        assert r.status_code == 400

    def test_reject_missing_body(self, client):
        r = client.post("/api/ping-interval", content_type="application/json")
        assert r.status_code == 400

    def test_all_valid_intervals(self, client):
        for interval in AVAILABLE_INTERVALS:
            r = client.post("/api/ping-interval", json={"interval": interval})
            assert r.status_code == 200


# ── POST /api/business-hours ────────────────────────────────────────────────


class TestBusinessHours:
    def test_enable_business_hours(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": 8,
                "end": 20,
            },
        )
        assert r.status_code == 200
        data = r.get_json()
        assert data["business_hours_enabled"] is True
        assert data["business_hours_start"] == 8
        assert data["business_hours_end"] == 20

    def test_disable_business_hours(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": False,
                "start": 8,
                "end": 20,
            },
        )
        assert r.status_code == 200
        assert r.get_json()["business_hours_enabled"] is False

    def test_overnight_window(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": 23,
                "end": 1,
            },
        )
        assert r.status_code == 200

    def test_reject_same_start_end(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": 10,
                "end": 10,
            },
        )
        assert r.status_code == 400

    def test_reject_out_of_range(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": 25,
                "end": 10,
            },
        )
        assert r.status_code == 400

    def test_reject_non_bool_enabled(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": "yes",
                "start": 8,
                "end": 20,
            },
        )
        assert r.status_code == 400

    def test_reject_non_int_hours(self, client):
        r = client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": "08:00",
                "end": "20:00",
            },
        )
        assert r.status_code == 400

    def test_state_persists_in_api(self, client):
        client.post(
            "/api/business-hours",
            json={
                "enabled": True,
                "start": 7,
                "end": 18,
            },
        )
        meta = client.get("/api/services").get_json()["meta"]
        assert meta["business_hours_enabled"] is True
        assert meta["business_hours_start"] == 7
        assert meta["business_hours_end"] == 18


# ── POST /api/check-all (mocked) ────────────────────────────────────────────


class TestCheckAll:
    def test_check_all_returns_202(self, client):
        r = client.post("/api/check-all")
        assert r.status_code == 202
        data = r.get_json()
        assert "message" in data


# ── POST /api/services/<id>/check (mocked) ──────────────────────────────────


class TestSingleServiceCheck:
    def test_check_nonexistent_service(self, client):
        r = client.post("/api/services/nonexistent/check")
        assert r.status_code == 404

    def test_check_disabled_service_returns_409(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Disabled",
                "url": "https://example.com/health",
            },
        )
        svc_id = r.get_json()["id"]
        client.post(f"/api/services/{svc_id}/toggle-enabled")
        r = client.post(f"/api/services/{svc_id}/check")
        assert r.status_code == 409


# ── POST /api/services/<id>/toggle-enabled ───────────────────────────────────


class TestToggleEnabled:
    def test_toggle_disables_service(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Toggleable",
                "url": "https://example.com/health",
            },
        )
        svc_id = r.get_json()["id"]
        r = client.post(f"/api/services/{svc_id}/toggle-enabled")
        assert r.status_code == 200
        assert r.get_json()["enabled"] is False

    def test_toggle_reenables_service(self, client):
        r = client.post(
            "/api/services",
            json={
                "name": "Toggleable",
                "url": "https://example.com/health",
            },
        )
        svc_id = r.get_json()["id"]
        client.post(f"/api/services/{svc_id}/toggle-enabled")
        r = client.post(f"/api/services/{svc_id}/toggle-enabled")
        assert r.get_json()["enabled"] is True

    def test_toggle_nonexistent_returns_404(self, client):
        r = client.post("/api/services/nonexistent/toggle-enabled")
        assert r.status_code == 404
