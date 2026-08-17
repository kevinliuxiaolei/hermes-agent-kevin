"""Antigravity ACP provider backed by a user-configured bridge process."""

import json
from pathlib import Path

from hermes_constants import get_hermes_home
from providers import register_provider
from providers.base import ProviderProfile


_DYNAMIC_ALIASES = {
    "agy-gemini-flash-high-latest": "gemini-3.7-flash-high",
    "agy-gemini-flash-medium-latest": "gemini-3.7-flash-medium",
    "agy-gemini-flash-low-latest": "gemini-3.7-flash-low",
    "agy-gemini-flash-lite-latest": "gemini-3.5-flash-lite",
    "agy-gemini-pro-high-latest": "gemini-3.1-pro-high",
}


class AntigravityACPProfile(ProviderProfile):
    """AGY bridge metadata; credentials and paths stay in HERMES_HOME/env."""

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        """Read the nightly source catalog cache without contacting AGY."""
        path = Path(get_hermes_home()) / "state" / "agy_model_catalog.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            rows = payload.get("models") if isinstance(payload, dict) else None
            models = [
                str(row.get("id") or "").strip()
                for row in (rows or [])
                if isinstance(row, dict) and str(row.get("id") or "").strip()
            ]
            return list(dict.fromkeys(models)) or None
        except Exception:
            return None

    def resolve_runtime_model(
        self,
        requested_model: str,
        *,
        scope_id: str | None = None,
    ) -> str:
        requested = str(requested_model or "").strip()
        fallback = _DYNAMIC_ALIASES.get(requested)
        if not fallback:
            return requested
        path = Path(get_hermes_home()) / "state" / "agy_model_resolution.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = ((payload.get("aliases") or {}).get(requested) or {})
            resolved = str(item.get("current_verified") or "").strip()
            return resolved or fallback
        except Exception:
            return fallback

    def model_context_length(self, model: str) -> int | None:
        return 1_000_000


antigravity_acp = AntigravityACPProfile(
    name="antigravity-acp",
    aliases=("agy", "agy-acp", "antigravity"),
    display_name="AGY Gemini (ACP)",
    description="Antigravity ACP bridge (external process)",
    api_mode="chat_completions",
    base_url="acp://antigravity",
    auth_type="external_process",
    command_env_vars=("HERMES_ANTIGRAVITY_ACP_COMMAND", "ANTIGRAVITY_ACP_COMMAND"),
    args_env_var="HERMES_ANTIGRAVITY_ACP_ARGS",
    base_url_env_var="ANTIGRAVITY_ACP_BASE_URL",
    default_command="agy_acp_bridge.py",
    default_args=(),
    expose_in_picker=True,
    fallback_models=(
        "agy-gemini-flash-high-latest",
        "gemini-3.7-flash-high",
        "gemini-3.7-flash-medium",
        "gemini-3.7-flash-low",
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ),
)

register_provider(antigravity_acp)
