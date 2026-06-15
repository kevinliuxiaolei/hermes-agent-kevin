import sys
from unittest.mock import AsyncMock, MagicMock
import pytest
import yaml

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource

def _make_runner():
    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._running_agents = {}
    runner._session_key_for_source = lambda src: f"{src.platform.value}:{src.chat_id}"
    runner._thread_metadata_for_source = lambda src, anchor: {"thread_id": "99999"}
    runner._reply_anchor_for_event = lambda ev: None
    runner._evict_cached_agent = MagicMock()
    return runner

def _make_event(text, platform=Platform.TELEGRAM):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=platform, chat_id="12345", chat_type="dm"),
    )

@pytest.mark.anyio
async def test_telegram_bare_model_command_triggers_picker(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    cfg_path = hermes_home / "config.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"model": "gemini-low", "providers": {}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr("hermes_constants.get_hermes_home", lambda: hermes_home)
    monkeypatch.setattr("hermes_cli.config.get_hermes_home", lambda: hermes_home)

    runner = _make_runner()
    
    # Mock Telegram Adapter
    mock_adapter = AsyncMock()
    mock_adapter.send_model_picker = AsyncMock(return_value=MagicMock(success=True))
    runner.adapters[Platform.TELEGRAM] = mock_adapter

    # Mock list_picker_providers to return some mock providers
    monkeypatch.setattr(
        "hermes_cli.model_switch.list_picker_providers",
        lambda **kw: [{"slug": "antigravity-acp", "name": "Antigravity", "models": ["3.5-flash(low)"], "total_models": 1}],
    )

    # Invoke /model
    result = await runner._handle_model_command(_make_event("/model"))

    # Assert that it returned None (signifying picker was handled and sent)
    assert result is None

    # Assert send_model_picker was called
    mock_adapter.send_model_picker.assert_called_once()
    
    # Verify the callback function
    kwargs = mock_adapter.send_model_picker.call_args[1]
    on_model_selected = kwargs["on_model_selected"]
    display_text = kwargs["display_text"]
    assert display_text.startswith("<pre>")
    assert "volcengine_agent_plan" not in display_text
    assert "Volcengine" in display_text
    assert "可用候选：" in display_text

    # Test the callback
    callback_result = await on_model_selected("12345", "3.5-flash(low)", "antigravity-acp")
    assert "Model switched" in callback_result
    assert "gemini-low" in callback_result or "3.5-flash(low)" in callback_result

    # Verify override was set
    session_key = "telegram:12345"
    assert session_key in runner._session_model_overrides
    assert runner._session_model_overrides[session_key]["selected_model_alias"] == "gemini-low"
