from datetime import datetime, timedelta

from agent.quota_display import (
    QuotaWindowDisplay,
    badge,
    display_from_windows,
    format_reset,
    parse_percent,
)
from agent.quota_registry import QuotaFamilyStatus


def test_parse_percent_treats_na_as_zero():
    assert parse_percent("N/A") == 0
    assert parse_percent("NA") == 0
    assert parse_percent("未知") == 0
    assert parse_percent("🔴 20%") == 20


def test_display_uses_tightest_window_and_health_icon():
    now = datetime(2026, 6, 10, 17, 0, 0)
    display = display_from_windows(
        [
            QuotaWindowDisplay("5h", 99, now + timedelta(hours=5)),
            QuotaWindowDisplay("7d", 0, now + timedelta(hours=15)),
        ],
        available=False,
        reason="7d_exhausted",
        now=now,
        include_reset_absolute=True,
    )

    assert display.icon == "🔴"
    assert display.percent_text == "0%"
    assert display.primary_window == "7d"
    assert "5h 99% / 7d 0%（以 7d 为准）" in display.detail
    assert "15h后" in display.detail


def test_health_icon_thresholds():
    assert display_from_windows([QuotaWindowDisplay("5h", 50)], available=True).icon == "🟢"
    assert display_from_windows([QuotaWindowDisplay("5h", 20)], available=True).icon == "🟡"
    assert display_from_windows([QuotaWindowDisplay("5h", 19)], available=True).icon == "🟠"
    assert display_from_windows([QuotaWindowDisplay("5h", 0)], available=False).icon == "🔴"


def test_badge_distinguishes_available_without_percentage_from_unavailable():
    available = QuotaFamilyStatus(
        quota_family="agy_claude",
        provider_family="antigravity",
        available=True,
        reason="available",
    )
    unavailable = QuotaFamilyStatus(
        quota_family="agy_claude",
        provider_family="antigravity",
        available=False,
        reason="quota_exhausted",
    )
    assert badge(available) == "可用"
    assert badge(unavailable) == "🔴0%"


def test_format_reset_uses_days_after_24h():
    now = datetime(2026, 6, 10, 17, 0, 0)
    reset = now + timedelta(hours=49)
    assert "2d后" in format_reset(reset, now, include_absolute=True)
