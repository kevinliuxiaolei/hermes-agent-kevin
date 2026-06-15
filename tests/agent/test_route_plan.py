from types import SimpleNamespace


def _entry(alias, provider, model, *, codex_home=None):
    return SimpleNamespace(
        alias=alias,
        provider=provider,
        model=model,
        quota_family=provider,
        codex_home=codex_home,
        codex_account=None,
    )


def test_route_plan_merges_configured_then_registry_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda **kwargs: [
            _entry("current", "primary", "model-a"),
            _entry("registry-b", "provider-b", "model-b"),
            _entry("registry-c", "provider-c", "model-c"),
        ],
    )

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        task="cron",
        current_provider="primary",
        current_model="model-a",
        configured_entries=[
            {"provider": "provider-b", "model": "model-b"},
            {"provider": "configured", "model": "model-d"},
        ],
        max_candidates=3,
    )

    assert plan.mode == "pin_with_fallback"
    assert [(e["provider"], e["model"]) for e in plan.fallbacks] == [
        ("provider-b", "model-b"),
        ("configured", "model-d"),
        ("provider-c", "model-c"),
    ]
    assert all(e["provider"] != "primary" for e in plan.fallbacks)


def test_route_plan_enforces_full_chain_limit(monkeypatch):
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda **kwargs: [
            _entry("registry-a", "registry", "a"),
            _entry("registry-b", "registry", "b"),
        ],
    )

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        configured_entries=[
            {"provider": "configured", "model": "a"},
            {"provider": "configured", "model": "b"},
        ],
        max_candidates=2,
    )

    assert len(plan.fallbacks) == 2
    assert [(e["provider"], e["model"]) for e in plan.fallbacks] == [
        ("configured", "a"),
        ("configured", "b"),
    ]
    assert any("fallback limit" in item["reason"] for item in plan.skipped)


def test_route_plan_unhealthy_configured_route_does_not_consume_slot(monkeypatch):
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda **kwargs: [_entry("registry-a", "registry", "a")],
    )
    monkeypatch.setattr(
        "agent.route_plan._route_health_allowed",
        lambda entry: (False, "auth") if entry["provider"] == "configured" else (True, None),
    )

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        configured_entries=[{"provider": "configured", "model": "bad"}],
        max_candidates=1,
    )

    assert [(entry["provider"], entry["model"]) for entry in plan.fallbacks] == [("registry", "a")]
    assert {"route": "configured/bad", "reason": "auth"} in plan.skipped


def test_route_plan_deduplicates_primary_when_only_primary_has_base_url(monkeypatch):
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda **kwargs: [_entry("same", "provider-a", "model-a")],
    )

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        current_provider="provider-a",
        current_model="model-a",
        current_base_url="https://example.invalid/v1",
        max_candidates=2,
    )

    assert plan.fallbacks == []
    assert plan.skipped == [{"route": "same", "reason": "same as primary"}]


def test_route_plan_strict_pin_disables_fallback(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("strict pin must not build a registry chain")

    monkeypatch.setattr("agent.model_selector.build_fallback_chain", fail_if_called)

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        current_provider="primary",
        current_model="model-a",
        mode="strict_pin",
    )

    assert plan.fallbacks == []
    assert plan.max_attempts == 1


def test_route_plan_enforces_max_route_attempts(monkeypatch):
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda **kwargs: [
            _entry("registry-a", "provider-a", "model-a"),
            _entry("registry-b", "provider-b", "model-b"),
            _entry("registry-c", "provider-c", "model-c"),
        ],
    )

    from agent.route_plan import build_route_plan

    plan = build_route_plan(
        current_provider="primary",
        current_model="model-primary",
        config={"model_selection": {"max_fallback_candidates": 5, "max_route_attempts": 3}},
    )

    assert plan.max_attempts == 3
    assert len(plan.fallbacks) == 2
