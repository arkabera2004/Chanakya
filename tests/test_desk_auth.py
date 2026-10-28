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


def test_configured_token_also_protects_acquisition_routes(desk, monkeypatch):
    """Confirms the single app-wide gate covers desk_acquisition.py too, now that its own
    router-level gate was removed in favor of this one (no regression from that change)."""
    desk.app.api_token_env = "CHANAKYA_TEST_DESK_TOKEN"
    monkeypatch.setenv("CHANAKYA_TEST_DESK_TOKEN", "s3cret")
    with TestClient(create_app()) as client:
        assert client.get("/api/acquisition/providers").status_code == 401
        assert client.get("/api/acquisition/providers", headers={"Authorization": "Bearer s3cret"}).status_code == 200
