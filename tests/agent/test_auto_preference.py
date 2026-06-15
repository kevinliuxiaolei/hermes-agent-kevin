from agent.auto_preference import choose_stable_auto_entry, record_auto_preference
from agent.model_registry import get_model_by_alias
from agent.quota_registry import QuotaFamilyStatus


def _status(family: str, remaining: int) -> QuotaFamilyStatus:
    return QuotaFamilyStatus(
        quota_family=family,
        provider_family=family,
        available=True,
        quota_5h_percent=remaining,
        reason="available",
        raw={},
        remaining_percent=remaining,
    )


def _entries():
    current = get_model_by_alias("gemini-low")
    challenger = get_model_by_alias("volcengine-coding-ark-code-latest")
    assert current is not None
    assert challenger is not None
    return current, challenger


def test_auto_preference_selects_initial_challenger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_MODEL_STATE_FILE", str(tmp_path / "auto.json"))
    current, challenger = _entries()
    quotas = {
        current.quota_family: _status(current.quota_family, 40),
        challenger.quota_family: _status(challenger.quota_family, 90),
    }

    selected, reason = choose_stable_auto_entry("chat", challenger, [challenger, current], quotas)

    assert selected.alias == challenger.alias
    assert reason == "automatic route selected by route priority"


def test_auto_preference_holds_current_inside_stability_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_MODEL_STATE_FILE", str(tmp_path / "auto.json"))
    current, challenger = _entries()
    record_auto_preference("chat", current.alias, selected_at=1000)
    quotas = {
        current.quota_family: _status(current.quota_family, 30),
        challenger.quota_family: _status(challenger.quota_family, 100),
    }

    selected, reason = choose_stable_auto_entry(
        "chat", challenger, [challenger, current], quotas, now=1100
    )

    assert selected.alias == current.alias
    assert "stability window" in reason


def test_auto_preference_switches_after_window_when_delta_is_large(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_MODEL_STATE_FILE", str(tmp_path / "auto.json"))
    current, challenger = _entries()
    record_auto_preference("chat", current.alias, selected_at=1000)
    quotas = {
        current.quota_family: _status(current.quota_family, 30),
        challenger.quota_family: _status(challenger.quota_family, 80),
    }

    selected, reason = choose_stable_auto_entry(
        "chat", challenger, [challenger, current], quotas, now=4000
    )

    assert selected.alias == challenger.alias
    assert "switched" in reason


def test_auto_preference_holds_after_window_when_delta_is_small(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_MODEL_STATE_FILE", str(tmp_path / "auto.json"))
    current, challenger = _entries()
    record_auto_preference("chat", current.alias, selected_at=1000)
    quotas = {
        current.quota_family: _status(current.quota_family, 60),
        challenger.quota_family: _status(challenger.quota_family, 70),
    }

    selected, reason = choose_stable_auto_entry(
        "chat", challenger, [challenger, current], quotas, now=4000
    )

    assert selected.alias == current.alias
    assert "quota delta" in reason


def test_auto_preference_switches_immediately_when_current_is_not_available(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_AUTO_MODEL_STATE_FILE", str(tmp_path / "auto.json"))
    current, challenger = _entries()
    record_auto_preference("chat", current.alias, selected_at=1000)
    quotas = {challenger.quota_family: _status(challenger.quota_family, 60)}

    selected, reason = choose_stable_auto_entry(
        "chat", challenger, [challenger], quotas, now=1100
    )

    assert selected.alias == challenger.alias
    assert reason == "automatic route selected by route priority"
