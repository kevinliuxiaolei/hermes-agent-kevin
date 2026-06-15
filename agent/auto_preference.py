"""Persistent stability state for dynamic automatic model preference."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_FILE = "/home/lighthouse/.hermes/state/auto_model_preferences.json"
DEFAULT_STABILITY_SECONDS = 1800
DEFAULT_SWITCH_DELTA_PERCENT = 20
DEFAULT_MIN_HEALTHY_PERCENT = 20


def _state_path() -> Path:
    return Path(os.getenv("HERMES_AUTO_MODEL_STATE_FILE", DEFAULT_STATE_FILE)).expanduser()


def load_auto_preference(task: str) -> Optional[dict]:
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_AUTO_MODEL_STATE_FILE" not in os.environ:
        return None
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        item = data.get(task) if isinstance(data, dict) else None
        return item if isinstance(item, dict) else None
    except Exception:
        return None


def record_auto_preference(task: str, alias: str, *, selected_at: Optional[float] = None) -> None:
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_AUTO_MODEL_STATE_FILE" not in os.environ:
        return
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data[task] = {
        "alias": alias,
        "selected_at": float(selected_at if selected_at is not None else time.time()),
        "last_used_at": time.time(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def choose_stable_auto_entry(
    task: str,
    challenger,
    available_entries: list,
    quota_statuses: dict,
    *,
    now: Optional[float] = None,
    stability_seconds: int = DEFAULT_STABILITY_SECONDS,
    switch_delta_percent: int = DEFAULT_SWITCH_DELTA_PERCENT,
    min_healthy_percent: int = DEFAULT_MIN_HEALTHY_PERCENT,
):
    """Keep a healthy recent auto route unless a clearly better route wins."""
    from agent.quota_registry import get_model_quota

    if challenger is None:
        return None, "no available automatic route"

    state = load_auto_preference(task)
    current = None
    if state:
        current = next((entry for entry in available_entries if entry.alias == state.get("alias")), None)
    if current is None:
        record_auto_preference(task, challenger.alias)
        return challenger, "automatic route selected by route priority"

    current_quota = get_model_quota(current, quota_statuses)
    challenger_quota = get_model_quota(challenger, quota_statuses)
    current_pct = current_quota.remaining_percent if current_quota.remaining_percent is not None else 50
    challenger_pct = challenger_quota.remaining_percent if challenger_quota.remaining_percent is not None else 50
    selected_at = float(state.get("selected_at") or 0)
    age = float(now if now is not None else time.time()) - selected_at

    if current.alias == challenger.alias:
        record_auto_preference(task, current.alias, selected_at=selected_at or None)
        return current, "automatic route remains best"
    if age < stability_seconds and current_pct >= min_healthy_percent:
        record_auto_preference(task, current.alias, selected_at=selected_at or None)
        return current, f"automatic route held for stability window ({int(age)}s/{stability_seconds}s)"
    if current_pct >= min_healthy_percent and challenger_pct < current_pct + switch_delta_percent:
        record_auto_preference(task, current.alias, selected_at=selected_at or None)
        return current, f"automatic route held; challenger quota delta {challenger_pct - current_pct}% < {switch_delta_percent}%"

    record_auto_preference(task, challenger.alias)
    return challenger, f"automatic route switched; quota {current_pct}% -> {challenger_pct}%"
