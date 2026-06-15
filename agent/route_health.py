"""Small shared runtime-health circuit breaker for model routes."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

DEFAULT_STATE_FILE = "/home/lighthouse/.hermes/route_health.json"


def _state_path() -> Path:
    return Path(os.getenv("HERMES_ROUTE_HEALTH_STATE_FILE", DEFAULT_STATE_FILE)).expanduser()


def load_route_health() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def normalize_route_health_provider(provider: str, *, model: str = "", base_url: str = "") -> str:
    """Resolve a generic custom runtime identity to a stable registry provider."""
    normalized = (provider or "").strip().lower()
    if normalized != "custom":
        return normalized

    base = (base_url or "").strip().lower()
    if "/api/plan/" in base:
        return "volcengine-agent-plan"
    if "/api/coding/" in base:
        return "volcengine-coding-plan"

    if model:
        try:
            from agent.model_registry import list_models

            providers = {
                entry.provider.strip().lower()
                for entry in list_models()
                if entry.model == model and entry.provider
            }
            if len(providers) == 1:
                return providers.pop()
        except Exception:
            pass
    return normalized


def route_health_allows(provider: str) -> tuple[bool, Optional[str]]:
    provider = (provider or "").strip().lower()
    # Unit tests must not inherit the workstation's live circuit-breaker
    # state. Tests that exercise health behavior opt in with an isolated file.
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_ROUTE_HEALTH_STATE_FILE" not in os.environ:
        return True, None
    state = load_route_health().get(provider)
    if not provider or not isinstance(state, dict):
        return True, None
    try:
        cooldown_until = float(state.get("cooldown_until") or 0)
    except (TypeError, ValueError):
        cooldown_until = 0
    if cooldown_until > time.time():
        return False, str(state.get("reason") or "runtime health cooldown")
    # Authentication failures require an explicit recovery signal.  Letting a
    # fixed cooldown re-enable invalid credentials creates an endless
    # select-401-fallback loop.  A successful forced/probed request can still
    # clear the state through record_route_success().
    if state.get("execute_enabled") is False:
        return False, str(state.get("reason") or "runtime disabled")
    return True, None


def record_route_failure(
    provider: str,
    *,
    reason: str,
    status_code: Optional[int] = None,
    message: str = "",
    model: str = "",
    base_url: str = "",
) -> None:
    """Persist provider cooldown for failures that are unsafe to retry blindly."""
    provider = normalize_route_health_provider(provider, model=model, base_url=base_url)
    reason = (reason or "").strip().lower()
    if not provider or provider in {"auto", "custom"}:
        return

    cooldown_seconds = 0
    execute_enabled = True
    if status_code in {401, 403} or reason == "auth":
        cooldown_seconds = 3600
        execute_enabled = False
    elif status_code in {402, 429} or reason in {"billing", "rate_limit"}:
        cooldown_seconds = 1800
    elif (status_code is not None and status_code >= 500) or reason in {"server_error", "timeout", "empty_response"}:
        cooldown_seconds = 300
    if cooldown_seconds <= 0:
        return

    # Unit tests must never trip the live gateway's circuit breaker.
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_ROUTE_HEALTH_STATE_FILE" not in os.environ:
        return

    state = load_route_health()
    previous = state.get(provider) if isinstance(state.get(provider), dict) else {}
    failures = int(previous.get("consecutive_failures") or 0) + 1
    state[provider] = {
        "reason": reason or f"http_{status_code or 'error'}",
        "status_code": status_code,
        "last_error": message[:1000],
        "last_failure_at": time.time(),
        "cooldown_until": time.time() + cooldown_seconds,
        "execute_enabled": execute_enabled,
        "consecutive_failures": failures,
    }
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def record_route_success(provider: str, *, model: str = "", base_url: str = "") -> None:
    provider = normalize_route_health_provider(provider, model=model, base_url=base_url)
    if not provider:
        return
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_ROUTE_HEALTH_STATE_FILE" not in os.environ:
        return
    state = load_route_health()
    if provider not in state:
        return
    state.pop(provider, None)
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return


def clear_route_health(provider: str) -> None:
    """Explicitly clear a provider block after credentials are replaced."""
    provider = (provider or "").strip().lower()
    if not provider:
        return
    if os.getenv("PYTEST_CURRENT_TEST") and "HERMES_ROUTE_HEALTH_STATE_FILE" not in os.environ:
        return
    state = load_route_health()
    if provider not in state:
        return
    state.pop(provider, None)
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        return
