import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.gradio_ui.app import create_gradio_app, mount_ui
from apps.gradio_ui.events import backend as bff
from core.errors import BankError


def test_gradio_components():
    ui = create_gradio_app()
    types = [item["type"] for item in ui.config["components"]]
    assert "chatbot" in types and "dataframe" in types and "dropdown" in types
    # No browser component state contains credentials or account tokens.
    for item in ui.config["components"]:
        if item["type"] == "state":
            assert ui.blocks[item["id"]].value == {"theme": "banking"}


def test_cookie_is_httponly(monkeypatch):
    async def login(username, password):
        return "opaque-test-session"

    monkeypatch.setattr(bff, "login", login)
    app = mount_ui(FastAPI())
    with TestClient(app) as client:
        response = client.post("/ui/login", json={"username": "demo01", "password": "synthetic"})
        assert response.status_code == 200
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "SameSite=strict" in response.headers["set-cookie"]
        assert "opaque-test-session" not in response.text


def test_missing_ui_session_fails():
    with pytest.raises(BankError):
        bff.get_session({})


def test_confirmation_controls_only_enable_a_current_prepared_proposal():
    import time

    from apps.gradio_ui.app import proposal_controls

    assert proposal_controls(None)[1]["visible"] is False
    pending = {"summary": "Synthetic checkbook request", "status": "prepared", "expires_at": time.time() + 60}
    controls = proposal_controls(pending)
    assert controls[1]["visible"] and controls[2]["interactive"]
    pending["status"] = "confirmed"
    assert not proposal_controls(pending)[2]["interactive"]
    assert proposal_controls(pending)[3]["visible"]
    pending["expires_at"] = 1
    assert not proposal_controls(pending)[2]["interactive"]
    assert "expired" in proposal_controls(pending)[0]


def test_transient_ui_error_preserves_pending_panels():
    from apps.gradio_ui.app import transient

    result = transient([], "Unavailable")
    assert result[2] == {"__type__": "update"}
    assert result[6] == {"__type__": "update"}
    assert result[9] == {"__type__": "update"}
