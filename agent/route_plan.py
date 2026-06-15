"""Shared route-plan construction for every Hermes execution entry point."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

RouteMode = Literal["auto", "pin_with_fallback", "strict_pin"]


def _route_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("provider") or "").strip().lower(),
        str(entry.get("model") or "").strip(),
        str(entry.get("codex_home") or "").strip(),
        str(entry.get("base_url") or "").strip().rstrip("/").lower(),
    )


def _same_route(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_key = _route_key(left)
    right_key = _route_key(right)
    if left_key[:3] != right_key[:3]:
        return False
    if left_key[0] == "custom" and left_key[3] and right_key[3]:
        return left_key[3] == right_key[3]
    return True


def _normalize_entries(raw: Any) -> list[dict[str, Any]]:
    entries = raw if isinstance(raw, list) else [raw]
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        normalized = dict(entry)
        normalized["provider"] = str(entry.get("provider") or "").strip()
        normalized["model"] = str(entry.get("model") or "").strip()
        if normalized["provider"] and normalized["model"]:
            result.append(normalized)
    return result


def _registry_entry_to_dict(entry: Any) -> dict[str, Any]:
    item = {
        "provider": entry.provider,
        "model": entry.model,
    }
    for key in ("alias", "quota_family", "codex_home", "codex_account"):
        value = getattr(entry, key, None)
        if value:
            item[key] = value
    return item


def _route_health_allowed(entry: dict[str, Any]) -> tuple[bool, str | None]:
    try:
        from agent.route_health import normalize_route_health_provider, route_health_allows

        provider = normalize_route_health_provider(
            str(entry.get("provider") or ""),
            model=str(entry.get("model") or ""),
            base_url=str(entry.get("base_url") or ""),
        )
        return route_health_allows(provider)
    except Exception:
        return True, None


def configured_fallbacks(config: Optional[dict]) -> list[dict[str, Any]]:
    """Return configured fallbacks in policy order, de-duplicated."""
    config = config or {}
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for key in ("fallback_providers", "fallback_model"):
        for entry in _normalize_entries(config.get(key)):
            key_value = _route_key(entry)
            if key_value in seen:
                continue
            seen.add(key_value)
            merged.append(entry)
    return merged


def _max_fallback_candidates(config: Optional[dict], default: int = 4) -> int:
    try:
        value = int(((config or {}).get("model_selection") or {}).get("max_fallback_candidates") or default)
        return max(0, value)
    except (TypeError, ValueError):
        return default


def _configured_mode(config: Optional[dict], default: RouteMode) -> RouteMode:
    value = str(((config or {}).get("model_selection") or {}).get("route_mode") or default).strip().lower()
    if value in {"auto", "pin_with_fallback", "strict_pin"}:
        return value  # type: ignore[return-value]
    return default


def _max_route_attempts(config: Optional[dict], default: int) -> int:
    try:
        value = int(((config or {}).get("model_selection") or {}).get("max_route_attempts") or default)
        return max(1, value)
    except (TypeError, ValueError):
        return default


@dataclass
class RoutePlan:
    """Execution-ready primary route plus a bounded ordered fallback chain."""

    task: str
    mode: RouteMode
    primary: Optional[dict[str, Any]]
    fallbacks: list[dict[str, Any]]
    max_attempts: int
    skipped: list[dict[str, str]] = field(default_factory=list)

    def legacy_fallback_model(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self.fallbacks]


def build_route_plan(
    *,
    task: str = "chat",
    preferred_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model: Optional[str] = None,
    current_codex_home: Optional[str] = None,
    current_base_url: Optional[str] = None,
    config: Optional[dict] = None,
    configured_entries: Any = None,
    mode: Optional[RouteMode] = None,
    max_candidates: Optional[int] = None,
) -> RoutePlan:
    """Build the same bounded route plan for cron, gateway, CLI and helpers.

    Explicit routes default to ``pin_with_fallback``: the primary stays pinned,
    while configured policy fallbacks and healthy registry candidates are
    merged behind it. ``strict_pin`` is the only mode that disables fallback.
    """
    from agent.model_registry import get_model_by_alias
    from agent.model_selector import build_fallback_chain

    if not current_codex_home and preferred_alias:
        preferred = get_model_by_alias(preferred_alias)
        if preferred is not None:
            current_codex_home = preferred.codex_home

    primary = None
    if current_provider and current_model:
        primary = {
            "provider": current_provider,
            "model": current_model,
        }
        if current_codex_home:
            primary["codex_home"] = current_codex_home
        if current_base_url:
            primary["base_url"] = current_base_url

    default_mode: RouteMode = "pin_with_fallback" if primary or preferred_alias else "auto"
    resolved_mode = mode or _configured_mode(config, default_mode)
    max_total = _max_fallback_candidates(config) if max_candidates is None else max(0, max_candidates)
    primary_count = 1 if primary else 0
    max_attempts = _max_route_attempts(config, max(1, max_total + primary_count))
    max_total = min(max_total, max(0, max_attempts - primary_count))

    if resolved_mode == "strict_pin":
        return RoutePlan(task=task, mode=resolved_mode, primary=primary, fallbacks=[], max_attempts=1)

    registry_entries = [
        _registry_entry_to_dict(entry)
        for entry in build_fallback_chain(
            task=task or "chat",
            max_candidates=max_total,
            preferred_alias=preferred_alias,
        )
    ]
    policy_entries = (
        _normalize_entries(configured_entries)
        if configured_entries is not None
        else configured_fallbacks(config)
    )

    current_route = {
        "provider": current_provider,
        "model": current_model,
        "codex_home": current_codex_home,
        "base_url": current_base_url,
    }
    fallbacks: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for source, entries in (("configured", policy_entries), ("registry", registry_entries)):
        for entry in entries:
            key = _route_key(entry)
            label = str(entry.get("alias") or f"{key[0]}/{key[1]}")
            if _same_route(entry, current_route):
                skipped.append({"route": label, "reason": "same as primary"})
                continue
            if key in seen:
                skipped.append({"route": label, "reason": "duplicate route"})
                continue
            allowed, health_reason = _route_health_allowed(entry)
            if not allowed:
                skipped.append({"route": label, "reason": health_reason or "runtime health blocked"})
                continue
            if len(fallbacks) >= max_total:
                skipped.append({"route": label, "reason": f"fallback limit {max_total} reached"})
                continue
            seen.add(key)
            fallbacks.append(dict(entry))

    return RoutePlan(
        task=task,
        mode=resolved_mode,
        primary=primary,
        fallbacks=fallbacks,
        max_attempts=max_attempts,
        skipped=skipped,
    )
