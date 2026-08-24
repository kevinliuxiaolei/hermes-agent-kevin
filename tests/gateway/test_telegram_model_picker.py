"""Tests for Telegram model picker thread fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from gateway.config import PlatformConfig
from plugins.platforms.telegram.adapter import TelegramAdapter


def _make_adapter():
    adapter = TelegramAdapter(PlatformConfig(enabled=True, token="test-token"))
    adapter._bot = AsyncMock()
    adapter._app = MagicMock()
    return adapter


class TestTelegramModelPicker:
    @pytest.mark.asyncio
    async def test_send_model_picker_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        sent = {}

        async def mock_send_message(**kwargs):
            sent.update(kwargs)
            return SimpleNamespace(message_id=101)

        adapter._bot.send_message = AsyncMock(side_effect=mock_send_message)

        result = await adapter.send_model_picker(
            chat_id="12345",
            providers=[
                {"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}
            ],
            current_model="model_1",
            current_provider="provider_one",
            session_key="s",
            on_model_selected=AsyncMock(),
            metadata={"thread_id": "99999"},
        )

        assert result.success is True
        assert "MARKDOWN_V2" in repr(sent["parse_mode"])
        assert "provider\\_one" in sent["text"]
        assert "`model_1`" in sent["text"]
        assert "12345:99999:101" in adapter._model_picker_state

    @pytest.mark.asyncio
    async def test_back_button_escapes_dynamic_provider_label(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345::42"] = {
            "providers": [{"slug": "provider_one", "name": "Provider One", "total_models": 1, "is_current": True}],
            "current_model": "model_1",
            "current_provider": "provider_one",
            "session_key": "s",
            "on_model_selected": AsyncMock(),
            "msg_id": 42,
        }

        query = AsyncMock()
        query.data = "mb"
        query.message = MagicMock()
        query.message.chat_id = 12345
        query.message.message_id = 42
        query.message.message_thread_id = None
        query.message.chat = SimpleNamespace(type="private")
        query.from_user = MagicMock()
        query.from_user.id = 1
        query.from_user.first_name = "tester"
        adapter._is_callback_user_authorized = MagicMock(return_value=True)
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()

        await adapter._handle_model_picker_callback(query, "mb", "12345")

        edit_kwargs = query.edit_message_text.call_args[1]
        assert "MARKDOWN_V2" in repr(edit_kwargs["parse_mode"])
        assert "provider\\_one" in edit_kwargs["text"]
        assert "`model_1`" in edit_kwargs["text"]

    @pytest.mark.asyncio
    async def test_model_picker_callback_rejects_unauthorized_user(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345:99:42"] = {
            "providers": [], "current_model": "m", "current_provider": "p",
            "session_key": "s", "on_model_selected": AsyncMock(), "msg_id": 42,
        }
        adapter._is_callback_user_authorized = MagicMock(return_value=False)
        query = AsyncMock()
        query.message = MagicMock(chat_id=12345, message_thread_id=99, message_id=42)
        query.message.chat = SimpleNamespace(type="supergroup")
        query.from_user = SimpleNamespace(id=2, first_name="other")

        await adapter._handle_model_picker_callback(query, "mb", "12345")

        query.answer.assert_awaited_once_with(
            text="⛔ You are not authorized to change this setting."
        )
        query.edit_message_text.assert_not_awaited()
        assert "12345:99:42" in adapter._model_picker_state

    @pytest.mark.asyncio
    async def test_model_picker_callback_rejects_other_authorized_requester(self):
        adapter = _make_adapter()
        adapter._model_picker_state["12345:99:42"] = {
            "providers": [], "current_model": "m", "current_provider": "p",
            "session_key": "s", "on_model_selected": AsyncMock(), "msg_id": 42,
            "requester_id": "1",
        }
        adapter._is_callback_user_authorized = MagicMock(return_value=True)
        query = AsyncMock()
        query.message = MagicMock(chat_id=12345, message_thread_id=99, message_id=42)
        query.message.chat = SimpleNamespace(type="supergroup")
        query.from_user = SimpleNamespace(id=2, first_name="other")

        await adapter._handle_model_picker_callback(query, "mb", "12345")

        query.answer.assert_awaited_once_with(
            text="⛔ This picker belongs to another user."
        )
        query.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_model_picker_state_is_isolated_by_topic(self):
        adapter = _make_adapter()
        adapter._bot.send_message = AsyncMock(
            side_effect=[SimpleNamespace(message_id=101), SimpleNamespace(message_id=102)]
        )
        kwargs = dict(
            chat_id="12345",
            providers=[{"slug": "p", "name": "P", "models": ["m"]}],
            current_model="m",
            current_provider="p",
            on_model_selected=AsyncMock(),
        )
        await adapter.send_model_picker(session_key="s:one", metadata={"thread_id": "1"}, **kwargs)
        await adapter.send_model_picker(session_key="s:two", metadata={"thread_id": "2"}, **kwargs)

        assert adapter._model_picker_state["12345:1:101"]["session_key"] == "s:one"
        assert adapter._model_picker_state["12345:2:102"]["session_key"] == "s:two"

    @pytest.mark.asyncio
    async def test_virtual_agy_row_switches_with_concrete_provider_slug(self):
        adapter = _make_adapter()
        selected = AsyncMock(return_value="ok")
        adapter._model_picker_state["12345:7:42"] = {
            "providers": [{
                "slug": "agy-oss-claude",
                "provider_slug": "antigravity-acp",
                "name": "AGY OSS/Claude",
                "models": ["claude-sonnet-4-6"],
                "total_models": 1,
            }],
            "current_model": "gemini-3.7-flash-high",
            "current_provider": "antigravity-acp",
            "session_key": "s",
            "on_model_selected": selected,
            "msg_id": 42,
        }
        adapter._is_callback_user_authorized = MagicMock(return_value=True)

        def query():
            q = AsyncMock()
            q.message = MagicMock(chat_id=12345, message_thread_id=7, message_id=42)
            q.message.chat = SimpleNamespace(type="supergroup")
            q.from_user = SimpleNamespace(id=1, first_name="tester")
            return q

        await adapter._handle_model_picker_callback(
            query(), "mp:agy-oss-claude", "12345"
        )
        await adapter._handle_model_picker_callback(query(), "mm:0", "12345")

        selected.assert_awaited_once_with(
            "12345", "claude-sonnet-4-6", "antigravity-acp"
        )
