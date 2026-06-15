from types import SimpleNamespace

import cli as cli_module


def test_registry_model_switch_updates_cli_runtime_and_forces_rebuild(monkeypatch):
    shell = cli_module.HermesCLI.__new__(cli_module.HermesCLI)
    shell.model = "old-model"
    shell.provider = "old-provider"
    shell.requested_provider = "old-provider"
    shell._explicit_api_key = "old-key"
    shell._explicit_base_url = "https://old.example/v1"
    shell._selected_model_alias = None
    shell._selected_codex_home = None
    shell._active_agent_route_signature = ("old",)
    shell.agent = object()
    shell.config = {}

    monkeypatch.setattr(cli_module, "_cprint", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: {})

    shell._handle_model_switch("/model gemini-low")

    assert shell._selected_model_alias == "gemini-low"
    assert shell.model == "models/gemini-flash-lite-latest"
    assert shell.provider == "antigravity-acp"
    assert shell.requested_provider == "antigravity-acp"
    assert shell._explicit_api_key is None
    assert shell._explicit_base_url is None
    assert shell.agent is None
    assert shell._active_agent_route_signature is None


def test_registry_model_auto_switch_clears_fixed_cli_route(monkeypatch):
    shell = cli_module.HermesCLI.__new__(cli_module.HermesCLI)
    shell.model = "gpt-5.4"
    shell.provider = "openai-codex"
    shell.requested_provider = "openai-codex"
    shell._explicit_api_key = None
    shell._explicit_base_url = None
    shell._selected_model_alias = "codex-plus-5.4"
    shell._selected_codex_home = "/home/lighthouse/.codex-plus"
    shell._active_agent_route_signature = ("old",)
    shell.agent = object()
    shell.config = {}

    monkeypatch.setattr(cli_module, "_cprint", lambda *_args, **_kwargs: None)

    shell._handle_model_switch("/model auto")

    assert shell._selected_model_alias == "auto"
    assert shell._selected_codex_home is None
    assert shell.agent is None
    assert shell._active_agent_route_signature is None


def test_turn_route_rebuilds_shared_route_plan(monkeypatch):
    shell = SimpleNamespace(
        model="models/gemini-flash-lite-latest",
        provider="antigravity-acp",
        api_key="no-key-required",
        base_url="acp://antigravity",
        api_mode="chat_completions",
        acp_command="/home/lighthouse/.hermes/bin/agy_acp_bridge.py",
        acp_args=[],
        _credential_pool=None,
        _selected_model_alias="gemini-low",
        _selected_codex_home=None,
        _fallback_model=[],
        config={},
        service_tier=None,
    )
    plan = SimpleNamespace(
        legacy_fallback_model=lambda: [
            {"provider": "volcengine-coding-plan", "model": "ark-code-latest"}
        ]
    )
    monkeypatch.setattr("agent.route_plan.build_route_plan", lambda **_kwargs: plan)

    route = cli_module.HermesCLI._resolve_turn_agent_config(shell, "continue")

    assert shell._fallback_model == [
        {"provider": "volcengine-coding-plan", "model": "ark-code-latest"}
    ]
    assert "volcengine-coding-plan" in route["signature"][-1]
