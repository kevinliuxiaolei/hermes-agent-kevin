import pytest
from agent.quota_registry import QuotaFamilyStatus
from agent.model_registry import _REGISTRY


@pytest.fixture(autouse=True)
def force_supports_execute():
    old_supports = {}
    old_enabled = {}
    for entry in _REGISTRY:
        old_supports[entry.alias] = entry.supports_execute
        old_enabled[entry.alias] = entry.execute_enabled
        entry.supports_execute = True
        entry.execute_enabled = True
    yield
    for entry in _REGISTRY:
        entry.supports_execute = old_supports[entry.alias]
        entry.execute_enabled = old_enabled[entry.alias]


@pytest.fixture(autouse=True)
def mock_fast_quota_to_normal(monkeypatch):
    import agent.quota_registry
    monkeypatch.setattr(
        agent.quota_registry,
        "get_all_quota_statuses_fast",
        lambda: agent.quota_registry.get_all_quota_statuses(refresh=False),
    )



def _status(
    quota_family: str,
    provider_family: str,
    *,
    available: bool = True,
    remaining: int = 100,
    reason: str = "available",
) -> QuotaFamilyStatus:
    return QuotaFamilyStatus(
        quota_family=quota_family,
        provider_family=provider_family,
        available=available,
        quota_5h_percent=remaining,
        reason=reason,
        raw={},
        remaining_percent=remaining,
    )


def _quota_map(
    *,
    codex_available: bool = True,
    volcengine_available: bool = True,
    gemini_available: bool = True,
    claude_available: bool = True,
    gpt_available: bool = True,
    gemini_fallback_available: bool = True,
    nvidia_nim_available: bool = True,
    codex_remaining: int = 100,
    volcengine_agent_remaining: int = 100,
    volcengine_coding_remaining: int = 100,
    gemini_remaining: int = 100,
) -> dict[str, QuotaFamilyStatus]:
    return {
        "codex_business": _status(
            "codex_business",
            "codex",
            available=codex_available,
            remaining=codex_remaining,
            reason="available" if codex_available else "quota_exhausted",
        ),
        "codex_plus": _status(
            "codex_plus",
            "codex",
            available=codex_available,
            remaining=codex_remaining,
            reason="available" if codex_available else "quota_exhausted",
        ),
        "volcengine": _status(
            "volcengine",
            "volcengine",
            available=volcengine_available,
            remaining=max(volcengine_agent_remaining, volcengine_coding_remaining),
            reason="available" if volcengine_available else "no_credentials",
        ),
        "volcengine_agent_plan": _status(
            "volcengine_agent_plan",
            "volcengine",
            available=volcengine_available,
            remaining=volcengine_agent_remaining,
            reason="available" if volcengine_available else "no_credentials",
        ),
        "volcengine_coding_plan": _status(
            "volcengine_coding_plan",
            "volcengine",
            available=volcengine_available,
            remaining=volcengine_coding_remaining,
            reason="available" if volcengine_available else "no_credentials",
        ),
        "agy_gemini": _status(
            "agy_gemini",
            "antigravity",
            available=gemini_available,
            remaining=gemini_remaining,
            reason="available" if gemini_available else "quota_exhausted",
        ),
        "agy_claude": _status(
            "agy_claude",
            "antigravity",
            available=claude_available,
            remaining=100,
            reason="available" if claude_available else "quota_exhausted",
        ),
        "agy_gpt": _status(
            "agy_gpt",
            "antigravity",
            available=gpt_available,
            remaining=100,
            reason="available" if gpt_available else "quota_exhausted",
        ),
        "gemini": _status(
            "gemini",
            "gemini",
            available=gemini_fallback_available,
            remaining=100,
            reason="available" if gemini_fallback_available else "quota_exhausted",
        ),
        "nvidia_nim": _status(
            "nvidia_nim",
            "nvidia_nim",
            available=nvidia_nim_available,
            remaining=100,
            reason="available" if nvidia_nim_available else "quota_exhausted",
        ),
    }


def test_chat_prefers_volcengine_even_when_codex_available(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    result = select_model_with_reason(task="chat")

    assert result.entry is not None
    assert result.entry.alias == "volcengine-agent-ark-code-latest"
    assert "route priority" in result.reason


def test_selector_hydrates_uninitialized_fast_quota_cache(monkeypatch):
    from agent.model_selector import select_model_with_reason

    calls = []
    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses_fast",
        lambda: {
            "codex_plus": _status(
                "codex_plus", "codex", available=False, remaining=0, reason="not_refreshed"
            )
        },
    )
    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: calls.append(refresh) or _quota_map(),
    )

    result = select_model_with_reason(task="chat")

    assert result.entry is not None
    assert calls == [False]


def test_code_prefers_codex_when_codex_available(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    result = select_model_with_reason(task="code")

    assert result.entry is not None
    assert result.entry.family in {"codex_business", "codex_plus"}


def test_cron_preserves_codex_and_prefers_volcengine(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.alias == "volcengine-agent-ark-code-latest"


def test_select_model_falls_back_to_volcengine_when_codex_unavailable(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(codex_available=False),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.alias == "volcengine-agent-ark-code-latest"


def test_select_model_falls_back_to_gemini_when_codex_and_volcengine_unavailable(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(codex_available=False, volcengine_available=False),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.alias == "gemini-low"


def test_select_model_hard_skips_runtime_cooldown(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )
    monkeypatch.setattr(
        "agent.model_selector._provider_runtime_allowed",
        lambda entry, statuses: (
            (False, "runtime health cooldown")
            if entry.provider.startswith("volcengine-")
            else (True, None)
        ),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.provider != "volcengine-agent-plan"
    assert any(reason == "runtime health cooldown" for _alias, reason in result.skipped)


def test_preferred_alias_still_wins_when_quota_available(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    result = select_model_with_reason(task="cron", preferred_alias="gemini-low")

    assert result.entry is not None
    assert result.entry.alias == "gemini-low"
    assert "preferred" in result.reason


def test_preferred_alias_low_quota_remains_pinned_while_available(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(
            volcengine_agent_remaining=100,
            gemini_remaining=1,
        ),
    )

    result = select_model_with_reason(task="chat", preferred_alias="gemini-low")

    assert result.entry is not None
    assert result.entry.alias == "gemini-low"
    assert "preferred" in result.reason


def test_auto_route_scoring_uses_cross_family_quota_before_group_priority(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(
            codex_remaining=21,
            volcengine_agent_remaining=21,
            volcengine_coding_remaining=21,
            gemini_remaining=49,
            claude_available=False,
            gpt_available=False,
        ),
    )

    result = select_model_with_reason(task="chat")

    assert result.entry is not None
    assert result.entry.alias == "gemini-low"


def test_fallback_chain_uses_route_group_priority(monkeypatch):
    from agent.model_registry import route_group_for_family, _REGISTRY
    from agent.model_selector import build_fallback_chain

    old_supports = {}
    for entry in _REGISTRY:
        old_supports[entry.alias] = entry.supports_execute
        entry.supports_execute = True

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    try:
        chain = build_fallback_chain(task="cron", max_candidates=5)

        assert [route_group_for_family(entry.family) for entry in chain[:5]] == [
            "volcengine",
            "codex",
            "gemini",
            "claude",
            "gpt",
        ]
        assert [entry.alias for entry in chain[:5]] == [
            "volcengine-agent-ark-code-latest",
            "codex-business-5.4-mini",
            "gemini-low",
            "claude-sonnet",
            "gpt-oss",
        ]
    finally:
        for entry in _REGISTRY:
            entry.supports_execute = old_supports.get(entry.alias, entry.supports_execute)


def test_select_model_falls_back_to_gemini_fallback_when_primary_unavailable(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(
            codex_available=False,
            volcengine_available=False,
            gemini_available=False,
            claude_available=False,
            gpt_available=False,
            gemini_fallback_available=True,
            nvidia_nim_available=True,
        ),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.alias == "gemini-fallback"


def test_select_model_falls_back_to_nvidia_nim_when_gemini_fallback_also_unavailable(monkeypatch):
    from agent.model_selector import select_model_with_reason

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(
            codex_available=False,
            volcengine_available=False,
            gemini_available=False,
            claude_available=False,
            gpt_available=False,
            gemini_fallback_available=False,
            nvidia_nim_available=True,
        ),
    )

    result = select_model_with_reason(task="cron")

    assert result.entry is not None
    assert result.entry.alias == "nv-fallback"


def test_build_fallback_chain_includes_gemini_and_nvidia_nim_fallbacks(monkeypatch):
    from agent.model_registry import _REGISTRY
    from agent.model_selector import build_fallback_chain

    old_supports = {}
    for entry in _REGISTRY:
        old_supports[entry.alias] = entry.supports_execute
        entry.supports_execute = True

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(
            codex_available=True,
            gemini_available=True,
            claude_available=True,
            gpt_available=True,
            gemini_fallback_available=True,
            nvidia_nim_available=True,
        ),
    )

    try:
        chain = build_fallback_chain(task="cron", max_candidates=10)
        aliases = [entry.alias for entry in chain]

        assert "codex-business-5.4-mini" in aliases
        assert "volcengine-agent-ark-code-latest" in aliases
        assert "gemini-low" in aliases
        assert "claude-sonnet" in aliases
        assert "gpt-oss" in aliases

        fallback_aliases = aliases[-5:]
        assert fallback_aliases == [
            "gemini-fallback",
            "gemini-pro-fallback",
            "nv-fallback",
            "nv-kimi",
            "nv-nemotron",
        ]
    finally:
        for entry in _REGISTRY:
            entry.supports_execute = old_supports.get(entry.alias, entry.supports_execute)


def test_build_fallback_chain_enforces_total_limit_including_fallback_only(monkeypatch):
    from agent.model_selector import build_fallback_chain

    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: _quota_map(),
    )

    chain = build_fallback_chain(task="cron", max_candidates=3)

    assert len(chain) == 3
