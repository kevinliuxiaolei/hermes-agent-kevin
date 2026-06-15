from hermes_cli.model_switch import list_authenticated_providers, list_picker_providers


def _auth_status(slug):
    return {"configured": True, "logged_in": slug == "antigravity-acp"}


def test_antigravity_acp_shows_up_in_authenticated_model_list(monkeypatch):
    import agent.models_dev as models_dev
    import hermes_cli.auth as auth
    import hermes_cli.models as models

    monkeypatch.setattr(models_dev, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(models, "cached_provider_model_ids", lambda slug: ["models/gemini-flash-latest"])
    monkeypatch.setattr(auth, "get_auth_status", _auth_status)

    rows = list_authenticated_providers(current_provider="antigravity-acp", max_models=5)
    slugs = [r["slug"] for r in rows]

    assert "antigravity-acp" in slugs
    antigravity = next(r for r in rows if r["slug"] == "antigravity-acp")
    assert antigravity["name"] == "Antigravity CLI (via ACP)"
    assert antigravity["models"] == ["models/gemini-flash-latest"]


def test_antigravity_acp_shows_up_in_gateway_picker(monkeypatch):
    import agent.models_dev as models_dev
    import hermes_cli.auth as auth
    import hermes_cli.models as models

    monkeypatch.setattr(models_dev, "fetch_models_dev", lambda: {})
    monkeypatch.setattr(models, "cached_provider_model_ids", lambda slug: ["models/gemini-flash-latest"])
    monkeypatch.setattr(auth, "get_auth_status", _auth_status)

    rows = list_picker_providers(current_provider="openai-codex", max_models=5)
    antigravity = next((r for r in rows if r["slug"] == "antigravity-acp"), None)

    assert antigravity is not None
    assert antigravity["name"] == "Antigravity CLI (via ACP)"
    assert antigravity["models"] == ["models/gemini-flash-latest"]
    assert antigravity["total_models"] == 1
