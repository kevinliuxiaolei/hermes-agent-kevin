from agent.quota_registry import (
    QuotaFamilyStatus,
    _build_agy_family_status,
    _parse_antigravity_json_all_accounts,
    _parse_antigravity_output,
    _parse_percent,
    get_model_quota,
)
from types import SimpleNamespace
import time


def test_parse_percent_treats_na_as_zero():
    assert _parse_percent("N/A") == 0
    assert _parse_percent("NA") == 0
    assert _parse_percent("未知") == 0


def test_agy_family_status_treats_na_as_exhausted():
    parsed = {
        "provider_connected": True,
        "available": False,
        "reason": "all_models_na",
        "models": {
            "Gemini 3.5 Flash (Low)": {"remaining": "N/A", "reset": "2h"},
            "Gemini 3.5 Flash (High)": {"remaining": "N/A", "reset": "2h"},
        },
    }

    status = _build_agy_family_status("agy_gemini", parsed)

    assert status.available is False
    assert status.remaining_percent == 0
    assert status.quota_5h_percent == 0
    assert status.reason == "quota_exhausted"


def test_parse_antigravity_output_treats_na_models_as_exhausted():
    parsed = _parse_antigravity_output(
        """
        Antigravity Quota Status
        Model │ Remaining │ Resets in
        Gemini 3.5 Flash (Low) │ N/A │ 1h
        Claude Sonnet 4.6 (Thinking) │ N/A │ 1h
        """
    )

    assert parsed["available"] is False
    assert parsed["reason"] == "quota_exhausted"
    assert parsed["models"]["Gemini 3.5 Flash (Low)"]["remaining"] == "0%"


def test_agy_multi_account_prefers_active_available_account_with_missing_secondary_percentages(monkeypatch):
    monkeypatch.setattr(
        "agent.agy_slot_state.load_agy_slot_state",
        lambda: {
            "last_executed_slot": "secondary",
            "slots": {
                "primary": {"authenticated": True, "cooldown_until": 0},
                "secondary": {"authenticated": True, "cooldown_until": 0},
            },
        },
    )
    parsed = _parse_antigravity_json_all_accounts(
        '''
        [
          {
            "email": "other@example.com",
            "isActive": false,
            "status": "success",
            "snapshot": {
              "models": [
                {"label": "Gemini 3 Flash", "modelId": "gemini-3-flash", "isExhausted": false},
                {"label": "Claude Sonnet 4.6 (Thinking)", "modelId": "claude-sonnet-4-6", "isExhausted": false}
              ]
            }
          },
          {
            "email": "dirisephan@example.com",
            "isActive": true,
            "status": "success",
            "snapshot": {
              "models": [
                {"label": "Gemini 3 Flash", "modelId": "gemini-3-flash", "remainingPercentage": 0.6, "isExhausted": false},
                {"label": "Claude Sonnet 4.6 (Thinking)", "modelId": "claude-sonnet-4-6", "isExhausted": false}
              ]
            }
          }
        ]
        '''
    )

    gemini = _build_agy_family_status("agy_gemini", parsed)
    claude = _build_agy_family_status("agy_claude", parsed)

    assert gemini.available is True
    assert gemini.remaining_percent == 60
    assert gemini.raw["account_count"] == 2
    assert gemini.raw["chosen_account"] == "dirisephan@example.com"
    assert gemini.raw["last_executed_slot"] == "secondary"
    assert len(gemini.raw["execution_slots"]) == 2
    assert claude.available is False
    assert claude.remaining_percent == 0
    assert claude.quota_5h_percent == 0
    assert claude.raw["chosen_account"] == "dirisephan@example.com"


def test_agy_family_is_unavailable_when_all_execution_slots_are_cooling_down(monkeypatch):
    future = time.time() + 600
    monkeypatch.setattr(
        "agent.agy_slot_state.load_agy_slot_state",
        lambda: {
            "slots": {
                "primary": {"authenticated": True, "cooldown_until": future},
                "secondary": {"authenticated": True, "cooldown_until": future},
            },
        },
    )
    status = _build_agy_family_status(
        "agy_gemini",
        {
            "accounts": {
                "a@example.com": {
                    "isActive": True,
                    "models": {
                        "Gemini 3 Flash": {
                            "remaining": 80,
                            "is_exhausted": False,
                        },
                    },
                },
            },
        },
    )
    assert status.available is False
    assert status.reason == "cooldown"
    assert status.raw["ready_slot_count"] == 0


def test_check_volcengine_cooldown():
    from agent.quota_registry import _check_volcengine_cooldown
    from datetime import datetime, timedelta
    from unittest.mock import patch, mock_open
    
    future_reset = datetime.now() + timedelta(hours=1)
    reset_str = future_reset.strftime("%Y-%m-%d %H:%M:%S")
    
    mock_log_content = (
        f"2026-06-10 15:51:13,832 WARNING agent.conversation_loop: API call failed (attempt 1/3) "
        f"provider=volcengine-coding-plan base_url=https://ark.cn-beijing.volces.com/api/coding/v3/ "
        f"model=ark-code-latest summary=HTTP 429: You have exceeded the 5-hour usage quota. "
        f"It will reset at {reset_str} +0800 CST.\n"
    ).encode("utf-8")
    
    with patch("os.path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=mock_log_content)):
        reset_dt = _check_volcengine_cooldown("volcengine-coding-plan")
        assert reset_dt is not None
        assert reset_dt.strftime("%Y-%m-%d %H:%M:%S") == reset_str


def test_get_model_quota_uses_agy_per_model_status():
    family = QuotaFamilyStatus(
        quota_family="agy_gemini",
        provider_family="antigravity",
        available=True,
        remaining_percent=80,
        reason="available",
        raw={
            "models": {
                "Gemini 3.5 Flash (Low)": {
                    "label": "Gemini 3.5 Flash (Low)",
                    "remaining": "0%",
                    "is_exhausted": True,
                    "reset": "25m",
                },
                "Gemini 3.5 Flash (High)": {
                    "label": "Gemini 3.5 Flash (High)",
                    "remaining": "80%",
                    "is_exhausted": False,
                },
            }
        },
    )
    entry = SimpleNamespace(
        quota_family="agy_gemini",
        model="models/gemini-flash-lite-latest",
        display_name="Gemini 3.5 Flash (Low)",
    )

    status = get_model_quota(entry, {"agy_gemini": family})

    assert status.available is False
    assert status.remaining_percent == 0
    assert status.reset_in == "25m"


def test_get_model_quota_falls_back_to_agy_family_when_model_missing():
    family = QuotaFamilyStatus(
        quota_family="agy_gemini",
        provider_family="antigravity",
        available=True,
        remaining_percent=80,
        reason="available",
        raw={"models": {}},
    )
    entry = SimpleNamespace(
        quota_family="agy_gemini",
        model="unknown",
        display_name="Unknown",
    )

    assert get_model_quota(entry, {"agy_gemini": family}) is family
