"""Shared quota display formatting for model routing UI and cron reports.

This module intentionally contains presentation-only logic.  Data collection stays
in quota_registry.py or the cron collection script; both paths call these helpers
so /model and hourly quota alerts use the same health icons, primary-window
selection, N/A handling, and reset-time wording.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class QuotaWindowDisplay:
    label: str
    percent: Optional[int]
    reset: Optional[Any] = None


@dataclass(frozen=True)
class QuotaDisplay:
    icon: str
    percent: Optional[int]
    percent_text: str
    status: str
    detail: str
    reset_text: str = "-"
    primary_window: Optional[str] = None


_NA_TOKENS = {"", "N/A", "NA", "N.A.", "NULL", "NONE", "UNKNOWN", "未知"}
_AUTH_REASONS = {
    "no_credentials",
    "token_expired",
    "expired",
    "auth_error",
    "auth_missing",
    "missing",
    "unauthorized",
}


def parse_percent(value: Any) -> int:
    """Normalize quota percentages.

    Policy: N/A/NA/未知/None means unavailable/exhausted (0%) for quota health.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return max(0, min(100, int(value)))
    text = str(value).strip()
    if not text or text.upper() in _NA_TOKENS or text == "-":
        return 0
    lowered = text.lower()
    if "exhausted" in lowered or "rate limit" in lowered or "耗尽" in lowered:
        return 0
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
    if match:
        return max(0, min(100, int(float(match.group(1)))))
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return max(0, min(100, int(float(text))))
    return 0


def health_icon(percent: Optional[int], *, available: Optional[bool] = None, reason: str | None = None) -> str:
    reason_text = (reason or "").lower()
    if percent is None:
        if available is False and any(token in reason_text for token in _AUTH_REASONS):
            return "🟠"
        if available is False:
            return "🔴"
        return "⚪"
    if percent <= 0:
        if any(token in reason_text for token in _AUTH_REASONS):
            return "🟠"
        return "🔴"
    if percent < 20:
        return "🟠"
    if percent < 50:
        return "🟡"
    return "🟢"


def percent_text(percent: Optional[int]) -> str:
    return "N/A" if percent is None else f"{percent}%"


def _parse_relative_duration(text: str) -> Optional[timedelta]:
    s = (text or "").strip().lower()
    if not s or s in {"-", "n/a", "na", "none", "unknown", "未知"}:
        return None
    days = hours = minutes = seconds = 0
    matched = False
    for value, unit in re.findall(r"(\d+)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)", s):
        matched = True
        n = int(value)
        if unit.startswith("d"):
            days += n
        elif unit.startswith("h"):
            hours += n
        elif unit.startswith("m"):
            minutes += n
        elif unit.startswith("s"):
            seconds += n
    if matched:
        return timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
    return None


def _relative_text(delta: timedelta) -> str:
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        return "已重置"
    hours = total_seconds // 3600
    if hours >= 24:
        return f"{hours // 24}d后"
    if hours >= 1:
        return f"{hours}h后"
    minutes = max(1, total_seconds // 60)
    return f"{minutes}m后"


def format_reset(reset: Any, now: Optional[datetime] = None, *, include_absolute: bool = True) -> str:
    if reset is None:
        return "-"
    now = now or datetime.now()
    if isinstance(reset, datetime):
        delta = reset - now
        rel = _relative_text(delta)
        if include_absolute:
            return f"{reset.strftime('%F %T')} ({rel})"
        return rel
    text = str(reset).strip()
    if not text or text.upper() in _NA_TOKENS or text == "-":
        return "-"

    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is not None and now.tzinfo is None:
                dt = dt.replace(tzinfo=None)
            delta = dt - now
            rel = _relative_text(delta)
            return f"{dt.strftime('%F %T')} ({rel})" if include_absolute else rel
        except Exception:
            pass

    delta = _parse_relative_duration(text)
    if delta is not None:
        return _relative_text(delta)
    return text


def select_primary_window(windows: Iterable[QuotaWindowDisplay]) -> Optional[QuotaWindowDisplay]:
    normalized = [w for w in windows if w is not None]
    if not normalized:
        return None
    return min(
        normalized,
        key=lambda w: (
            w.percent if w.percent is not None else 101,
            str(w.label or ""),
        ),
    )


def display_from_windows(
    windows: Iterable[QuotaWindowDisplay],
    *,
    available: Optional[bool] = None,
    reason: str | None = None,
    now: Optional[datetime] = None,
    include_reset_absolute: bool = True,
) -> QuotaDisplay:
    window_list = list(windows)
    primary = select_primary_window(window_list)
    pct = primary.percent if primary else None
    if pct is None and available is False:
        pct = 0
    icon = health_icon(pct, available=available, reason=reason)
    ptxt = percent_text(pct)
    if primary:
        parts = [f"{w.label} {percent_text(w.percent)}" for w in window_list]
        primary_note = f"（以 {primary.label} 为准）" if len(window_list) > 1 else ""
        reset_text = format_reset(primary.reset, now, include_absolute=include_reset_absolute)
        detail = f"{' / '.join(parts)}{primary_note} (重置: {reset_text})"
        primary_label = primary.label
    else:
        reset_text = "-"
        detail = reason_text(reason) if reason else "状态未知"
        primary_label = None
    return QuotaDisplay(
        icon=icon,
        percent=pct,
        percent_text=ptxt,
        status="available" if available else (reason or "unknown"),
        detail=detail,
        reset_text=reset_text,
        primary_window=primary_label,
    )


def _parse_percent_optional(value: Any) -> Optional[int]:
    """Parse a quota percentage only when the source field is present.

    ``parse_percent(None)`` intentionally maps unknown/N/A to 0 for alerting,
    but display code must not invent an exhausted 5h window when a provider only
    reports ``remaining_percent`` (for example Volcengine/Ark plan accounts).
    """
    if value is None:
        return None
    return parse_percent(value)


def windows_from_quota_status(status: Any) -> list[QuotaWindowDisplay]:
    if status is None:
        return []
    windows: list[QuotaWindowDisplay] = []
    raw_q5 = getattr(status, "quota_5h_percent", None)
    q5 = _parse_percent_optional(raw_q5)
    if q5 is None:
        q5 = _parse_percent_optional(getattr(status, "remaining_percent", None))
    if q5 is not None:
        windows.append(QuotaWindowDisplay("5h", q5, getattr(status, "reset_5h", None) or getattr(status, "reset_in", None)))
    q7 = _parse_percent_optional(getattr(status, "quota_7d_percent", None))
    if q7 is not None:
        windows.append(QuotaWindowDisplay("7d", q7, getattr(status, "reset_7d", None)))
    return windows


def display_from_quota_status(
    status: Any,
    *,
    now: Optional[datetime] = None,
    include_reset_absolute: bool = False,
) -> QuotaDisplay:
    if status is None:
        return QuotaDisplay("⚪", None, "N/A", "no_data", "状态未知", "-")
    windows = windows_from_quota_status(status)
    available = bool(getattr(status, "available", False))
    reason = getattr(status, "reason", None) or ("available" if available else "unknown")
    if windows:
        return display_from_windows(
            windows,
            available=available,
            reason=reason,
            now=now,
            include_reset_absolute=include_reset_absolute,
        )
    raw_remaining = getattr(status, "remaining_percent", None)
    pct = None if raw_remaining is None else parse_percent(raw_remaining)
    if pct is None and not available:
        pct = 0 if reason != "no_data" else None
    icon = health_icon(pct, available=available, reason=reason)
    reset_text = format_reset(getattr(status, "reset_in", None), now, include_absolute=include_reset_absolute)
    detail = reason_text(reason) if not available else "可用"
    if reset_text != "-":
        detail = f"{detail} (重置: {reset_text})"
    return QuotaDisplay(icon, pct, percent_text(pct), reason, detail, reset_text)


def reason_text(reason: str | None) -> str:
    mapping = {
        "available": "可用",
        "no_credentials": "未登录/未配置凭证",
        "token_expired": "Token 已过期（需重新登录）",
        "expired": "Token 已过期（需重新登录）",
        "auth_error": "认证异常",
        "auth_missing": "未配置凭证",
        "missing": "未登录",
        "rate_limited": "已限流",
        "quota_exhausted": "额度已耗尽",
        "5h_exhausted": "5h 额度已耗尽",
        "7d_exhausted": "7d 额度已耗尽",
        "all_models_na": "全部模型额度不可用",
        "no_data": "状态未知",
        "not_refreshed": "状态未知",
        "unknown": "状态未知",
        "command_failed": "额度命令执行失败",
        "unavailable": "不可用",
        "cooldown": "冷却中",
    }
    return mapping.get((reason or "").lower(), reason or "状态未知")


def badge(status: Any) -> str:
    d = display_from_quota_status(status)
    if d.percent is None:
        if d.icon in {"🔴", "🟠"}:
            return "不可用"
        return "可用" if d.status == "available" else "N/A"
    return f"{d.icon}{d.percent_text}"


def display_width(s: str) -> int:
    import unicodedata
    width = 0
    for char in s:
        if char in "🟢🟡🔴⚪🟠":
            width += 2
        elif unicodedata.east_asian_width(char) in ("W", "F"):
            width += 2
        else:
            width += 1
    return width


def pad_to_width(s: str, target_width: int) -> str:
    w = display_width(s)
    if w >= target_width:
        return s
    return s + " " * (target_width - w)


def percent_text_padded(pct_text: str) -> str:
    w = len(pct_text)
    if w >= 4:
        return pct_text
    return pct_text + " " * (4 - w)


def cron_line(label: str, display: QuotaDisplay, max_label_width: int = 0) -> str:
    padded_pct = percent_text_padded(display.percent_text)
    padded_lbl = pad_to_width(label, max_label_width) if max_label_width > 0 else label
    return f"  {display.icon} {padded_pct} {padded_lbl}: {display.detail}"

