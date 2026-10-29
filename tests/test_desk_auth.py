"""server.require_desk_auth protects the whole desk API, not just /api/acquisition/*.

Open by default (app.api_token_env unset) preserves today's local-development experience;
setting it gates every route except /api/health.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from chanakya import server
from chanakya.server import create_app


@pytest.fixture
def desk(settings, monkeypatch):
    monkeypatch.setattr(server, "desk_settings", lambda: settings)
    return settings


def test_health_is_always_open_even_with_a_token_configured(desk):
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200


def test_no_token_configured_leaves_the_whole_desk_open(desk):
    with TestClient(create_app()) as client:
        assert client.get("/api/brief").status_code == 200
        assert client.get("/api/opportunities").status_code == 200


def test_configured_token_protects_brief_and_opportunities(desk, monkeypatch):
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    monkeypatch.setenv("CHANAKYA_TEST_DESK_TOKEN", "s3cret")
    with TestClient(create_app()) as client:
        assert client.get("/api/brief").status_code == 401
        assert client.get("/api/opportunities").status_code == 401
        assert client.get("/api/brief", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert client.get("/api/brief", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert client.get("/api/opportunities", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_cors_origins_are_configurable_and_default_to_localhost(desk):
    assert desk.app.cors_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]

    desk.app.cors_origins = ["https://desk.example.com"]
    with TestClient(create_app()) as client:
        allowed = client.get("/api/health", headers={"Origin": "https://desk.example.com"})
        assert allowed.headers.get("access-control-allow-origin") == "https://desk.example.com"

        disallowed = client.get("/api/health", headers={"Origin": "https://evil.example.com"})
        assert "access-control-allow-origin" not in disallowed.headers


def test_rate_limit_returns_429_with_retry_after_once_exceeded(desk):
    desk.app.rate_limit_per_minute = 3
    with TestClient(create_app()) as client:
        for _ in range(3):
            client.get("/api/brief")
        blocked = client.get("/api/brief")
        assert blocked.status_code == 429
        assert "retry-after" in {k.lower() for k in blocked.headers.keys()}


def test_rate_limit_zero_disables_limiting(desk):
    desk.app.rate_limit_per_minute = 0
    with TestClient(create_app()) as client:
        for _ in range(10):
            assert client.get("/api/brief").status_code == 200


def test_health_is_exempt_from_rate_limiting(desk):
    desk.app.rate_limit_per_minute = 2
    with TestClient(create_app()) as client:
        for _ in range(5):
            assert client.get("/api/health").status_code == 200


def test_every_response_carries_baseline_security_headers(desk):
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "DENY"
        assert resp.headers.get("referrer-policy") == "no-referrer"


def test_configured_token_also_protects_acquisition_routes(desk, monkeypatch):
    """Confirms the single app-wide gate covers desk_acquisition.py too, now that its own
    router-level gate was removed in favor of this one (no regression from that change)."""
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    monkeypatch.setenv("CHANAKYA_TEST_DESK_TOKEN", "s3cret")
    with TestClient(create_app()) as client:
        assert client.get("/api/acquisition/providers").status_code == 401
        assert client.get("/api/acquisition/providers", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_production_mode_without_a_token_refuses_to_start(desk):
    desk.app.environment = "production"
    desk.app.api_token_env = None
    with pytest.raises(RuntimeError, match="refusing to start an unauthenticated desk API"):
        create_app()


def test_production_mode_with_a_configured_but_unset_env_var_still_refuses(desk, monkeypatch):
    desk.app.environment = "production"
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    monkeypatch.delenv("CHANAKYA_TEST_DESK_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="refusing to start"):
        create_app()


def test_production_mode_with_a_real_token_starts_and_enforces_it(desk, monkeypatch):
    desk.app.environment = "production"
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    monkeypatch.setenv("CHANAKYA_TEST_DESK_TOKEN", "s3cret")
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/brief").status_code == 401
        assert client.get("/api/brief", headers={"Authorization": "Bearer s3cret"}).status_code == 200


def test_development_mode_is_unaffected_by_the_production_check(desk):
    assert desk.app.environment == "development"
    desk.app.api_token_env = None
    with TestClient(create_app()) as client:  # must not raise
        assert client.get("/api/brief").status_code == 200
