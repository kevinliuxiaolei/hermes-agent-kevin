"""Shared route-health state for quota-aware model routing.

The configured fallback/model route lists describe the candidate pool.  This
module stores the current health/cooldown overlay used by quota monitors,
runtime fallback, cron routing, and model picker display so temporary quota
exhaustion can recover automatically when its reset time passes.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    import fcntl
except ImportError:  # pragma: no cover - production is Linux; tests may be portable
    fcntl = None

from hermes_constants import get_hermes_home

HEALTH_STATE_RELATIVE_PATH = Path("cron/state/model_route_health.json")
BLOCKING_STATUSES = {"cooldown", "auth_failed"}


@dataclass(frozen=True)
class RouteBlock:
    blocked: bool
    provider: str
    model: str
    status: str = "healthy"
    reason: str = ""
    cooldown_until: datetime | None = None
    record: dict[str, Any] | None = None


def _state_path(path: str | Path | None = None) -> Path:
    return Path(path) if path is not None else get_hermes_home() / HEALTH_STATE_RELATIVE_PATH


def _lock_path(path: str | Path | None = None) -> Path:
    state_path = _state_path(path)
    return state_path.with_suffix(state_path.suffix + ".lock")


@contextmanager
def _state_lock(path: str | Path | None = None):
    """Serialize read/modify/write updates from quota monitors and gateways."""

    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock_file:
        if fcntl is not None:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def route_key(provider: str, model: str | None = None) -> str:
    provider_norm = str(provider or "").strip().lower()
    model_norm = str(model or "").strip().lower()
    return f"{provider_norm}/{model_norm}" if model_norm else provider_norm


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _format_dt(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _load_route_health_unlocked(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    state_path = _state_path(path)
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    records: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            records[str(key)] = dict(value)
    return records


def load_route_health(path: str | Path | None = None) -> dict[str, dict[str, Any]]:
    with _state_lock(path):
        return _load_route_health_unlocked(path)


def _save_route_health_unlocked(records: dict[str, dict[str, Any]], path: str | Path | None = None) -> None:
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(state_path)


def save_route_health(records: dict[str, dict[str, Any]], path: str | Path | None = None) -> None:
    with _state_lock(path):
        _save_route_health_unlocked(records, path)


def write_route_health(
    *,
    provider: str,
    model: str | None = None,
    status: str,
    reason: str = "",
    confidence: str = "high",
    cooldown_until: datetime | str | None = None,
    quota_family: str | None = None,
    source: str = "",
    now: datetime | None = None,
    path: str | Path | None = None,
) -> dict[str, Any]:
    """Upsert a route-health record and return the stored record."""

    now_dt = now or _now_utc()
    until_dt = _parse_dt(cooldown_until)
    key = route_key(provider, model)
    record: dict[str, Any] = {
        "provider": str(provider or "").strip(),
        "model": str(model or "").strip(),
        "status": str(status or "unknown").strip().lower(),
        "reason": str(reason or "").strip(),
        "confidence": str(confidence or "").strip() or "high",
        "source": str(source or "").strip(),
        "last_seen_at": _format_dt(now_dt),
    }
    if quota_family:
        record["quota_family"] = str(quota_family).strip()
    if until_dt is not None:
        record["cooldown_until"] = _format_dt(until_dt)

    with _state_lock(path):
        records = _load_route_health_unlocked(path)
        records[key] = record
        _save_route_health_unlocked(records, path)
    return record


def _record_for(provider: str, model: str | None = None, *, path: str | Path | None = None) -> dict[str, Any] | None:
    records = load_route_health(path)
    exact = records.get(route_key(provider, model))
    if exact:
        return exact
    # Optional provider-level record, useful for auth failures or full-plan cooldowns.
    return records.get(route_key(provider, None))


def is_route_blocked(
    provider: str,
    model: str | None = None,
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> RouteBlock:
    """Return whether a candidate should be skipped right now.

    Expired cooldown records automatically stop blocking without mutating the
    file, so routes recover without config edits or monitor writes.
    """

    provider_s = str(provider or "").strip()
    model_s = str(model or "").strip()
    record = _record_for(provider_s, model_s, path=path)
    if not record:
        return RouteBlock(False, provider_s, model_s)

    status = str(record.get("status") or "unknown").strip().lower()
    reason = str(record.get("reason") or status).strip() or status
    cooldown_until = _parse_dt(record.get("cooldown_until"))
    now_dt = now or _now_utc()

    if status == "cooldown":
        if cooldown_until is not None and now_dt < cooldown_until:
            return RouteBlock(
                True,
                provider_s,
                model_s,
                status=status,
                reason=f"{reason}; cooldown until {_format_dt(cooldown_until)}",
                cooldown_until=cooldown_until,
                record=record,
            )
        return RouteBlock(False, provider_s, model_s, status="healthy", record=record)

    if status == "auth_failed":
        return RouteBlock(True, provider_s, model_s, status=status, reason=reason, record=record)

    return RouteBlock(False, provider_s, model_s, status=status, reason=reason, cooldown_until=cooldown_until, record=record)


def filter_usable_routes(
    routes: Iterable[dict[str, Any]],
    *,
    now: datetime | None = None,
    path: str | Path | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (usable, skipped) route entries without mutating input routes."""

    usable: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for route in routes:
        if not isinstance(route, dict):
            continue
        provider = str(route.get("provider") or "").strip()
        model = str(route.get("model") or "").strip()
        block = is_route_blocked(provider, model, now=now, path=path)
        if block.blocked:
            skipped_entry = dict(route)
            skipped_entry["skip_reason"] = block.reason
            skipped_entry["health_status"] = block.status
            skipped.append(skipped_entry)
        else:
            usable.append(dict(route))
    return usable, skipped
