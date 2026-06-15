from hermes_cli.auth import get_auth_status
from hermes_cli.models import normalize_provider, provider_label


def test_gemini_acp_alias_normalizes_to_antigravity():
    assert normalize_provider("gemini-acp") == "antigravity-acp"
    assert provider_label("gemini-acp") == "Antigravity CLI (via ACP)"


def test_get_auth_status_routes_gemini_acp_to_antigravity(monkeypatch):
    from hermes_cli import auth

    calls = []

    def fake_status(provider_id):
        calls.append(provider_id)
        return {"provider": provider_id, "logged_in": True, "configured": True}

    monkeypatch.setattr(auth, "get_external_process_provider_status", fake_status)

    status = get_auth_status("gemini-acp")

    assert calls == ["antigravity-acp"]
    assert status["provider"] == "antigravity-acp"
    assert status["logged_in"] is True
