from __future__ import annotations

from types import SimpleNamespace

from gateway.run import GatewayRunner

from hermes_cli.fallback_config import (
    VOLC_SELECTION_FAMILY,
    VOLC_SELECTION_POLICY,
    build_effective_fallback_chain,
)


LOGICAL_VOLC = {
    "selection_family": VOLC_SELECTION_FAMILY,
    "selection_policy": VOLC_SELECTION_POLICY,
}


def test_public_volc_coding_route_prepends_same_model_agent_sibling():
    existing = [{"provider": "openrouter", "model": "other"}]
    chain = build_effective_fallback_chain(
        {
            "provider": "volcengine-coding-plan",
            "model": "deepseek-v4-pro",
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "api_mode": "openai_chat",
        },
        LOGICAL_VOLC,
        existing,
    )

    assert chain == [
        {
            "provider": "volcengine-agent-plan",
            "model": "deepseek-v4-pro",
            "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "api_mode": "openai_chat",
        },
        {"provider": "openrouter", "model": "other"},
    ]


def test_typed_concrete_volc_route_stays_concrete_without_logical_marker():
    existing = [{"provider": "openrouter", "model": "other"}]
    assert build_effective_fallback_chain(
        {"provider": "volcengine-coding-plan", "model": "glm-5.3"},
        None,
        existing,
    ) == existing


def test_route_dedup_keeps_same_model_on_distinct_sibling_routes():
    chain = build_effective_fallback_chain(
        {"provider": "volcengine-coding-plan", "model": "glm-5.3"},
        LOGICAL_VOLC,
        [
            {"provider": "volcengine-coding-plan", "model": "glm-5.3"},
            {"provider": "volcengine-agent-plan", "model": "glm-5.3"},
        ],
    )
    assert [entry["provider"] for entry in chain] == [
        "volcengine-agent-plan",
        "volcengine-coding-plan",
    ]


def test_gateway_fresh_route_builds_policy_derived_sibling():
    route = {
        "model": "glm-5.3",
        "runtime": {
            "provider": "volcengine-coding-plan",
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "api_mode": "openai_chat",
        },
        **LOGICAL_VOLC,
    }
    chain = GatewayRunner._effective_fallback_chain_for_route(
        route, [{"provider": "openrouter", "model": "other"}]
    )
    assert chain[0] == {
        "provider": "volcengine-agent-plan",
        "model": "glm-5.3",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "api_mode": "openai_chat",
    }


def test_gateway_reused_agent_refresh_preserves_sibling_policy():
    agent = SimpleNamespace(
        provider="volcengine-coding-plan",
        model="deepseek-v4-flash",
        base_url="https://ark.cn-beijing.volces.com/api/coding/v3",
        api_mode="openai_chat",
        _selection_family="volc",
        _selection_policy="coding-primary-agent-sibling",
        _fallback_chain=[],
        _fallback_activated=False,
        _fallback_index=0,
        _rate_limited_until=0,
        _unavailable_fallback_keys=set(),
    )
    GatewayRunner._apply_fallback_chain_to_agent(
        agent, [{"provider": "openrouter", "model": "other"}]
    )
    assert agent._fallback_chain[0]["provider"] == "volcengine-agent-plan"
    assert agent._fallback_chain[0]["model"] == "deepseek-v4-flash"


def test_route_dedup_drops_unresolved_duplicate_of_sibling_route():
    chain = build_effective_fallback_chain(
        {
            "provider": "volcengine-coding-plan",
            "model": "glm-5.3",
            "base_url": "https://ark.cn-beijing.volces.com/api/coding/v3",
            "api_mode": "chat_completions",
        },
        LOGICAL_VOLC,
        [{"provider": "volcengine-agent-plan", "model": "glm-5.3"}],
    )
    assert chain == [{
        "provider": "volcengine-agent-plan",
        "model": "glm-5.3",
        "base_url": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "api_mode": "chat_completions",
    }]
