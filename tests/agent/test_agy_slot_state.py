from agent.agy_slot_state import get_last_executed_slot, slot_summary


def test_slot_summary_reports_ready_and_last_slot():
    state = {
        "last_executed_slot": "secondary",
        "slots": {
            "primary": {"authenticated": True, "cooldown_until": 0},
            "secondary": {"authenticated": True, "cooldown_until": 0},
        },
    }
    assert slot_summary(state) == "2/2 slots ready · last secondary"


def test_get_last_executed_slot_reads_state(monkeypatch):
    monkeypatch.setattr(
        "agent.agy_slot_state.load_agy_slot_state",
        lambda: {"last_executed_slot": "primary", "updated_at": 100},
    )
    monkeypatch.setattr("agent.agy_slot_state.time.time", lambda: 120)
    assert get_last_executed_slot(max_age_seconds=60) == "primary"
    assert get_last_executed_slot(max_age_seconds=10) is None
