from agent.model_command import handle_model_command, resolve_model_input
from agent.quota_registry import QuotaFamilyStatus


def test_resolve_model_input_reports_ambiguous_shared_raw_model_id():
    entry, error = resolve_model_input("ark-code-latest")

    assert entry is None
    assert error is not None
    assert "Ambiguous" in error
    assert "volcengine-agent-ark-code-latest" in error
    assert "volcengine-coding-ark-code-latest" in error


def test_model_command_hydrates_uninitialized_fast_quota_cache(monkeypatch):
    from agent.model_command import _load_quota_statuses

    stale = QuotaFamilyStatus(
        quota_family="codex_plus",
        provider_family="codex",
        available=False,
        reason="not_refreshed",
        raw={},
    )
    fresh = QuotaFamilyStatus(
        quota_family="codex_plus",
        provider_family="codex",
        available=True,
        remaining_percent=39,
        reason="available",
        raw={},
    )
    calls = []
    monkeypatch.setattr("agent.quota_registry.get_all_quota_statuses_fast", lambda: {"codex_plus": stale})
    monkeypatch.setattr(
        "agent.quota_registry.get_all_quota_statuses",
        lambda refresh=False: calls.append(refresh) or {"codex_plus": fresh},
    )

    statuses = _load_quota_statuses()

    assert statuses["codex_plus"].remaining_percent == 39
    assert calls == [False]


def test_resolve_model_input_accepts_provider_model_for_agent_plan():
    entry, error = resolve_model_input("volcengine-agent-plan/ark-code-latest")

    assert error is None
    assert entry is not None
    assert entry.alias == "volcengine-agent-ark-code-latest"


def test_resolve_model_input_accepts_unique_raw_model_id():
    entry, error = resolve_model_input("doubao-seed-2.0-code")

    assert error is None
    assert entry is not None
    assert entry.alias == "volcengine-coding-doubao-code"


def test_resolve_model_input_accepts_unique_display_name():
    entry, error = resolve_model_input("Volcengine Coding Plan / Doubao Seed 2.0 Code")

    assert error is None
    assert entry is not None
    assert entry.alias == "volcengine-coding-doubao-code"


def test_resolve_model_input_reports_ambiguous_raw_model_id():
    entry, error = resolve_model_input("gpt-5.4")

    assert entry is None
    assert error is not None
    assert "Ambiguous" in error
    assert "codex-plus-5.4" in error
    assert "codex-business-5.4" in error


def test_model_list_command_returns_split_parts():
    result = handle_model_command(
        "list",
        current_alias="volcengine-agent-ark-code-latest",
        config={},
    )

    assert "parts" in result
    assert len(result["parts"]) == 1
    assert "可用模型概览（按有效额度倒序）" in result["parts"][0]
    assert "volcengine-agent-ark-code-latest" in result["parts"][0]
    assert result["new_alias"] is None


def test_model_list_all_returns_every_alias():
    result = handle_model_command(
        "list all",
        current_alias="volcengine-agent-ark-code-latest",
        config={},
    )

    assert result["text"].startswith("完整模型清单（按有效额度倒序）")
    assert "codex-business-5.4" in result["text"]


def test_empty_model_command_returns_compact_menu():
    result = handle_model_command(
        "",
        current_alias="volcengine-agent-ark-code-latest",
        config={},
    )

    text = result["text"]
    assert "模型选择" in text
    assert "可用候选" in text
    assert "额度概览" in text
    assert "自动模式：/model auto" in text
    assert "完整清单：/model list" in text
    assert "Quota 5h" not in text
    assert "CODEX_HOME" not in text
    assert len(text) < 1200


def test_model_switch_message_is_compact_chinese():
    result = handle_model_command(
        "volcengine-agent-ark-code-latest",
        current_alias="gemini-low",
        config={},
    )

    text = result["text"]
    assert "已切换：Ark Agent" in text
    assert "后端：volcengine-agent-plan / ark-code-latest" in text
    assert "仅当前会话生效" in text
    assert "Model switched" not in text
    assert len(text) < 400


def test_model_auto_switch_returns_dynamic_mode_without_concrete_session_switch():
    calls = []

    result = handle_model_command(
        "auto",
        current_alias="gemini-low",
        config={},
        session_switch_fn=lambda **kwargs: calls.append(kwargs),
    )

    assert result["new_alias"] == "auto"
    assert result["auto_mode"] is True
    assert result["new_provider"] is None
    assert calls == []
    assert "自动模式" in result["text"]


def test_model_auto_global_persists_alias_and_clears_fixed_selection():
    saved = {}

    result = handle_model_command(
        "auto --global",
        current_alias="gemini-low",
        config={},
        save_config_fn=saved.__setitem__,
    )

    assert result["persisted"] is True
    assert saved["model_selection.selected_model_alias"] == "auto"
    assert saved["model_selection.selected_provider"] == ""
    assert saved["model_selection.selected_model"] == ""


def test_empty_model_command_in_auto_mode():
    result = handle_model_command(
        "",
        current_alias="auto",
        config={},
    )
    text = result["text"]
    assert "模式：自动" in text

