from agent.route_plan import RoutePlan
from tools.hermes_model_doctor import validate_route_plan


def test_validate_route_plan_accepts_bounded_unique_progression():
    plan = RoutePlan(
        task="cron",
        mode="pin_with_fallback",
        primary={"provider": "primary", "model": "a"},
        fallbacks=[
            {"provider": "fallback-a", "model": "b"},
            {"provider": "fallback-b", "model": "c"},
        ],
        max_attempts=3,
    )

    assert validate_route_plan(plan) == []


def test_validate_route_plan_rejects_primary_duplicate_and_overflow():
    plan = RoutePlan(
        task="cron",
        mode="pin_with_fallback",
        primary={"provider": "primary", "model": "a"},
        fallbacks=[
            {"provider": "primary", "model": "a"},
            {"provider": "fallback", "model": "b"},
        ],
        max_attempts=2,
    )

    issues = validate_route_plan(plan)

    assert "route count exceeds max_route_attempts" in issues
    assert "fallback repeats primary: primary/a" in issues
