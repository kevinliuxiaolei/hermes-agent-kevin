"""
Unified Model Selector for Hermes.

Provides a single selection function used by:
  - /model command (session switching)
  - Background/cron tasks
  - Fallback routing (replaces raw fallback_providers iteration)

All callers share the same registry + quota logic.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# Task priority for model selection
TASK_TAGS_PREFER_LOW = {"light", "summary", "cron"}
TASK_TAGS_ALLOW_HIGH = {"review", "analysis", "code"}
QUOTA_BUCKET_HEALTHY = 50
QUOTA_BUCKET_WARNING = 20
QUOTA_BUCKET_LOW = 1

VOLCENGINE_STATE_FILE = "/home/lighthouse/.hermes/volcengine_state.json"

ROUTE_PROFILES: dict[str, list[str]] = {
    # Telegram/default chat should stay stable and preserve premium quota.
    "chat": ["volcengine", "codex", "gemini", "claude", "gpt", "fallback_free"],
    "background": ["volcengine", "codex", "gemini", "claude", "gpt", "fallback_free"],
    # Coding/review tasks may spend the strongest coding quota when healthy.
    "code": ["codex", "volcengine", "claude", "gemini", "gpt", "fallback_free"],
    "review": ["codex", "volcengine", "claude", "gemini", "gpt", "fallback_free"],
    # Routine scheduled work should preserve Codex/ACP quota.
    "cron": ["volcengine", "codex", "gemini", "claude", "gpt", "fallback_free"],
    "summary": ["volcengine", "codex", "gemini", "claude", "gpt", "fallback_free"],
    "light": ["volcengine", "codex", "gemini", "claude", "gpt", "fallback_free"],
    # Deep analysis can prefer premium reasoning/long-context routes.
    "analysis": ["codex", "claude", "volcengine", "gemini", "gpt", "fallback_free"],
}


def _route_group_rank_for_task(family: str, task: str) -> int:
    from agent.model_registry import ROUTE_GROUP_ORDER, route_group_for_family

    group = route_group_for_family(family)
    order = ROUTE_PROFILES.get(task, ROUTE_GROUP_ORDER)
    try:
        return order.index(group)
    except ValueError:
        return len(order)


def _task_match_score(entry, task: str) -> int:
    """Score how naturally a model fits the requested task."""
    tags = set(entry.task_tags or [])
    if task in tags:
        return 3
    if task in TASK_TAGS_PREFER_LOW and tags & TASK_TAGS_PREFER_LOW:
        return 2
    if task in TASK_TAGS_ALLOW_HIGH and tags & TASK_TAGS_ALLOW_HIGH:
        return 2
    if "chat" in tags:
        return 1
    return 0


def _quota_bucket(pct: int | float | None) -> int:
    if pct is None:
        pct = 50
    if pct >= QUOTA_BUCKET_HEALTHY:
        return 3
    if pct >= QUOTA_BUCKET_WARNING:
        return 2
    if pct >= QUOTA_BUCKET_LOW:
        return 1
    return 0


def _read_runtime_state(path: str) -> dict:
    import json

    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _provider_runtime_allowed(entry, quota_statuses: dict) -> tuple[bool, str | None]:
    provider = (getattr(entry, "provider", "") or "").strip().lower()
    try:
        from agent.route_health import route_health_allows
        allowed, reason = route_health_allows(provider)
        if not allowed:
            return False, reason or "runtime health cooldown"
    except Exception:
        pass
    if provider == "antigravity-acp":
        try:
            from agent.quota_gate import agy_health_allows_calls
            if not agy_health_allows_calls():
                return False, "runtime health cooldown"
        except Exception:
            pass
    elif provider.startswith("volcengine-"):
        state = _read_runtime_state(VOLCENGINE_STATE_FILE)
        provider_state = state.get(provider) if isinstance(state.get(provider), dict) else None
        if provider_state:
            try:
                import time
                cooldown_until = float(provider_state.get("cooldown_until") or 0)
                if cooldown_until and time.time() < cooldown_until:
                    return False, "runtime quota cooldown"
            except Exception:
                pass
    return True, None


def _candidate_score(entry, task: str, quota_statuses: dict, family_rank: dict[str, int]):
    from agent.quota_registry import get_model_quota

    runtime_ok, _runtime_reason = _provider_runtime_allowed(entry, quota_statuses)
    if not runtime_ok:
        return (0, 0, 0, 0, 0, 0, 0, 0)
    quota = get_model_quota(entry, quota_statuses)
    if not quota.available:
        return (0, 0, 0, 0, 0, 0, 0, 0)
    pct = quota.remaining_percent if quota.remaining_percent is not None else 50
    bucket = _quota_bucket(pct)
    cost = entry.cost_order()
    group_priority = -_route_group_rank_for_task(entry.family, task)
    family_priority = -family_rank.get(entry.family, 99)
    task_fit = _task_match_score(entry, task)
    entry_priority = -getattr(entry, "priority", 99)
    # Task route group is the cross-family policy. Quota optimizes within that
    # policy instead of letting a healthy low-priority family consume work that
    # should preserve it for a different task profile.
    return (1, group_priority, task_fit, bucket, pct, -cost, family_priority, entry_priority)


def _auto_candidate_score(entry, task: str, quota_statuses: dict, family_rank: dict[str, int]):
    """Rank dynamic-mode candidates by trustworthy quota before route policy."""
    from agent.quota_registry import get_model_quota

    runtime_ok, _runtime_reason = _provider_runtime_allowed(entry, quota_statuses)
    if not runtime_ok:
        return (0, 0, 0, 0, 0, 0, 0, 0, 0)
    quota = get_model_quota(entry, quota_statuses)
    if not quota.available:
        return (0, 0, 0, 0, 0, 0, 0, 0, 0)
    known_quota = quota.remaining_percent is not None
    pct = quota.remaining_percent if known_quota else -1
    bucket = _quota_bucket(pct) if known_quota else 0
    cost = entry.cost_order()
    group_priority = -_route_group_rank_for_task(entry.family, task)
    family_priority = -family_rank.get(entry.family, 99)
    task_fit = _task_match_score(entry, task)
    entry_priority = -getattr(entry, "priority", 99)
    return (
        1,
        int(known_quota),
        bucket,
        pct,
        group_priority,
        task_fit,
        -cost,
        family_priority,
        entry_priority,
    )


@dataclass
class SelectionResult:
    entry: object        # ModelEntry | None
    reason: str
    skipped: list        # list of (alias, reason)


def select_model(
    task: str = "chat",
    preferred_alias: Optional[str] = None,
    refresh_quota: bool = False,
) -> Optional[object]:
    """
    Select the best available model for a given task.

    Args:
        task: Task type hint ("chat", "cron", "summary", "analysis", "code", "background")
        preferred_alias: User's preferred model alias (from /model selection)
        refresh_quota: If True, refresh quota data before selecting

    Returns:
        ModelEntry or None if no model available.
    """
    result = select_model_with_reason(task=task, preferred_alias=preferred_alias, refresh_quota=refresh_quota)
    return result.entry


def select_model_with_reason(
    task: str = "chat",
    preferred_alias: Optional[str] = None,
    refresh_quota: bool = False,
) -> SelectionResult:
    """
    Select the best available model, with detailed selection reasoning.
    """
    from agent.model_registry import (
        family_order,
        get_model_by_alias,
        list_models,
    )
    from agent.quota_registry import get_all_quota_statuses, get_model_quota

    skipped = []
    auto_mode = not preferred_alias or str(preferred_alias).strip().lower() == "auto"

    try:
        if refresh_quota:
            quota_statuses = get_all_quota_statuses(refresh=True)
        else:
            try:
                from agent.quota_registry import get_all_quota_statuses_fast
                quota_statuses = get_all_quota_statuses_fast()
                if any(status.reason == "not_refreshed" for status in quota_statuses.values()):
                    quota_statuses = get_all_quota_statuses(refresh=False)
            except Exception:
                quota_statuses = get_all_quota_statuses(refresh=False)
    except Exception as e:
        logger.warning("model_selector: quota fetch failed: %s", e)
        quota_statuses = {}

    all_models = list_models()

    # Helper to log structured route decision
    def _log_decision(entry, reason):
        import json
        from hermes_cli.config import load_config
        max_route_attempts = None
        try:
            cfg = load_config() or {}
            ms = cfg.get("model_selection") or {}
            max_fallbacks = int(ms.get("max_fallback_candidates") or 4)
            max_route_attempts = int(ms.get("max_route_attempts") or (max_fallbacks + 1))
        except Exception:
            max_route_attempts = 5
        decision = {
            "event": "model_route_decision",
            "task": task,
            "preferred_alias": preferred_alias,
            "selected_alias": entry.alias if entry else "N/A",
            "selected_provider": entry.provider if entry else "N/A",
            "selected_model": entry.model if entry else "N/A",
            "quota_family": entry.quota_family if entry else "N/A",
            "reason": reason,
            "skipped": [{"alias": s[0], "reason": s[1]} for s in skipped],
            "max_route_attempts": max_route_attempts,
        }
        logger.info("model_route_decision: %s", json.dumps(decision))

    if not auto_mode:
        preferred = get_model_by_alias(preferred_alias)
        if preferred and preferred.enabled:
            if not preferred.supports_execute:
                skipped.append((preferred_alias, "execute bridge not enabled"))
                logger.info("selector skipped execute_disabled model: %s", preferred_alias)
            else:
                quota = get_model_quota(preferred, quota_statuses)
                preferred_pct = quota.remaining_percent if quota.remaining_percent is not None else 100
                runtime_ok, runtime_reason = _provider_runtime_allowed(preferred, quota_statuses)
                if not runtime_ok:
                    skipped.append((preferred_alias, runtime_reason or "runtime health cooldown"))
                elif quota.available:
                    logger.info("model_selector: selected %s (preferred, quota available)", preferred_alias)
                    reason = "selected because preferred and quota available"
                    _log_decision(preferred, reason)
                    return SelectionResult(entry=preferred, reason=reason, skipped=skipped)
                else:
                    skipped.append((preferred_alias, f"quota unavailable: {quota.reason}"))
                    logger.info("model_selector: preferred %s skipped, quota %s", preferred_alias, quota.reason)
                    # Fall through to the global priority order.

    # 2. Build candidates: split into primary and fallback-only
    primary_candidates = []
    fallback_candidates = []
    for model in all_models:
        if not model.enabled or not model.supports_execute:
            continue
        runtime_ok, runtime_reason = _provider_runtime_allowed(model, quota_statuses)
        if not runtime_ok:
            if not preferred_alias or model.alias != preferred_alias:
                skipped.append((model.alias, runtime_reason or "runtime health cooldown"))
            continue
        if getattr(model, "role", "primary") == "fallback_only":
            fallback_candidates.append(model)
        else:
            primary_candidates.append(model)

    # 3. Score candidates
    family_rank = {family: idx for idx, family in enumerate(family_order())}

    # First try primary candidates
    score_fn = _auto_candidate_score if auto_mode else _candidate_score
    ranked_primary = sorted(
        primary_candidates,
        key=lambda entry: score_fn(entry, task, quota_statuses, family_rank),
        reverse=True,
    )

    available_primary = []
    for entry in ranked_primary:
        if not auto_mode and preferred_alias and entry.alias == preferred_alias:
            continue
        runtime_ok, runtime_reason = _provider_runtime_allowed(entry, quota_statuses)
        if not runtime_ok:
            skipped.append((entry.alias, runtime_reason or "runtime health cooldown"))
            continue
        quota = get_model_quota(entry, quota_statuses)
        if not quota.available:
            skipped.append((entry.alias, f"quota unavailable: {quota.reason}"))
            logger.debug("model_selector: skipping %s — quota %s", entry.alias, quota.reason)
            continue
        available_primary.append(entry)

    if auto_mode and available_primary:
        from agent.auto_preference import choose_stable_auto_entry

        entry, auto_reason = choose_stable_auto_entry(
            task,
            available_primary[0],
            available_primary,
            quota_statuses,
        )
        reason = (
            f"{auto_reason}; quota {get_model_quota(entry, quota_statuses).remaining_percent}% remaining"
            if get_model_quota(entry, quota_statuses).remaining_percent is not None
            else auto_reason
        )
        _log_decision(entry, reason)
        return SelectionResult(entry=entry, reason=reason, skipped=skipped)

    for entry in available_primary:
        quota = get_model_quota(entry, quota_statuses)
        logger.info(
            "model_selector: selected %s (family=%s, remaining=%s%%, reason=%s)",
            entry.alias, entry.family,
            quota.remaining_percent, "priority + quota ok"
        )
        reason = (
            f"selected by route priority because quota {quota.remaining_percent}% remaining"
            if quota.remaining_percent is not None
            else "selected by route priority because quota available"
        )
        _log_decision(entry, reason)
        return SelectionResult(entry=entry, reason=reason, skipped=skipped)

    # If all primary candidates failed, try fallback-only candidates sorted by priority
    ranked_fallback = sorted(fallback_candidates, key=lambda m: getattr(m, "priority", 99))
    for entry in ranked_fallback:
        if preferred_alias and entry.alias == preferred_alias:
            continue
        runtime_ok, runtime_reason = _provider_runtime_allowed(entry, quota_statuses)
        if not runtime_ok:
            skipped.append((entry.alias, runtime_reason or "runtime health cooldown"))
            continue
        quota = get_model_quota(entry, quota_statuses)
        if not quota.available:
            skipped.append((entry.alias, f"quota unavailable: {quota.reason}"))
            logger.debug("model_selector: skipping fallback-only %s — quota %s", entry.alias, quota.reason)
            continue
        logger.info(
            "model_selector: selected fallback-only %s (family=%s, remaining=%s%%, reason=%s)",
            entry.alias, entry.family,
            quota.remaining_percent, "nvidia fallback + quota ok"
        )
        reason = "selected because primary providers are unavailable, using NVIDIA NIM Free fallback"
        _log_decision(entry, reason)
        return SelectionResult(entry=entry, reason=reason, skipped=skipped)

    # No model available
    logger.warning("model_selector: no available model found for task=%s", task)
    reason = "no available model found"
    _log_decision(None, reason)
    return SelectionResult(entry=None, reason=reason, skipped=skipped)


def build_fallback_chain(
    task: str = "chat",
    max_candidates: int = 4,
    preferred_alias: Optional[str] = None,
) -> list:
    """
    Build an ordered fallback chain of ModelEntry objects.

    Used to replace raw fallback_providers list iteration.
    Returns up to max_candidates enabled, quota-aware entries.
    Each route group contributes at most one entry so the
    default chain follows codex -> gemini -> claude -> gpt-oss instead of
    retrying several variants on the same backend.
    """
    from agent.model_registry import (
        family_order,
        list_models,
        route_group_for_family,
    )
    from agent.quota_registry import get_all_quota_statuses, get_model_quota

    try:
        quota_statuses = get_all_quota_statuses()
    except Exception as e:
        logger.warning("model_selector: quota fetch for fallback failed: %s", e)
        quota_statuses = {}

    candidates = [m for m in list_models() if m.enabled and m.supports_execute and getattr(m, "role", "primary") != "fallback_only"]
    prefer_low_cost = task in TASK_TAGS_PREFER_LOW

    chain = []
    route_group_count: dict[str, int] = {}
    seen_aliases: set[str] = set()

    # First: try preferred alias
    if preferred_alias and str(preferred_alias).strip().lower() != "auto":
        from agent.model_registry import get_model_by_alias
        pref = get_model_by_alias(preferred_alias)
        if pref and pref.enabled and pref.supports_execute:
            quota = get_model_quota(pref, quota_statuses)
            runtime_ok, _runtime_reason = _provider_runtime_allowed(pref, quota_statuses)
            if quota.available and runtime_ok:
                chain.append(pref)
                seen_aliases.add(pref.alias)
                route_group_count[route_group_for_family(pref.family)] = 1

    # Sort remaining candidates
    family_rank = {family: idx for idx, family in enumerate(family_order())}

    def score(entry):
        quota = get_model_quota(entry, quota_statuses)
        if not quota.available:
            return (0, 0, 0, 0, 0, 0, 0, 0)
        pct = quota.remaining_percent if quota.remaining_percent is not None else 50
        
        # Map pct to bucket according to Quota Display Contract
        if pct >= 50:
            bucket = 3   # 🟢 Healthy
        elif pct >= 20:
            bucket = 2   # 🟡 Warning
        elif pct >= 1:
            bucket = 1   # 🟠 Low
        else:
            bucket = 0   # 🔴 Exhausted/Blocked
            
        cost = entry.cost_order()
        group_priority = -_route_group_rank_for_task(entry.family, task)
        family_priority = -family_rank.get(entry.family, 99)
        task_fit = _task_match_score(entry, task)
        entry_priority = -getattr(entry, "priority", 99)
        if prefer_low_cost:
            return (1, group_priority, task_fit, bucket, pct, -cost, family_priority, entry_priority)
        return (1, group_priority, task_fit, bucket, pct, -cost, family_priority, entry_priority)

    ranked = sorted(candidates, key=score, reverse=True)

    for entry in ranked:
        if entry.alias in seen_aliases:
            continue
        if len(chain) >= max_candidates:
            break
        group = route_group_for_family(entry.family)
        if route_group_count.get(group, 0) >= 1:
            continue
        quota = get_model_quota(entry, quota_statuses)
        runtime_ok, runtime_reason = _provider_runtime_allowed(entry, quota_statuses)
        if not runtime_ok:
            logger.debug("model_selector: fallback chain skipping %s — %s", entry.alias, runtime_reason)
            continue
        if not quota.available:
            logger.debug("model_selector: fallback chain skipping %s — quota %s", entry.alias, quota.reason)
            continue
        chain.append(entry)
        seen_aliases.add(entry.alias)
        route_group_count[group] = route_group_count.get(group, 0) + 1

    # Append available fallback-only models at the end of the chain
    fallback_candidates = [m for m in list_models() if m.enabled and m.supports_execute and getattr(m, "role", "primary") == "fallback_only"]
    sorted_fallbacks = sorted(fallback_candidates, key=lambda m: getattr(m, "priority", 99))
    for fb in sorted_fallbacks:
        if len(chain) >= max_candidates:
            break
        if fb.alias in seen_aliases:
            continue
        runtime_ok, runtime_reason = _provider_runtime_allowed(fb, quota_statuses)
        if not runtime_ok:
            logger.debug("model_selector: fallback chain skipping fallback-only %s — %s", fb.alias, runtime_reason)
            continue
        quota = get_model_quota(fb, quota_statuses)
        if not quota.available:
            logger.debug("model_selector: fallback chain skipping fallback-only %s — quota %s", fb.alias, quota.reason)
            continue
        chain.append(fb)
        seen_aliases.add(fb.alias)

    logger.info(
        "model_selector: fallback chain for task=%s: %s",
        task, [e.alias for e in chain]
    )
    return chain


def chain_to_legacy_dicts(chain: list) -> list[dict]:
    """Convert a list of ModelEntry to legacy {provider, model} dicts for run_agent."""
    result: list[dict] = []
    for e in chain:
        item = {
            "provider": e.provider,
            "model": e.model,
            "alias": e.alias,
            "quota_family": e.quota_family,
        }
        if e.codex_home:
            item["codex_home"] = e.codex_home
        if e.codex_account:
            item["codex_account"] = e.codex_account
        result.append(item)
    return result


def get_selected_alias_from_config(config: dict) -> Optional[str]:
    """Read selected_model_alias from config, with fallback to legacy fields."""
    ms = config.get("model_selection") or {}
    alias = ms.get("selected_model_alias")
    if alias:
        return alias
    # Fallback: try to resolve from legacy model.provider + model.model
    model_cfg = config.get("model") or {}
    provider = model_cfg.get("provider") or ""
    model = model_cfg.get("model") or model_cfg.get("default") or model_cfg.get("name") or ""
    if provider and model:
        from agent.model_registry import resolve_alias_from_config_model
        return resolve_alias_from_config_model(provider, model)
    return None
