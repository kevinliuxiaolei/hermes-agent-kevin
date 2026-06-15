"""
Unified /model command handler for Hermes.

Provides:
  - Display of all model families with quota grouped by family header
  - Model switching (/model <alias>)
  - Global persistence (/model <alias> --global)
  - Quota refresh (/model refresh)

Used by gateway/run.py to replace the old provider-specific picker.
"""
from __future__ import annotations

import logging
from typing import Optional

from agent.quota_display import badge as quota_badge_text
from agent.quota_display import display_from_quota_status, reason_text, pad_to_width

logger = logging.getLogger(__name__)

# ─── Family display config ────────────────────────────────────────────────────

FAMILY_CONFIG = {
    "gemini": {
        "label": "AGY Gemini",
        "quota_family": "agy_gemini",
    },
    "claude": {
        "label": "AGY Claude",
        "quota_family": "agy_claude",
    },
    "gpt": {
        "label": "AGY GPT-OSS",
        "quota_family": "agy_gpt",
    },
    "codex_plus": {
        "label": "Codex Plus",
        "quota_family": "codex_plus",
        "codex_home": "/home/lighthouse/.codex-plus",
    },
    "codex_business": {
        "label": "Codex Business",
        "quota_family": "codex_business",
        "codex_home": "/home/lighthouse/.codex-business",
    },
    "volcengine": {
        "label": "Volcengine",
        "quota_family": "volcengine",
    },
    "fallback_free": {
        "label": "Fallback",
        "quota_family": "fallback_free",
    },
}


def _load_quota_statuses(*, refresh: bool = False) -> dict:
    from agent.quota_registry import get_all_quota_statuses, get_all_quota_statuses_fast

    if refresh:
        return get_all_quota_statuses(refresh=True)
    statuses = get_all_quota_statuses_fast()
    if any(status.reason == "not_refreshed" for status in statuses.values()):
        return get_all_quota_statuses(refresh=False)
    return statuses


def _load_enabled_models():
    from agent.model_registry import get_enabled_models

    return get_enabled_models()


def _fmt_pct(pct: Optional[int]) -> str:
    return "N/A" if pct is None else f"{pct}%"


def _fmt_reset(reset_val: Optional[str]) -> str:
    return "N/A" if not reset_val else reset_val


def _family_status_text(family: str, fam_quota) -> str:
    display = display_from_quota_status(fam_quota)
    if display.status == "available":
        return "可用"
    return reason_text(display.status)


def _quota_display_for_entry(entry, quota_statuses: dict):
    from agent.quota_registry import get_model_quota

    quota = get_model_quota(entry, quota_statuses)
    return quota, display_from_quota_status(quota)


def _quota_rank_for_entry(entry, quota_statuses: dict, *, task: str = "chat") -> tuple:
    from agent.model_selector import _route_group_rank_for_task

    quota, display = _quota_display_for_entry(entry, quota_statuses)
    available_rank = 1 if quota.available else 0
    pct = display.percent if display.percent is not None else -1
    # Higher quota first. Ties follow the same task route-group policy as the
    # selector, keeping fallback_free behind healthy paid/subscription routes.
    return (
        available_rank,
        pct,
        -_route_group_rank_for_task(entry.family, task),
        -int(getattr(entry, "priority", 99) or 99),
        getattr(entry, "alias", ""),
    )


def _sorted_enabled_entries(entries: list, quota_statuses: dict, *, task: str = "chat") -> list:
    return sorted(
        entries,
        key=lambda entry: _quota_rank_for_entry(entry, quota_statuses, task=task),
        reverse=True,
    )


def _agy_account_summary(quota_status) -> str:
    raw = getattr(quota_status, "raw", {}) or {}
    account_count = raw.get("account_count")
    chosen = raw.get("chosen_account")
    active = raw.get("active_account")
    parts = []
    if account_count:
        parts.append(f"{account_count} accounts")
    if chosen:
        parts.append(f"chosen {chosen}")
    if active and active != chosen:
        parts.append(f"active {active}")
    execution_slots = raw.get("execution_slots")
    try:
        from agent.agy_slot_state import slot_summary
        execution_summary = (
            slot_summary({
                "slots": execution_slots,
                "last_executed_slot": raw.get("last_executed_slot"),
            })
            if isinstance(execution_slots, dict) and execution_slots
            else ""
        )
    except Exception:
        execution_summary = ""
    if execution_summary:
        parts.append(execution_summary)
    return " · ".join(parts)


def _model_row(entry, model_quota) -> str:
    context = entry.context_size or "N/A"
    if not entry.enabled:
        status = "disabled"
    elif not entry.supports_execute:
        status = "disabled"
    elif model_quota is not None and not model_quota.available:
        status = "unavailable"
    else:
        status = "ready"
    quota = _quota_badge(model_quota)
    return f"{entry.alias:<34} {quota:<8} {context:<5} {status}"


def _model_status_line(entry, family_quota, current_alias: Optional[str]) -> str:
    marker = "●" if entry.alias == current_alias else "○"
    return f"{marker} {_model_row(entry, family_quota)}"


def _build_family_block(
    family: str,
    entries: list,
    quota_statuses: dict,
    current_alias: Optional[str],
) -> list[str]:
    cfg = FAMILY_CONFIG.get(
        family,
        {"label": family.upper(), "quota_family": family},
    )
    fam_quota = quota_statuses.get(cfg["quota_family"])
    lines: list[str] = []

    label = cfg["label"]
    if fam_quota is not None:
        display = display_from_quota_status(fam_quota)
    else:
        display = display_from_quota_status(None)

    header = f"{label} · {_quota_badge(fam_quota)}"
    if family in {"gemini", "claude", "gpt"} and fam_quota is not None:
        extra = _agy_account_summary(fam_quota)
        if extra:
            header += f" · {extra}"
    lines.append(header)
    if display.detail and display.detail not in {"状态未知", "可用"}:
        lines.append(f"  {display.detail}")

    for entry in _sorted_enabled_entries(entries, quota_statuses):
        entry_quota, _entry_display = _quota_display_for_entry(entry, quota_statuses)
        lines.append(_model_status_line(entry, entry_quota, current_alias))
    lines.append("")
    return lines


def _resolve_model_state(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    *,
    task: str = "chat",
    refresh: bool = False,
) -> dict:
    from agent.model_selector import select_model_with_reason

    resolved_alias, requested_entry = _resolve_current_entry(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
    )

    if refresh:
        from agent.quota_registry import invalidate_cache
        invalidate_cache()

    try:
        quota_statuses = _load_quota_statuses(refresh=refresh)
    except Exception as exc:
        logger.warning("model_cmd: quota fetch failed: %s", exc)
        quota_statuses = {}

    selected = None
    try:
        preferred_alias = requested_entry.alias if requested_entry else resolved_alias
        selected = select_model_with_reason(
            task=task,
            preferred_alias=preferred_alias,
            refresh_quota=False,
        )
    except Exception as exc:
        logger.warning("model_cmd: selector state build failed: %s", exc)
        selected = None

    selected_entry = selected.entry if selected else None
    return {
        "auto_mode": str(resolved_alias or "").strip().lower() == "auto" or not resolved_alias,
        "requested_alias": resolved_alias,
        "requested_entry": requested_entry,
        "selected": selected,
        "selected_entry": selected_entry,
        "quota_statuses": quota_statuses,
    }


def _resolve_current_entry(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
):
    from agent.model_registry import (
        get_model_by_alias,
        get_model_by_provider_model,
        resolve_alias_from_config_model,
    )

    resolved_alias = current_alias
    if resolved_alias and str(resolved_alias).strip().lower() == "auto":
        return "auto", None

    entry = get_model_by_alias(resolved_alias) if resolved_alias else None
    if resolved_alias and entry is None:
        for provider_guess in ("antigravity-acp", "openai-codex"):
            resolved = resolve_alias_from_config_model(provider_guess, resolved_alias)
            if resolved:
                resolved_alias = resolved
                entry = get_model_by_alias(resolved)
                break

    if entry is None and current_provider and current_model_value:
        entry = get_model_by_provider_model(current_provider, current_model_value)
        if entry:
            resolved_alias = entry.alias

    return resolved_alias, entry


def _normalize_model_input(value: str) -> str:
    import re

    text = (value or "").strip().lower()
    text = text.replace("_", "-")
    text = re.sub(r"[^a-z0-9./+-]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def resolve_model_input(raw_value: str):
    """Resolve user /model input to a registry entry.

    Accepts exact aliases, unique alias prefixes, unique raw model ids,
    ``provider/model`` strings, and unique normalized display names.  Returns
    ``(entry, None)`` on success, otherwise ``(None, human_error)``.
    """
    from agent.model_registry import get_enabled_models, get_model_by_alias

    raw = (raw_value or "").strip()
    value = raw.lower()
    if not value:
        return None, "Empty model value."

    entry = get_model_by_alias(value)
    if entry:
        return entry, None

    all_enabled = get_enabled_models()

    def _unique_or_error(matches: list, label: str):
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            match_list = ", ".join(f"`{e.alias}`" for e in matches)
            return None, f"Ambiguous {label} `{raw}`. Matches: {match_list}"
        return None, None

    entry, error = _unique_or_error([e for e in all_enabled if e.alias.startswith(value)], "alias")
    if entry or error:
        return entry, error

    provider_matches = []
    for e in all_enabled:
        prefix = f"{e.provider}/"
        if value.startswith(prefix) and value[len(prefix):] == e.model.lower():
            provider_matches.append(e)
    entry, error = _unique_or_error(provider_matches, "provider/model")
    if entry or error:
        return entry, error

    norm = _normalize_model_input(raw)
    normalized_matches = [
        e for e in all_enabled
        if _normalize_model_input(e.model) == norm
        or _normalize_model_input(e.display_name) == norm
        or _normalize_model_input(e.alias) == norm
    ]
    entry, error = _unique_or_error(normalized_matches, "model")
    if entry or error:
        return entry, error

    from agent.model_registry import list_models
    known = ", ".join(f"`{e.alias}`" for e in list_models())
    return None, f"Unknown model alias `{raw}`.\nKnown: {known}"


def _quota_values(fam_quota) -> tuple[str, str, str, str]:
    if fam_quota is None:
        return "N/A", "N/A", "N/A", "N/A"
    q5h = _fmt_pct(
        fam_quota.quota_5h_percent
        if fam_quota.quota_5h_percent is not None
        else fam_quota.remaining_percent
    )
    q7d = _fmt_pct(fam_quota.quota_7d_percent)
    reset_5h = _fmt_reset(fam_quota.reset_5h or fam_quota.reset_in)
    reset_7d = _fmt_reset(fam_quota.reset_7d)
    return q5h, q7d, reset_5h, reset_7d


def _family_summary_line(family: str, quota_statuses: dict) -> str:
    cfg = FAMILY_CONFIG.get(
        family,
        {"label": family.upper(), "quota_family": family},
    )
    if family == "fallback_free":
        gemini = quota_statuses.get("gemini")
        nvidia = quota_statuses.get("nvidia_nim")
        parts = []
        for label, fam_quota in (("gemini", gemini), ("nvidia_nim", nvidia)):
            q5h, _q7d, reset_5h, _reset_7d = _quota_values(fam_quota)
            status = _family_status_text(family, fam_quota)
            parts.append(f"{label} 5h {q5h}, reset {reset_5h} | {status}")
        return f"  {cfg['label']}: " + "; ".join(parts)

    fam_quota = quota_statuses.get(cfg["quota_family"])
    q5h, q7d, reset_5h, reset_7d = _quota_values(fam_quota)
    status = _family_status_text(family, fam_quota)

    parts = [f"5h {q5h}"]
    if q7d != "N/A":
        parts.append(f"7d {q7d}")

    resets = [reset_5h]
    if reset_7d != "N/A":
        resets.append(reset_7d)

    quota_text = ", ".join(parts)
    reset_text = " / ".join(resets)
    return (
        f"  {cfg['label']} ({cfg['quota_family']}): "
        f"{quota_text}, reset {reset_text} | {status}"
    )


def _short_alias_label(entry) -> str:
    """Human-sized button/menu label for model entries."""
    alias_map = {
        "volcengine-agent-ark-code-latest": "Ark Agent",
        "volcengine-agent-doubao-pro": "Doubao Pro",
        "volcengine-agent-kimi-k2-6": "Kimi Agent",
        "volcengine-agent-doubao-lite": "Doubao Lite",
        "volcengine-coding-ark-code-latest": "Ark Code",
        "volcengine-coding-doubao-code": "Doubao Code",
        "volcengine-coding-kimi-k2-6": "Kimi Code",
        "volcengine-coding-deepseek-v3-2": "DeepSeek Code",
        "gemini-low": "Gemini Low",
        "gemini-medium": "Gemini Med",
        "gemini-high": "Gemini High",
        "gemini-pro-low": "Gemini Pro L",
        "gemini-pro-high": "Gemini Pro H",
        "claude-sonnet": "Claude Sonnet",
        "claude-opus": "Claude Opus",
        "gpt-oss": "GPT-OSS",
        "codex-plus-5.5": "Plus 5.5",
        "codex-plus-5.4": "Plus 5.4",
        "codex-plus-5.4-mini": "Plus Mini",
        "codex-plus-5.3-code": "Plus Code",
        "codex-plus-5.2": "Plus 5.2",
        "codex-business-5.5": "Biz 5.5",
        "codex-business-5.4": "Biz 5.4",
        "codex-business-5.4-mini": "Biz Mini",
        "codex-business-5.3-code": "Biz Code",
        "codex-business-5.2": "Biz 5.2",
        "gemini-fallback": "Gemini API",
        "gemini-pro-fallback": "Gemini Pro API",
        "nv-fallback": "NV DeepSeek",
        "nv-kimi": "NV Kimi",
        "nv-nemotron": "NV Nemotron",
    }
    if entry.alias in alias_map:
        return alias_map[entry.alias]
    label = entry.display_name or entry.alias
    for prefix in (
        "Volcengine Agent Plan / ",
        "Volcengine Coding Plan / ",
        "Gemini ",
        "GPT-",
    ):
        label = label.replace(prefix, "")
    return label[:28]


def _quota_badge(fam_quota) -> str:
    if fam_quota is None:
        return "N/A"
    return quota_badge_text(fam_quota)


def _entry_badge(entry, quota_statuses: dict) -> str:
    if not entry.supports_execute:
        return "禁用"
    model_quota, _display = _quota_display_for_entry(entry, quota_statuses)
    return _quota_badge(model_quota)


def _compact_entry_line(entry, quota_statuses: dict, marker: str = "○") -> str:
    """Short one-line menu row for the default picker.

    Long registry aliases make the Telegram menu hard to read. Keep the quick
    picker human-first (badge + short label); `/model list` remains the place
    for exact aliases and full diagnostics.
    """
    return f"  {marker} {_entry_badge(entry, quota_statuses)} {_short_alias_label(entry)}"


def _compact_family_line(family: str, quota_statuses: dict, max_label_width: int = 0) -> str:
    cfg = FAMILY_CONFIG.get(
        family,
        {"label": family.upper(), "quota_family": family},
    )
    label = cfg["label"]
    if max_label_width > 0:
        label = pad_to_width(label, max_label_width)
    if family in {"gemini", "claude", "gpt"}:
        extra = _agy_account_summary(quota_statuses.get(cfg["quota_family"]))
        badge = _quota_badge(quota_statuses.get(cfg["quota_family"]))
        if extra:
            return f"  {label}: {badge} · {extra}"
        return f"  {label}: {badge}"
    if family in {"volcengine", "fallback_free"}:
        from agent.model_registry import models_by_family

        seen: list[str] = []
        for entry in models_by_family().get(family, []):
            if entry.quota_family not in seen:
                seen.append(entry.quota_family)
        parts = [f"{qf} {_quota_badge(quota_statuses.get(qf))}" for qf in seen]
        return f"  {label}: " + " / ".join(parts)
    return f"  {label}: {_quota_badge(quota_statuses.get(cfg['quota_family']))}"


def _recommended_aliases_for_menu(quota_statuses: dict) -> list[str]:
    """Return enabled model aliases ordered by effective quota availability."""
    entries = _load_enabled_models()
    ranked = _sorted_enabled_entries(entries, quota_statuses)
    return [entry.alias for entry in ranked]


def model_picker_aliases(
    current_alias: Optional[str] = None,
    *,
    refresh: bool = False,
    limit: int = 8,
) -> list[str]:
    """Return the compact, quota/health-ranked alias set for model pickers."""
    from agent.model_registry import get_model_by_alias
    from agent.quota_registry import get_model_quota

    quota_statuses = _load_quota_statuses(refresh=refresh)
    aliases: list[str] = []
    if current_alias and get_model_by_alias(current_alias):
        aliases.append(current_alias)
    for alias in _recommended_aliases_for_menu(quota_statuses):
        entry = get_model_by_alias(alias)
        quota = get_model_quota(entry, quota_statuses) if entry else None
        if not entry or not entry.execute_enabled or (quota is not None and not quota.available):
            continue
        try:
            from agent.route_health import route_health_allows

            if not route_health_allows(entry.provider)[0]:
                continue
        except Exception:
            pass
        if alias not in aliases:
            aliases.append(alias)
        if len(aliases) >= max(1, limit):
            break
    return aliases


def _compact_recommendation_rank(entry, current_alias: Optional[str], quota_statuses: dict) -> tuple:
    from agent.quota_registry import get_model_quota
    fam_quota = get_model_quota(entry, quota_statuses)
    badge = _entry_badge(entry, quota_statuses)
    available = bool(fam_quota.available) if fam_quota is not None else False
    current = entry.alias == current_alias
    if badge == "禁用":
        tier = 3
    elif available:
        tier = 0
    elif badge == "N/A":
        tier = 2
    else:
        tier = 1
    # Current entry should stay visible near the top, but after a healthy
    # alternative if the user opened /model to switch away from it.
    current_rank = 0 if current else 1
    return (current_rank, tier, -int((fam_quota.remaining_percent or 0) if fam_quota else 0), entry.alias)


def _telegram_quota_family_lines(quota_statuses: dict) -> list[str]:
    lines: list[str] = []
    groups = [
        ("Codex", [("Biz", "codex_business"), ("Plus", "codex_plus")]),
        ("Volcengine", [("Agent", "volcengine_agent_plan"), ("Coding", "volcengine_coding_plan")]),
        ("AGY", [("Gemini", "agy_gemini"), ("Claude", "agy_claude"), ("GPT-OSS", "agy_gpt")]),
        ("Fallback", [("Gemini", "gemini"), ("NVIDIA", "nvidia_nim")]),
    ]
    for group_label, items in groups:
        lines.append(group_label)
        for short, quota_family in items:
            quota = quota_statuses.get(quota_family)
            badge = _quota_badge(quota)
            if quota is None:
                value = "N/A"
            else:
                value = badge
            lines.append(f"  {short:<8} {value}")
    return lines


def _compact_quota_overview_lines(quota_statuses: dict) -> list[str]:
    """Compact grouped quota summary shared by /model quick menus."""
    lines: list[str] = []
    groups = [
        ("Codex", [("Biz", "codex_business"), ("Plus", "codex_plus")]),
        ("Volcengine", [("Agent", "volcengine_agent_plan"), ("Coding", "volcengine_coding_plan")]),
        ("AGY", [("Gemini", "agy_gemini"), ("Claude", "agy_claude"), ("GPT-OSS", "agy_gpt")]),
        ("Fallback", [("Gemini API", "gemini"), ("NVIDIA", "nvidia_nim")]),
    ]
    for group_label, items in groups:
        parts = [f"{short} {_quota_badge(quota_statuses.get(quota_family))}" for short, quota_family in items]
        lines.append(f"  {group_label}: " + " / ".join(parts))
    return lines


def render_model_menu_telegram(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
) -> str:
    """Render a Telegram-friendly compact menu wrapped for monospace display."""
    from html import escape
    from agent.model_registry import get_model_by_alias

    state = _resolve_model_state(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        task="chat",
        refresh=refresh,
    )
    resolved_alias = state["requested_alias"]
    entry = state["requested_entry"]
    quota_statuses = state["quota_statuses"]

    try:
        from agent.routing_version import ROUTING_VERSION
        lines: list[str] = [f"模型选择 (v{ROUTING_VERSION})", ""]
    except ImportError:
        lines: list[str] = ["模型选择", ""]
    if state["auto_mode"]:
        lines.append("模式：自动（按额度与健康状态动态选择）")
        selected = state["selected_entry"]
        if selected:
            lines.append(f"当前可执行：{_entry_badge(selected, quota_statuses)} {_short_alias_label(selected)}")
    elif entry:
        lines.append(f"当前：{_entry_badge(entry, quota_statuses)} {_short_alias_label(entry)}")
        selected = state["selected_entry"]
        if selected and selected.alias != entry.alias:
            lines.append(f"实际：{_entry_badge(selected, quota_statuses)} {_short_alias_label(selected)}")
    elif resolved_alias:
        lines.append(f"当前：{resolved_alias} · 未解析")
    else:
        lines.append("当前：自动选择")
    lines.append("")

    lines.append("可用候选：")
    recommended = []
    for alias in _recommended_aliases_for_menu(quota_statuses):
        rec = get_model_by_alias(alias)
        if rec and rec.alias != resolved_alias:
            recommended.append(rec)
    recommended = recommended[:5]
    for rec in recommended:
        lines.append(_compact_entry_line(rec, quota_statuses))
    lines.append("")

    lines.append("额度概览：")
    lines.extend(_compact_quota_overview_lines(quota_statuses))
    lines.append("")
    lines.append("自动模式：/model auto；完整清单：/model list；当前详情：/model current。")

    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def render_model_menu(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
) -> str:
    """Render a compact Telegram-friendly /model menu; full registry is /model list."""
    from agent.model_registry import get_model_by_alias

    state = _resolve_model_state(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        task="chat",
        refresh=refresh,
    )
    resolved_alias = state["requested_alias"]
    entry = state["requested_entry"]
    quota_statuses = state["quota_statuses"]

    try:
        from agent.routing_version import ROUTING_VERSION
        lines: list[str] = [f"模型选择 (v{ROUTING_VERSION})", ""]
    except ImportError:
        lines: list[str] = ["模型选择", ""]
    if state["auto_mode"]:
        lines.append("模式：自动（按额度与健康状态动态选择）")
        selected = state["selected_entry"]
        if selected:
            lines.append(f"当前可执行：{_entry_badge(selected, quota_statuses)} {_short_alias_label(selected)}")
    elif entry:
        selected = state["selected_entry"]
        if selected and selected.alias != entry.alias:
            lines.append(f"当前：{_entry_badge(entry, quota_statuses)} {_short_alias_label(entry)}")
            lines.append(f"实际：{_entry_badge(selected, quota_statuses)} {_short_alias_label(selected)}")
        else:
            lines.append(f"当前：{_entry_badge(entry, quota_statuses)} {_short_alias_label(entry)}")
    elif resolved_alias:
        lines.append(f"当前：{resolved_alias} · 未解析")
    else:
        lines.append("当前：自动选择")
    lines.append("")

    lines.append("可用候选：")
    candidates = []
    for alias in _recommended_aliases_for_menu(quota_statuses):
        rec = get_model_by_alias(alias)
        if rec and rec.alias != resolved_alias:
            candidates.append(rec)
    recs_to_show = candidates[:5]
    for rec in recs_to_show:
        lines.append(_compact_entry_line(rec, quota_statuses))
    lines.append("")

    lines.append("额度概览：")
    lines.extend(_compact_quota_overview_lines(quota_statuses))
    lines.append("")

    lines.append("自动模式：/model auto；完整清单：/model list；当前详情：/model current。")
    return "\n".join(lines)


def render_model_list_parts(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
    include_all: bool = False,
) -> list[str]:
    """Build a readable quota-ordered route summary or full alias list."""

    state = _resolve_model_state(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        task="chat",
        refresh=refresh,
    )
    resolved_alias = state["requested_alias"]
    current_entry = state["requested_entry"]
    selected = state["selected"]
    quota_statuses = state["quota_statuses"]

    lines = [
        "完整模型清单（按有效额度倒序）"
        if include_all else
        "可用模型概览（按有效额度倒序）",
        "",
    ]
    if state["auto_mode"]:
        lines.append("当前模式：自动（按额度与健康状态动态选择）")
        if selected and selected.entry:
            lines.append(
                f"当前可执行：{_entry_badge(selected.entry, quota_statuses)} {_short_alias_label(selected.entry)}（{selected.entry.alias}）"
            )
    elif resolved_alias:
        entry = current_entry
        if entry:
            lines.append(f"当前偏好：{_entry_badge(entry, quota_statuses)} {_short_alias_label(entry)}（{entry.alias}）")
            if selected and selected.entry and selected.entry.alias != entry.alias:
                lines.append(
                    f"当前可执行：{_entry_badge(selected.entry, quota_statuses)} {_short_alias_label(selected.entry)}（{selected.entry.alias}）"
                )
        else:
            lines.append(f"当前偏好：{resolved_alias} · 未解析")
    else:
        lines.append("当前偏好：自动选择")
    lines.extend(["", "模型："])

    entries = _sorted_enabled_entries(_load_enabled_models(), quota_statuses)
    hidden_ready_count = 0
    unavailable_count = 0
    if not include_all:
        representatives = []
        seen_quota_families = set()
        for entry in entries:
            quota, _display = _quota_display_for_entry(entry, quota_statuses)
            if not entry.supports_execute or (quota is not None and not quota.available):
                unavailable_count += 1
                continue
            family_key = entry.quota_family or entry.family
            if family_key in seen_quota_families:
                hidden_ready_count += 1
                continue
            seen_quota_families.add(family_key)
            representatives.append(entry)
        entries = representatives
    rows = []
    for entry in entries:
        marker = "●" if entry.alias == resolved_alias else "○"
        quota, display = _quota_display_for_entry(entry, quota_statuses)
        badge = _entry_badge(entry, quota_statuses)
        context = entry.context_size or "未知上下文"
        if not entry.supports_execute:
            status = "不可执行"
        elif quota is not None and not quota.available:
            status = reason_text(display.status)
        else:
            status = "可执行"
        lbl = _short_alias_label(entry)
        rows.append({
            "marker": marker,
            "badge": badge,
            "label": lbl,
            "alias": entry.alias,
            "status": status,
            "context": context
        })

    if rows:
        for r in rows:
            lines.append(f"  {r['marker']} {r['badge']} {r['label']}")
            lines.append(f"      alias: {r['alias']} · {r['status']} · 上下文: {r['context']}")

    lines.append("")
    if not include_all:
        lines.append(
            f"已折叠同额度池可执行模型 {hidden_ready_count} 个；不可用模型 {unavailable_count} 个。"
        )
        lines.append("查看全部 alias：/model list all")
    lines.append("自动：/model auto；切换：/model <alias>；全局切换：加 --global；刷新：/model refresh")
    return ["\n".join(lines).strip()]


def render_model_list(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
) -> str:
    """
    Build the compact menu text for /model.
    """
    return render_model_menu(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        refresh=refresh,
    )


def render_model_detail(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
    include_all: bool = False,
) -> str:
    """Render the detailed family/model listing for diagnostics."""
    parts = render_model_list_parts(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        refresh=refresh,
        include_all=include_all,
    )
    return "\n\n".join(parts)


def render_current_model(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
) -> str:
    """Render the compact current-model summary."""
    state = _resolve_model_state(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        task="chat",
        refresh=refresh,
    )
    resolved_alias = state["requested_alias"]
    entry = state["requested_entry"]
    selected = state["selected"]
    selected_entry = state["selected_entry"]
    quota_statuses = state["quota_statuses"]

    if state["auto_mode"]:
        lines = ["当前模型模式：", "  mode: auto", ""]
        lines.append("当前可执行：")
        if selected_entry is not None:
            selected_quota, selected_display = _quota_display_for_entry(selected_entry, quota_statuses)
            lines.extend([
                f"  selected: {selected_entry.alias}",
                f"  display: {selected_entry.display_name}",
                f"  provider: {selected_entry.provider}",
                f"  model: {selected_entry.model}",
                f"  quota: {selected_display.icon} {selected_display.percent_text}",
                f"  reason: {selected.reason if selected else 'selected'}",
            ])
        else:
            lines.append("  selected: N/A")
        return "\n".join(lines)

    if entry is None:
        if resolved_alias:
            return "\n".join([
                "当前偏好模型：",
                f"  alias: {resolved_alias}",
                "  status: unresolved",
            ])
        return "\n".join([
            "当前偏好模型：",
            "  status: none set",
        ])

    requested_quota, requested_display = _quota_display_for_entry(entry, quota_statuses)
    lines: list[str] = [
        "当前偏好模型：",
        f"  requested: {entry.alias}",
        f"  display: {entry.display_name}",
        f"  provider: {entry.provider}",
        f"  model: {entry.model}",
        f"  context: {entry.context_size or 'N/A'}",
        "",
    ]
    lines.append("请求 quota：")
    lines.append(f"  family: {requested_quota.quota_family}")
    lines.append(f"  quota: {requested_display.icon} {requested_display.percent_text}")
    lines.append(f"  detail: {requested_display.detail}")
    lines.append(f"  status: {_family_status_text(entry.family, requested_quota)}")

    lines.append("")
    lines.append("实际选择：")
    if selected_entry is not None:
        selected_quota, selected_display = _quota_display_for_entry(selected_entry, quota_statuses)
        lines.append(f"  selected: {selected_entry.alias}")
        lines.append(f"  display: {selected_entry.display_name}")
        lines.append(f"  provider: {selected_entry.provider}")
        lines.append(f"  model: {selected_entry.model}")
        lines.append(f"  quota: {selected_display.icon} {selected_display.percent_text}")
        lines.append(f"  reason: {selected.reason if selected else 'selected'}")
        if selected_quota.raw.get("accounts"):
            extra = _agy_account_summary(selected_quota)
            if extra:
                lines.append(f"  AGY: {extra}")
    else:
        lines.append("  alias: N/A")
        lines.append("  display: N/A")
        if selected is not None and selected.skipped:
            skipped_alias, skipped_reason = selected.skipped[0]
            lines.append(f"  reason: no executable model available; first skipped {skipped_alias}: {skipped_reason}")
        elif selected is not None:
            lines.append(f"  reason: {selected.reason}")
        else:
            lines.append("  reason: selector unavailable")
    lines.append("")

    lines.append("其他：")
    cp_quota = quota_statuses.get("codex_plus")
    cp_status = "no_data"
    if cp_quota:
        cp_status = "available" if cp_quota.available else cp_quota.reason
    lines.append(f"  Codex Plus: {cp_status}")

    cb_quota = quota_statuses.get("codex_business")
    cb_status = "no_data"
    if cb_quota:
        cb_status = "available" if cb_quota.available else cb_quota.reason
    lines.append(f"  Codex Business: {cb_status}")

    agy = quota_statuses.get("agy_gemini")
    if agy is not None:
        lines.append(f"  AGY Gemini: {_agy_account_summary(agy) or '1 slot'}")

    lines.append(f"  Claude/GPT: quota visible, execute enabled (agy bridge)")
    return "\n".join(lines)


def render_current_model_telegram(
    current_alias: Optional[str] = None,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    refresh: bool = False,
) -> str:
    from html import escape

    state = _resolve_model_state(
        current_alias=current_alias,
        current_provider=current_provider,
        current_model_value=current_model_value,
        task="chat",
        refresh=refresh,
    )
    resolved_alias = state["requested_alias"]
    entry = state["requested_entry"]
    quota_statuses = state["quota_statuses"]

    lines: list[str] = ["当前详情", ""]
    if state["auto_mode"]:
        lines.append("模式：自动（按额度与健康状态动态选择）")
        actual_entry = state["selected_entry"]
        lines.append("")
        lines.append("当前可执行：")
        if actual_entry is not None:
            lines.append(f"  {_short_alias_label(actual_entry):<16} {actual_entry.alias}")
            lines.append(f"  后端：{actual_entry.provider} / {actual_entry.model}")
            lines.append(f"  原因：{state['selected'].reason if state['selected'] else 'selected'}")
        else:
            lines.append("  alias  N/A")
        lines.append("")
        lines.append("返回菜单继续切换。")
        return f"<pre>{escape(chr(10).join(lines))}</pre>"

    if entry is None:
        if resolved_alias:
            lines.append(f"当前：{resolved_alias} · 未解析")
        else:
            lines.append("当前：自动选择")
        return f"<pre>{escape(chr(10).join(lines))}</pre>"

    from agent.quota_registry import get_model_quota
    fam_quota = get_model_quota(entry, quota_statuses)
    lines.append(
        f"当前：{_short_alias_label(entry):<16} {entry.alias:<32} {_entry_badge(entry, quota_statuses)}"
    )
    lines.append(f"后端：{entry.provider} / {entry.model}")
    lines.append(f"上下文：{entry.context_size or 'N/A'}")

    if fam_quota is not None:
        display = display_from_quota_status(fam_quota)
        lines.append("")
        lines.append("额度池：")
        lines.append(f"  family  {fam_quota.quota_family}")
        lines.append(f"  quota   {display.icon} {display.percent_text}")
        lines.append(f"  detail  {display.detail}")
        lines.append(f"  status  {_family_status_text(entry.family, fam_quota)}")

    try:
        from agent.model_selector import select_model_with_reason

        selection = select_model_with_reason(
            task="chat",
            preferred_alias=entry.alias,
            refresh_quota=False,
        )
        actual_entry = selection.entry
    except Exception as exc:
        logger.warning("model_cmd: selector current-model check failed: %s", exc)
        selection = None
        actual_entry = None

    lines.append("")
    lines.append("实际执行：")
    if actual_entry is not None:
        lines.append(f"  {_short_alias_label(actual_entry):<16} {actual_entry.alias}")
        lines.append(f"  后端：{actual_entry.provider} / {actual_entry.model}")
        lines.append(f"  原因：{selection.reason if selection else 'selected'}")
    else:
        lines.append("  alias  N/A")
        lines.append("  后端   N/A")
        if selection is not None and selection.skipped:
            skipped_alias, skipped_reason = selection.skipped[0]
            lines.append(f"  原因   no executable model available; first skipped {skipped_alias}: {skipped_reason}")
        elif selection is not None:
            lines.append(f"  原因   {selection.reason}")
        else:
            lines.append("  原因   selector unavailable")

    lines.append("")
    lines.append("返回菜单继续切换。")
    return f"<pre>{escape(chr(10).join(lines))}</pre>"


def _build_switch_message(entry, fam_quota, scope: str) -> str:
    label = _short_alias_label(entry)
    quota = _quota_badge(fam_quota)
    scope_text = "全局" if scope.startswith("global") else "当前会话"
    lines: list[str] = [
        f"已切换：{label}",
        f"alias：{entry.alias}",
        f"后端：{entry.provider} / {entry.model}",
        f"额度：{quota} · 范围：{scope_text}",
    ]
    if entry.context_size:
        lines.append(f"上下文：{entry.context_size}")
    if entry.codex_home:
        lines.append(f"CODEX_HOME：{entry.codex_home}")
    if not entry.supports_execute:
        lines.append("注意：该模型当前仅展示额度，执行桥接未启用。")
    elif fam_quota is not None and not fam_quota.available:
        lines.append("注意：当前额度不可用，实际执行会自动 fallback。")
    return "\n".join(lines)


def handle_model_command(
    raw_args: str,
    current_alias: Optional[str],
    config: dict,
    current_provider: Optional[str] = None,
    current_model_value: Optional[str] = None,
    save_config_fn=None,
    session_switch_fn=None,
) -> dict:
    """
    Handle a /model command invocation.
    """
    from agent.model_registry import get_enabled_models, get_model_by_alias
    from agent.quota_registry import get_model_quota, invalidate_cache

    args = raw_args.strip()
    persist_global = "--global" in args
    if persist_global:
        args = args.replace("--global", "").strip()

    if args.lower() in ("refresh", "--refresh"):
        invalidate_cache()
        text = render_model_list(current_alias=current_alias, refresh=True)
        parts = render_model_list_parts(current_alias=current_alias, refresh=True)
        return {"text": text, "parts": parts, "new_alias": None, "codex_home": None, "persisted": False}

    if args.lower() == "current":
        text = render_current_model(
            current_alias=current_alias,
            current_provider=current_provider,
            current_model_value=current_model_value,
        )
        return {"text": text, "new_alias": None, "codex_home": None, "persisted": False}

    if args.lower() in ("list", "preview", "inspect"):
        text = render_model_detail(
            current_alias=current_alias,
            current_provider=current_provider,
            current_model_value=current_model_value,
        )
        parts = render_model_list_parts(
            current_alias=current_alias,
            current_provider=current_provider,
            current_model_value=current_model_value,
        )
        return {"text": text, "parts": parts, "new_alias": None, "codex_home": None, "persisted": False}

    if args.lower() in ("list all", "all", "dump"):
        text = render_model_detail(
            current_alias=current_alias,
            current_provider=current_provider,
            current_model_value=current_model_value,
            include_all=True,
        )
        parts = render_model_list_parts(
            current_alias=current_alias,
            current_provider=current_provider,
            current_model_value=current_model_value,
            include_all=True,
        )
        return {"text": text, "parts": parts, "new_alias": None, "codex_home": None, "persisted": False}

    if not args:
        text = render_model_list(current_alias=current_alias)
        parts = render_model_list_parts(current_alias=current_alias)
        return {"text": text, "parts": parts, "new_alias": None, "codex_home": None, "persisted": False}

    if args.lower() == "auto":
        text = "已切换：自动模式\n系统将在每个新任务边界按额度与健康状态选择主路由。"
        if persist_global and save_config_fn:
            try:
                save_config_fn("model_selection.selected_model_alias", "auto")
                save_config_fn("model_selection.selected_provider", "")
                save_config_fn("model_selection.selected_model", "")
                save_config_fn("model_selection.codex_home", "")
                save_config_fn("model_selection.codex_account", "")
                text += "\n\n已保存到 config.yaml（--global）"
            except Exception as exc:
                text += f"\n\n保存失败：{exc}"
        elif not persist_global:
            text += "\n\n仅当前会话生效；加 --global 可持久化。"
        return {
            "text": text,
            "new_alias": "auto",
            "new_provider": None,
            "new_model": None,
            "codex_home": None,
            "codex_account": None,
            "auto_mode": True,
            "persisted": persist_global,
        }

    entry, resolve_error = resolve_model_input(args)
    if entry is None:
        return {
            "text": resolve_error or f"Unknown model `{args}`.",
            "new_alias": None,
            "codex_home": None,
            "persisted": False,
        }

    quota_statuses = _load_quota_statuses()
    fam_quota = get_model_quota(entry, quota_statuses)
    scope = "global (config.yaml)" if persist_global else "session"
    text = _build_switch_message(entry, fam_quota, scope)

    if persist_global and save_config_fn:
        try:
            save_config_fn("model_selection.selected_model_alias", entry.alias)
            save_config_fn("model_selection.selected_provider", entry.provider)
            save_config_fn("model_selection.selected_model", entry.model)
            save_config_fn("model.provider", entry.provider)
            save_config_fn("model.model", entry.model)
            if entry.codex_home:
                save_config_fn("model_selection.codex_home", entry.codex_home)
                save_config_fn("model_selection.codex_account", entry.codex_account or "")
            text += "\n\n已保存到 config.yaml（--global）"
        except Exception as exc:
            text += f"\n\n保存失败：{exc}"
    elif not persist_global:
        text += "\n\n仅当前会话生效；加 --global 可持久化。"

    if session_switch_fn and entry.supports_execute:
        try:
            session_switch_fn(
                provider=entry.provider,
                model=entry.model,
                codex_home=entry.codex_home,
            )
        except Exception as exc:
            logger.warning("model_cmd: session switch failed: %s", exc)

    return {
        "text": text,
        "new_alias": entry.alias,
        "new_provider": entry.provider,
        "new_model": entry.model,
        "codex_home": entry.codex_home,
        "codex_account": entry.codex_account,
        "persisted": persist_global,
    }


def switch_model(
    alias: str,
    global_flag: bool = False,
    *,
    current_alias: Optional[str] = None,
    config: Optional[dict] = None,
    save_config_fn=None,
    session_switch_fn=None,
) -> dict:
    """Unified alias switch entrypoint used by text commands and callbacks."""
    raw_args = alias.strip()
    if global_flag:
        raw_args = f"{raw_args} --global"
    return handle_model_command(
        raw_args=raw_args,
        current_alias=current_alias,
        config=config or {},
        save_config_fn=save_config_fn,
        session_switch_fn=session_switch_fn,
    )
