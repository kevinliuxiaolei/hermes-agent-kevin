"""Antigravity ACP provider backed by a user-configured bridge process."""

from providers import register_provider
from providers.base import ProviderProfile


class AntigravityACPProfile(ProviderProfile):
    """AGY bridge metadata; credentials and paths stay in HERMES_HOME/env."""

    def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
        return None

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
        "gemini-3.6-flash-high",
        "gemini-3.6-flash-medium",
        "gemini-3.6-flash-low",
        "gemini-3.5-flash-high",
        "gemini-3.5-flash-medium",
        "gemini-3.5-flash-low",
        "gemini-3.5-flash-lite",
        "gemini-3.1-pro-high",
        "gemini-3.1-pro-low",
        "gemini-3-flash",
        "claude-sonnet-4-6",
        "claude-opus-4-6-thinking",
        "gpt-oss-120b-medium",
    ),
)

register_provider(antigravity_acp)
