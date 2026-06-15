"""
Unified Model Registry for Hermes.

Single source of truth for all model entries used by:
  - /model command display
  - Model selector (select_model, build_fallback_chain)
  - Fallback routing

Do NOT add model lists elsewhere. Add them here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import os
import shutil


COST_ORDER = {"low": 0, "medium": 1, "high": 2, "very_high": 3}
ROUTE_GROUP_ORDER = ["codex", "volcengine", "gemini", "claude", "gpt", "fallback_free"]


def _resolve_antigravity_acp_command() -> str:
    hermes_bridge = "/home/lighthouse/.hermes/bin/agy_acp_bridge.py"
    return (
        os.getenv("HERMES_ANTIGRAVITY_ACP_COMMAND", "").strip()
        or os.getenv("HERMES_ANTIGRAVITY_CLI", "").strip()
        or os.getenv("HERMES_COPILOT_ACP_COMMAND", "").strip()
        or os.getenv("COPILOT_CLI_PATH", "").strip()
        or (hermes_bridge if _command_exists(hermes_bridge) else "")
        or "copilot"
    )


def _command_exists(command: str) -> bool:
    if not command:
        return False
    expanded = os.path.expanduser(command)
    if os.path.isabs(expanded) or os.sep in expanded:
        path = Path(expanded)
        return path.exists() and os.access(path, os.X_OK)
    return shutil.which(expanded) is not None


@dataclass
class ModelEntry:
    alias: str
    provider: str
    model: str
    display_name: str
    family: str            # gemini | claude | gpt | codex_plus | codex_business | fallback_free
    quota_family: str      # agy_gemini | agy_claude | agy_gpt | codex_plus | codex_business | nvidia_nim
    cost_tier: str         # low | medium | high | very_high
    task_tags: list = field(default_factory=list)
    supports_execute: bool = True   # False = quota visible but bridge not enabled
    execute_enabled: Optional[bool] = None
    supports_background: bool = True
    enabled: bool = True
    codex_home: Optional[str] = None   # for Codex models: which CODEX_HOME to use
    codex_account: Optional[str] = None  # "plus" or "business"
    context_size: Optional[str] = None  # human-readable e.g. "1M", "200K"
    context_limit_tokens: Optional[int] = None
    priority: int = 99
    role: str = "primary"

    def __post_init__(self) -> None:
        if self.execute_enabled is None:
            self.execute_enabled = self.supports_execute
        else:
            self.supports_execute = bool(self.execute_enabled)

        if self.provider == "antigravity-acp" and self.supports_execute:
            cmd = _resolve_antigravity_acp_command()
            if not _command_exists(cmd):
                self.supports_execute = False
                self.execute_enabled = False


    def cost_order(self) -> int:
        return COST_ORDER.get(self.cost_tier, 99)


# ─── Registry ────────────────────────────────────────────────────────────────

_REGISTRY: list[ModelEntry] = [

    # ── Volcengine / Ark Plans ────────────────────────────────────────────────
    # One logical family (volcengine), two execution plans/providers.  Aliases
    # include the plan name because many raw model ids are identical across
    # /api/plan/v3 and /api/coding/v3.
    ModelEntry(
        alias="volcengine-agent-ark-code-latest",
        provider="volcengine-agent-plan",
        model="ark-code-latest",
        display_name="Volcengine Agent Plan / Ark Code Latest",
        family="volcengine",
        quota_family="volcengine_agent_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["light", "chat", "summary", "cron", "background", "analysis"],
        priority=1,
    ),
    ModelEntry(
        alias="volcengine-agent-doubao-pro",
        provider="volcengine-agent-plan",
        model="doubao-seed-2.0-pro",
        display_name="Volcengine Agent Plan / Doubao Seed 2.0 Pro",
        family="volcengine",
        quota_family="volcengine_agent_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["chat", "summary", "cron", "background", "analysis"],
        priority=2,
    ),
    ModelEntry(
        alias="volcengine-agent-kimi-k2-6",
        provider="volcengine-agent-plan",
        model="kimi-k2.6",
        display_name="Volcengine Agent Plan / Kimi K2.6",
        family="volcengine",
        quota_family="volcengine_agent_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["chat", "analysis", "background"],
        priority=3,
    ),
    ModelEntry(
        alias="volcengine-agent-doubao-lite",
        provider="volcengine-agent-plan",
        model="doubao-seed-2.0-lite",
        display_name="Volcengine Agent Plan / Doubao Seed 2.0 Lite",
        family="volcengine",
        quota_family="volcengine_agent_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["light", "summary", "cron", "chat"],
        priority=4,
    ),
    ModelEntry(
        alias="volcengine-coding-ark-code-latest",
        provider="volcengine-coding-plan",
        model="ark-code-latest",
        display_name="Volcengine Coding Plan / Ark Code Latest",
        family="volcengine",
        quota_family="volcengine_coding_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["code", "review", "analysis"],
        priority=10,
    ),
    ModelEntry(
        alias="volcengine-coding-doubao-code",
        provider="volcengine-coding-plan",
        model="doubao-seed-2.0-code",
        display_name="Volcengine Coding Plan / Doubao Seed 2.0 Code",
        family="volcengine",
        quota_family="volcengine_coding_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["code", "review"],
        priority=11,
    ),
    ModelEntry(
        alias="volcengine-coding-kimi-k2-6",
        provider="volcengine-coding-plan",
        model="kimi-k2.6",
        display_name="Volcengine Coding Plan / Kimi K2.6",
        family="volcengine",
        quota_family="volcengine_coding_plan",
        cost_tier="low",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["code", "review", "analysis"],
        priority=12,
    ),
    ModelEntry(
        alias="volcengine-coding-deepseek-v3-2",
        provider="volcengine-coding-plan",
        model="deepseek-v3.2",
        display_name="Volcengine Coding Plan / DeepSeek V3.2",
        family="volcengine",
        quota_family="volcengine_coding_plan",
        cost_tier="low",
        context_size="128K",
        context_limit_tokens=128_000,
        task_tags=["code", "review", "analysis"],
        priority=13,
    ),

    # ── Gemini / Antigravity ─────────────────────────────────────────────────

    ModelEntry(
        alias="gemini-low",
        provider="antigravity-acp",
        model="models/gemini-flash-lite-latest",
        display_name="Gemini 3.5 Flash (Low)",
        family="gemini",
        quota_family="agy_gemini",
        cost_tier="low",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["light", "chat", "summary", "cron"],
    ),
    ModelEntry(
        alias="gemini-medium",
        provider="antigravity-acp",
        model="models/gemini-flash-lite-latest",
        display_name="Gemini 3.5 Flash (Medium)",
        family="gemini",
        quota_family="agy_gemini",
        cost_tier="low",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["chat", "summary", "cron"],
    ),
    ModelEntry(
        alias="gemini-high",
        provider="antigravity-acp",
        model="models/gemini-flash-latest",
        display_name="Gemini 3.5 Flash (High)",
        family="gemini",
        quota_family="agy_gemini",
        cost_tier="medium",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["chat", "analysis", "review", "background"],
    ),
    ModelEntry(
        alias="gemini-pro-low",
        provider="antigravity-acp",
        model="gemini-3.1-pro-low",
        display_name="Gemini 3.1 Pro (Low)",
        family="gemini",
        quota_family="agy_gemini",
        cost_tier="medium",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["chat", "analysis"],
        supports_execute=True,
        enabled=True,
    ),
    ModelEntry(
        alias="gemini-pro-high",
        provider="antigravity-acp",
        model="gemini-3.1-pro-high",
        display_name="Gemini 3.1 Pro (High)",
        family="gemini",
        quota_family="agy_gemini",
        cost_tier="high",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["chat", "analysis"],
        supports_execute=True,
        enabled=True,
    ),

    # ── Claude ───────────────────────────────────────────────────────────────

    ModelEntry(
        alias="claude-sonnet",
        provider="antigravity-acp",
        model="claude-sonnet-thinking",
        display_name="Claude Sonnet 4.6 (Thinking)",
        family="claude",
        quota_family="agy_claude",
        cost_tier="high",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["chat", "analysis", "review", "code"],
        supports_execute=True,
        enabled=True,
    ),
    ModelEntry(
        alias="claude-opus",
        provider="antigravity-acp",
        model="claude-opus-thinking",
        display_name="Claude Opus 4.6 (Thinking)",
        family="claude",
        quota_family="agy_claude",
        cost_tier="very_high",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["analysis", "review"],
        supports_execute=True,
        enabled=True,
    ),

    # ── GPT ───────────────────────────────────────────────────────────────────

    ModelEntry(
        alias="gpt-oss",
        provider="antigravity-acp",
        model="gpt-oss-120b-medium",
        display_name="GPT-OSS 120B (Medium)",
        family="gpt",
        quota_family="agy_gpt",
        cost_tier="medium",
        context_size="128K",
        context_limit_tokens=128_000,
        task_tags=["chat", "analysis"],
        supports_execute=True,
        enabled=True,
    ),

    # ── Codex Plus ───────────────────────────────────────────────────────────

    ModelEntry(
        alias="codex-plus-5.5",
        provider="openai-codex",
        model="gpt-5.5",
        display_name="GPT-5.5",
        family="codex_plus",
        quota_family="codex_plus",
        cost_tier="high",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-plus",
        codex_account="plus",
        task_tags=["chat", "code", "analysis"],
    ),
    ModelEntry(
        alias="codex-plus-5.4",
        provider="openai-codex",
        model="gpt-5.4",
        display_name="GPT-5.4",
        family="codex_plus",
        quota_family="codex_plus",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-plus",
        codex_account="plus",
        task_tags=["chat", "code", "summary", "cron"],
    ),
    ModelEntry(
        alias="codex-plus-5.4-mini",
        provider="openai-codex",
        model="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        family="codex_plus",
        quota_family="codex_plus",
        cost_tier="low",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-plus",
        codex_account="plus",
        task_tags=["light", "chat", "summary", "cron"],
    ),
    ModelEntry(
        alias="codex-plus-5.3-code",
        provider="openai-codex",
        model="gpt-5.3-codex",
        display_name="GPT-5.3 Codex",
        family="codex_plus",
        quota_family="codex_plus",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-plus",
        codex_account="plus",
        task_tags=["code", "chat"],
    ),
    ModelEntry(
        alias="codex-plus-5.2",
        provider="openai-codex",
        model="gpt-5.2",
        display_name="GPT-5.2",
        family="codex_plus",
        quota_family="codex_plus",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-plus",
        codex_account="plus",
        task_tags=["chat", "summary"],
    ),

    # ── Codex Business ───────────────────────────────────────────────────────

    ModelEntry(
        alias="codex-business-5.5",
        provider="openai-codex",
        model="gpt-5.5",
        display_name="GPT-5.5",
        family="codex_business",
        quota_family="codex_business",
        cost_tier="high",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-business",
        codex_account="business",
        task_tags=["chat", "code", "analysis"],
    ),
    ModelEntry(
        alias="codex-business-5.4",
        provider="openai-codex",
        model="gpt-5.4",
        display_name="GPT-5.4",
        family="codex_business",
        quota_family="codex_business",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-business",
        codex_account="business",
        task_tags=["chat", "code", "summary", "cron"],
    ),
    ModelEntry(
        alias="codex-business-5.4-mini",
        provider="openai-codex",
        model="gpt-5.4-mini",
        display_name="GPT-5.4 Mini",
        family="codex_business",
        quota_family="codex_business",
        cost_tier="low",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-business",
        codex_account="business",
        task_tags=["light", "chat", "summary", "cron"],
    ),
    ModelEntry(
        alias="codex-business-5.3-code",
        provider="openai-codex",
        model="gpt-5.3-codex",
        display_name="GPT-5.3 Codex",
        family="codex_business",
        quota_family="codex_business",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-business",
        codex_account="business",
        task_tags=["code", "chat"],
    ),
    ModelEntry(
        alias="codex-business-5.2",
        provider="openai-codex",
        model="gpt-5.2",
        display_name="GPT-5.2",
        family="codex_business",
        quota_family="codex_business",
        cost_tier="medium",
        context_size="272K",
        context_limit_tokens=272_000,
        codex_home="/home/lighthouse/.codex-business",
        codex_account="business",
        task_tags=["chat", "summary"],
    ),
    # ── Google AI Studio Free ────────────────────────────────────────────────
    ModelEntry(
        alias="gemini-fallback",
        provider="gemini",
        model="gemini-2.0-flash",
        display_name="Gemini 2.0 Flash (AI Studio)",
        family="fallback_free",
        quota_family="gemini",
        cost_tier="low",
        priority=1,
        role="fallback_only",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["light", "chat", "summary", "cron", "agent_fallback"],
    ),
    ModelEntry(
        alias="gemini-pro-fallback",
        provider="gemini",
        model="gemini-2.0-pro",
        display_name="Gemini 2.0 Pro (AI Studio)",
        family="fallback_free",
        quota_family="gemini",
        cost_tier="low",
        priority=2,
        role="fallback_only",
        context_size="2M",
        context_limit_tokens=2_000_000,
        task_tags=["chat", "analysis", "review", "code", "agent_fallback"],
    ),

    # ── NVIDIA NIM Free ──────────────────────────────────────────────────────
    ModelEntry(
        alias="nv-fallback",
        provider="nvidia_nim",
        model="deepseek-ai/deepseek-v4-flash",
        display_name="DeepSeek V4 Flash (NVIDIA)",
        family="fallback_free",
        quota_family="nvidia_nim",
        cost_tier="low",
        priority=3,
        role="fallback_only",
        context_size="1M",
        context_limit_tokens=1_000_000,
        task_tags=["coding_light", "agent_fallback", "cron_summary", "telegram_reply"],
    ),
    ModelEntry(
        alias="nv-kimi",
        provider="nvidia_nim",
        model="moonshotai/kimi-k2.6",
        display_name="Kimi K2.6 (NVIDIA)",
        family="fallback_free",
        quota_family="nvidia_nim",
        cost_tier="low",
        priority=4,
        role="fallback_only",
        context_size="256K",
        context_limit_tokens=256_000,
        task_tags=["chinese_long_context", "agent_fallback", "summary"],
    ),
    ModelEntry(
        alias="nv-nemotron",
        provider="nvidia_nim",
        model="nvidia/llama-3.1-nemotron-nano-8b-v1",
        display_name="Nemotron Nano 8B (NVIDIA)",
        family="fallback_free",
        quota_family="nvidia_nim",
        cost_tier="low",
        priority=5,
        role="fallback_only",
        context_size="128K",
        context_limit_tokens=128_000,
        task_tags=["conservative_fallback", "simple_chat", "healthcheck_backup"],
    ),
]


# ─── Legacy model string → alias mapping ─────────────────────────────────────
# Allows existing config.yaml model names to be resolved to registry entries.

_LEGACY_MODEL_MAP: dict[tuple[str, str], str] = {
    ("volcengine-agent-plan", "ark-code-latest"): "volcengine-agent-ark-code-latest",
    ("volcengine-agent-plan", "doubao-seed-2.0-pro"): "volcengine-agent-doubao-pro",
    ("volcengine-agent-plan", "doubao-seed-2.0-lite"): "volcengine-agent-doubao-lite",
    ("volcengine-agent-plan", "kimi-k2.6"): "volcengine-agent-kimi-k2-6",
    ("volcengine-coding-plan", "ark-code-latest"): "volcengine-coding-ark-code-latest",
    ("volcengine-coding-plan", "doubao-seed-2.0-code"): "volcengine-coding-doubao-code",
    ("volcengine-coding-plan", "kimi-k2.6"): "volcengine-coding-kimi-k2-6",
    ("volcengine-coding-plan", "deepseek-v3.2"): "volcengine-coding-deepseek-v3-2",
    ("volcengine-coding-plan", "volcengine-ark-code-latest"): "volcengine-coding-ark-code-latest",
    ("volcengine-coding-plan", "volcengine-doubao-code-latest"): "volcengine-coding-doubao-code",
    ("custom", "ark-code-latest"): "volcengine-agent-ark-code-latest",
    # (provider, model_string) -> alias
    ("antigravity-acp", "3.5-flash(high)"): "gemini-high",
    ("antigravity-acp", "3.5-flash(low)"): "gemini-low",
    ("antigravity-acp", "3.5-flash(medium)"): "gemini-medium",
    ("antigravity-acp", "3.1-pro(low)"): "gemini-pro-low",
    ("antigravity-acp", "3.1-pro(high)"): "gemini-pro-high",
    ("antigravity-acp", "gemini-3.1-pro-low"): "gemini-pro-low",
    ("antigravity-acp", "gemini-3.1-pro-high"): "gemini-pro-high",
    ("antigravity-acp", "models/gemini-flash-latest"): "gemini-high",
    ("antigravity-acp", "3-flash"): "gemini-high",
    ("antigravity-acp", "models/gemini-flash-lite-latest"): "gemini-low",
    ("antigravity-acp", "claude-sonnet-thinking"): "claude-sonnet",
    ("antigravity-acp", "claude-opus-thinking"): "claude-opus",
    ("antigravity-acp", "gpt-oss-120b-medium"): "gpt-oss",
    # Direct alias strings users might type in /model or config.yaml
    ("antigravity-acp", "claude-sonnet"): "claude-sonnet",
    ("antigravity-acp", "claude-opus"): "claude-opus",
    ("antigravity-acp", "gpt-oss"): "gpt-oss",
    ("antigravity-acp", "claude-sonnet-4-6"): "claude-sonnet",
    ("antigravity-acp", "claude-opus-4-6"): "claude-opus",
    # Legacy codex aliases map to plus by default
    ("openai-codex", "gpt-5.5"): "codex-plus-5.5",
    ("openai-codex", "gpt-5.4"): "codex-plus-5.4",
    ("openai-codex", "gpt-5.4-mini"): "codex-plus-5.4-mini",
    ("openai-codex", "gpt-5.3-codex"): "codex-plus-5.3-code",
    ("openai-codex", "gpt-5.2"): "codex-plus-5.2",
    # Old alias strings from previous registry version
    ("openai-codex", "codex-5.5"): "codex-plus-5.5",
    ("openai-codex", "codex-5.4"): "codex-plus-5.4",
    ("openai-codex", "codex-5.4-mini"): "codex-plus-5.4-mini",
    ("openai-codex", "codex-5.3-code"): "codex-plus-5.3-code",
    ("openai-codex", "codex-5.2"): "codex-plus-5.2",
}

# Display name → alias (for quota mapping)
_DISPLAY_NAME_MAP: dict[str, str] = {e.display_name: e.alias for e in _REGISTRY}


# ─── Public API ───────────────────────────────────────────────────────────────

def list_models() -> list[ModelEntry]:
    """Return full registry in display order."""
    return list(_REGISTRY)


def get_model_by_alias(alias: str) -> Optional[ModelEntry]:
    """Return the ModelEntry for a given alias, or None."""
    for entry in _REGISTRY:
        if entry.alias == alias:
            return entry
    return None


def get_model_by_provider_model(provider: str, model: str) -> Optional[ModelEntry]:
    """Return ModelEntry for a (provider, model) pair.  Falls back to legacy map."""
    for entry in _REGISTRY:
        if entry.provider == provider and entry.model == model:
            return entry
    # Try legacy map
    alias = _LEGACY_MODEL_MAP.get((provider, model))
    if alias:
        return get_model_by_alias(alias)
    return None


def get_model_by_display_name(display_name: str) -> Optional[ModelEntry]:
    """Return ModelEntry matching the display_name used in quota.json."""
    alias = _DISPLAY_NAME_MAP.get(display_name)
    if alias:
        return get_model_by_alias(alias)
    return None


def get_enabled_models() -> list[ModelEntry]:
    """Return only models with enabled=True."""
    return [e for e in _REGISTRY if e.enabled]


def get_executable_models() -> list[ModelEntry]:
    """Return models that are enabled AND supports_execute=True."""
    return [e for e in _REGISTRY if e.enabled and e.supports_execute]


def family_order() -> list[str]:
    return ["codex_business", "codex_plus", "volcengine", "gemini", "claude", "gpt", "fallback_free"]


def route_group_for_family(family: str) -> str:
    """Map registry families to top-level routing groups."""
    if family in {"codex_business", "codex_plus"}:
        return "codex"
    if family == "volcengine":
        return "volcengine"
    if family == "gpt":
        return "gpt"
    if family == "fallback_free":
        return "fallback_free"
    return family


def route_group_rank(family: str) -> int:
    """Lower rank means higher routing priority."""
    group = route_group_for_family(family)
    try:
        return ROUTE_GROUP_ORDER.index(group)
    except ValueError:
        return len(ROUTE_GROUP_ORDER)


def models_by_family() -> dict[str, list[ModelEntry]]:
    """Return registry grouped by family, in canonical family order."""
    result: dict[str, list[ModelEntry]] = {}
    for fam in family_order():
        result[fam] = [e for e in _REGISTRY if e.family == fam]
    return result


def resolve_alias_from_config_model(provider: str, model: str) -> Optional[str]:
    """Given a legacy (provider, model) from config.yaml, return best alias."""
    entry = get_model_by_provider_model(provider, model)
    return entry.alias if entry else None
