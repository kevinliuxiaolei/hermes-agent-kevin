from __future__ import annotations

from hermes_cli import model_switch


VOLC_MODELS = [
    "ark-code-latest",
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "glm-5.3",
    "kimi-k2.7-code",
    "doubao-seed-2.1-turbo",
    "doubao-seed-evolving",
]


def _row(slug: str, models: list[str], name: str | None = None) -> dict:
    return {
        "slug": slug,
        "name": name or slug,
        "is_current": False,
        "is_user_defined": slug.startswith("volcengine-"),
        "models": models,
        "total_models": len(models),
        "source": "user-config" if slug.startswith("volcengine-") else "hermes",
    }


def test_overlay_public_picker_has_exactly_four_routes(monkeypatch):
    rows = [
        _row("openai-codex", ["gpt-5.6-codex"], "Codex"),
        _row(
            "antigravity-acp",
            [
                "agy-gemini-flash-high-latest",
                "gemini-3.7-flash-high",
                "claude-sonnet-4-6",
            ],
            "AGY",
        ),
        _row("volcengine-coding-plan", VOLC_MODELS, "Volc"),
        _row("volcengine-agent-plan", VOLC_MODELS, "Volc Agent Route"),
        _row("moa", ["balanced"], "Mixture of Agents"),
        _row("opencode-free", ["free-model"], "OpenCode Free"),
    ]
    monkeypatch.setattr(model_switch, "list_authenticated_providers", lambda **_: rows)
    monkeypatch.setattr("hermes_cli.models.fetch_openrouter_models", lambda: [])
    monkeypatch.setattr(
        "hermes_cli.models.provider_model_ids",
        lambda provider: [
            "agy-gemini-flash-high-latest",
            "agy-gemini-flash-medium-latest",
            "agy-gemini-flash-low-latest",
            "agy-gemini-flash-lite-latest",
            "agy-gemini-pro-high-latest",
            "gpt-oss-120b-medium",
            "gemini-3.5-flash-high",
            "gemini-3.5-flash-medium",
            "gemini-3.5-flash-low",
        ] if provider == "antigravity-acp" else [],
    )

    providers = model_switch.list_picker_providers(
        user_providers={
            "antigravity-acp": {"models": {}},
            "volcengine-coding-plan": {"models": {m: {} for m in VOLC_MODELS}},
            "volcengine-agent-plan": {"models": {m: {} for m in VOLC_MODELS}},
        },
        include_moa=True,
        max_models=50,
    )

    assert [(p["name"], p["slug"]) for p in providers] == [
        ("Codex", "openai-codex"),
        ("AGY Gemini", "agy-gemini"),
        ("Volc", "volcengine-coding-plan"),
        ("AGY OSS/Claude", "agy-oss-claude"),
    ]
    assert providers[1]["provider_slug"] == "antigravity-acp"
    assert providers[3]["provider_slug"] == "antigravity-acp"
    assert providers[1]["models"] == [
        "agy-gemini-flash-high-latest",
        "agy-gemini-flash-medium-latest",
        "agy-gemini-flash-low-latest",
        "agy-gemini-flash-lite-latest",
        "agy-gemini-pro-high-latest",
    ]
    assert all(model.startswith("agy-gemini-") for model in providers[1]["models"])
    assert providers[2]["models"] == VOLC_MODELS
    assert providers[3]["models"] == ["claude-sonnet-4-6", "gpt-oss-120b-medium"]


def test_overlay_picker_contract_does_not_change_typed_provider_resolution(monkeypatch):
    rows = [_row("moa", ["balanced"]), _row("opencode-free", ["free-model"])]
    monkeypatch.setattr(model_switch, "list_authenticated_providers", lambda **_: rows)
    monkeypatch.setattr("hermes_cli.models.fetch_openrouter_models", lambda: [])

    providers = model_switch.list_picker_providers(
        user_providers={}, include_moa=False, max_models=50
    )

    assert [p["slug"] for p in providers] == ["moa", "opencode-free"]
