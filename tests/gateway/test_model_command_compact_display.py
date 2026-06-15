"""Regression tests for the compact family-grouped /model output."""

from unittest.mock import MagicMock

import pytest

from agent.quota_registry import QuotaFamilyStatus
from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    return runner


def _make_event(text="/model"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm"),
    )


def _fake_quota_map():
    return {
        "agy_gemini": QuotaFamilyStatus(
            quota_family="agy_gemini",
            provider_family="antigravity",
            available=True,
            quota_5h_percent=100,
            reset_5h="4h 44m",
            reason="available",
            raw={},
            remaining_percent=100,
        ),
        "agy_claude": QuotaFamilyStatus(
            quota_family="agy_claude",
            provider_family="antigravity",
            available=True,
            quota_5h_percent=20,
            reset_5h="3h 13m",
            reason="available",
            raw={},
            remaining_percent=20,
        ),
        "agy_gpt": QuotaFamilyStatus(
            quota_family="agy_gpt",
            provider_family="antigravity",
            available=True,
            quota_5h_percent=20,
            reset_5h="3h 13m",
            reason="available",
            raw={},
            remaining_percent=20,
        ),
        "codex_plus": QuotaFamilyStatus(
            quota_family="codex_plus",
            provider_family="codex",
            available=False,
            quota_5h_percent=99,
            quota_7d_percent=0,
            reset_5h="5h",
            reset_7d="12h",
            reason="rate_limited",
            raw={},
            remaining_percent=0,
        ),
        "codex_business": QuotaFamilyStatus(
            quota_family="codex_business",
            provider_family="codex",
            available=True,
            quota_5h_percent=88,
            quota_7d_percent=75,
            reset_5h="2h",
            reset_7d="1d",
            reason="available",
            raw={},
            remaining_percent=75,
        ),
    }


@pytest.mark.anyio
async def test_handle_model_command_no_args_is_text_only(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())
    monkeypatch.setattr("agent.quota_registry.invalidate_cache", lambda: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"model": {}})

    event = _make_event("/model")
    event.source.platform = Platform.DISCORD

    result = await runner._handle_model_command(event)

    assert result is not None
    assert result.startswith("模型选择")
    assert "Model Configuration" not in result
    assert "Select a model family below to switch" not in result
    assert "可用候选：" in result
    assert "额度概览：" in result
    assert "AGY: Gemini 🟢100% / Claude 🟡20% / GPT-OSS 🟡20%" in result
    assert "Codex: Biz 🟢75% / Plus 🔴0%" in result
    assert "volcengine_agent_plan" not in result
    assert result.index("Gemini Low") < result.index("Codex:")


@pytest.mark.anyio
async def test_handle_model_command_current_is_summary_only(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())
    monkeypatch.setattr("agent.quota_registry.invalidate_cache", lambda: None)
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"model": {"provider": "antigravity-acp", "default": "models/gemini-flash-lite-latest"}})

    result = await runner._handle_model_command(_make_event("/model current"))

    assert result is not None
    assert result.startswith("当前偏好模型：")
    assert "requested: gemini-low" in result
    assert "display: Gemini 3.5 Flash (Low)" in result
    assert "provider: antigravity-acp" in result
    assert "model: models/gemini-flash-lite-latest" in result
    assert "context: 1M" in result
    assert "请求 quota：" in result
    assert "实际选择：" in result
    assert "quota: 🟢 100%" in result
    assert "Quota 5h:" not in result
    assert "Model Registry" not in result


@pytest.mark.anyio
async def test_handle_model_command_switch_writes_runtime_override(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"model": {}})

    event = _make_event("/model codex-business-5.4")
    event.source.platform = Platform.DISCORD
    await runner._handle_model_command(event)

    override = next(iter(runner._session_model_overrides.values()))
    assert override["selected_model_alias"] == "codex-business-5.4"
    assert override["provider"] == "openai-codex"
    assert override["model"] == "gpt-5.4"
    assert override["codex_home"] == "/home/lighthouse/.codex-business"


@pytest.mark.anyio
async def test_handle_model_command_auto_clears_fixed_runtime_override(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    event = _make_event("/model auto")
    event.source.platform = Platform.DISCORD
    session_key = runner._session_key_for_source(event.source)
    runner._session_model_overrides[session_key] = {
        "selected_model_alias": "codex-business-5.4",
        "provider": "openai-codex",
        "model": "gpt-5.4",
        "codex_home": "/home/lighthouse/.codex-business",
    }
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {"model": {}})

    await runner._handle_model_command(event)

    override = runner._session_model_overrides[session_key]
    assert override == {"selected_model_alias": "auto"}


def test_render_model_menu_auto_shows_actual_route_not_unresolved(monkeypatch):
    from agent.model_command import render_model_menu

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    text = render_model_menu(current_alias="auto")

    assert "模式：自动" in text
    assert "当前可执行：" in text
    assert "未解析" not in text


def test_render_current_model_telegram_is_compact(monkeypatch):
    from agent.model_command import render_current_model_telegram

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    text = render_current_model_telegram(
        current_alias="gemini-low",
        current_provider="antigravity-acp",
        current_model_value="models/gemini-flash-lite-latest",
    )

    assert text.startswith("<pre>")
    assert "当前详情" in text
    assert "当前：Gemini Low" in text
    assert "后端：antigravity-acp / models/gemini-flash-lite-latest" in text
    assert "额度池：" in text
    assert "实际执行：" in text
    assert "Quota 5h:" not in text
    assert "CODEX_HOME" not in text


def test_handle_model_command_switch_feedback_is_compact(monkeypatch):
    from agent.model_command import handle_model_command

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    result = handle_model_command(
        raw_args="gemini-low",
        current_alias="gemini-low",
        config={},
        save_config_fn=None,
        session_switch_fn=None,
    )

    text = result["text"]
    assert text.startswith("已切换：Gemini Low")
    assert "alias：gemini-low" in text
    assert "后端：antigravity-acp / models/gemini-flash-lite-latest" in text
    assert "额度：🟢100% · 范围：当前会话" in text
    assert "上下文：1M" in text
    assert "注意：" not in text


def test_handle_model_command_unavailable_codex_includes_note(monkeypatch):
    from agent.model_command import handle_model_command

    quota_map = _fake_quota_map()
    quota_map["codex_business"] = QuotaFamilyStatus(
        quota_family="codex_business",
        provider_family="codex",
        available=False,
        quota_5h_percent=99,
        quota_7d_percent=0,
        reset_5h="5h",
        reset_7d="12h",
        reason="rate_limited",
        raw={},
        remaining_percent=0,
    )
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: quota_map)

    result = handle_model_command(
        raw_args="codex-business-5.4",
        current_alias="gemini-low",
        config={},
        save_config_fn=None,
        session_switch_fn=None,
    )

    text = result["text"]
    assert text.startswith("已切换：Biz 5.4")
    assert "alias：codex-business-5.4" in text
    assert "后端：openai-codex / gpt-5.4" in text
    assert "额度：🔴0% · 范围：当前会话" in text
    assert "CODEX_HOME：/home/lighthouse/.codex-business" in text
    assert "注意：当前额度不可用，实际执行会自动 fallback。" in text


def test_handle_model_command_preview_is_read_only(monkeypatch):
    from agent.model_command import handle_model_command

    quota_map = _fake_quota_map()
    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: quota_map)

    result = handle_model_command(
        raw_args="preview",
        current_alias="gemini-low",
        config={},
        save_config_fn=None,
        session_switch_fn=None,
    )

    text = result["text"]
    assert text.startswith("可用模型概览（按有效额度倒序）")
    assert result["new_alias"] is None
    assert result["persisted"] is False
    assert "当前偏好：" in text
    assert len(result["parts"]) == 1


def test_model_list_is_globally_quota_ordered(monkeypatch):
    from agent.model_command import handle_model_command

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    text = handle_model_command(
        raw_args="list all",
        current_alias="gemini-low",
        config={},
    )["text"]

    assert text.index("gemini-low") < text.index("codex-business-5.4")
    assert text.index("codex-business-5.4") < text.index("codex-plus-5.4")
    assert "Model Registry" not in text


def test_render_model_menu_telegram_is_monospaced_and_compact(monkeypatch):
    from agent.model_command import render_model_menu_telegram

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    text = render_model_menu_telegram(current_alias="gemini-low", current_provider="antigravity-acp", current_model_value="models/gemini-flash-lite-latest")

    assert text.startswith("<pre>")
    assert "volcengine_agent_plan" not in text
    assert "Volcengine" in text
    assert "可用候选：" in text
    assert "额度概览：" in text
    assert "Gemini 3.5 Flash (Low)" not in text
    assert "gemini-low" not in text
    assert "volcengine-coding-ark-code-latest" not in text
    assert "○ 🟢100%" in text


def test_model_picker_aliases_filters_unavailable_and_keeps_current(monkeypatch):
    from agent.model_command import model_picker_aliases

    monkeypatch.setattr("agent.model_command._load_quota_statuses", lambda refresh=False: _fake_quota_map())

    aliases = model_picker_aliases(current_alias="codex-business-5.4", limit=4)

    assert aliases[0] == "codex-business-5.4"
    assert len(aliases) == 4
    assert "codex-plus-5.4" not in aliases


def test_telegram_keyboard_uses_compact_ranked_aliases(monkeypatch):
    import gateway.platforms.telegram as telegram_module
    from gateway.platforms.telegram import TelegramAdapter

    class Button:
        def __init__(self, text, callback_data):
            self.text = text
            self.callback_data = callback_data

    class Markup:
        def __init__(self, rows):
            self.inline_keyboard = rows

    monkeypatch.setattr(telegram_module, "InlineKeyboardButton", Button)
    monkeypatch.setattr(telegram_module, "InlineKeyboardMarkup", Markup)
    monkeypatch.setattr(
        "agent.model_command.model_picker_aliases",
        lambda current_alias=None: [current_alias, "gemini-low"],
    )

    adapter = object.__new__(TelegramAdapter)
    markup = adapter._build_registry_model_keyboard(current_alias="codex-business-5.4")
    callbacks = [button.callback_data for row in markup.inline_keyboard for button in row]

    assert callbacks[:3] == [
        "model:select:auto",
        "model:select:codex-business-5.4",
        "model:select:gemini-low",
    ]
    assert len(callbacks) == 6
