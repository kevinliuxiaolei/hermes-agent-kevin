"""Shared reader for Antigravity bridge account-slot execution state."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

AGY_SLOT_STATE_PATH = Path("/home/lighthouse/.hermes/state/agy_slots.json")
AGY_SLOT_HOMES = {
    "primary": Path("/home/lighthouse"),
    "secondary": Path("/home/lighthouse/.hermes/second_home"),
}


def _authenticated(home: Path) -> bool:
    return (home / ".gemini" / "antigravity-cli" / "antigravity-oauth-token").exists()


def load_agy_slot_state() -> dict[str, Any]:
    try:
        payload = json.loads(AGY_SLOT_STATE_PATH.read_text(encoding="utf-8"))
        state = payload if isinstance(payload, dict) else {}
    except Exception:
        state = {}
    slots = state.setdefault("slots", {})
    for name, home in AGY_SLOT_HOMES.items():
        item = slots.setdefault(name, {})
        item.setdefault("home", str(home))
        item["authenticated"] = _authenticated(home)
        item.setdefault("cooldown_until", 0.0)
    return state


def get_last_executed_slot(*, max_age_seconds: float | None = None) -> Optional[str]:
    state = load_agy_slot_state()
    slot = str(state.get("last_executed_slot") or "").strip()
    if not slot:
        return None
    if max_age_seconds is not None:
        try:
            updated_at = float(state.get("updated_at") or 0)
        except (TypeError, ValueError):
            return None
        if updated_at <= 0 or time.time() - updated_at > max_age_seconds:
            return None
    return slot


def slot_summary(state: dict[str, Any] | None = None) -> str:
    state = state or load_agy_slot_state()
    slots = state.get("slots") if isinstance(state, dict) else None
    if not isinstance(slots, dict):
        return ""

    now = time.time()
    authenticated = 0
    ready = 0
    for item in slots.values():
        if not isinstance(item, dict) or not item.get("authenticated"):
            continue
        authenticated += 1
        try:
            cooldown_until = float(item.get("cooldown_until") or 0)
        except (TypeError, ValueError):
            cooldown_until = 0
        if cooldown_until <= now:
            ready += 1

    parts = [f"{ready}/{authenticated} slots ready"] if authenticated else ["0 slots"]
    last_slot = str(state.get("last_executed_slot") or "").strip()
    if last_slot:
        parts.append(f"last {last_slot}")
    return " · ".join(parts)
