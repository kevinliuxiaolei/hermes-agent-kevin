"""Volcano Engine (Ark) provider profile."""

from providers import register_provider
from providers.base import ProviderProfile

volcano = ProviderProfile(
    name="volcano",
    aliases=("volcano-ark", "volcengine"),
    env_vars=("VOLCANO_API_KEY", "ARK_API_KEY"),
    display_name="Volcano Engine",
    description="Volcano Engine Ark LLM service by ByteDance",
    signup_url="https://www.volcengine.com/product/ark",
    base_url="https://ark.cn-beijing.volces.com/api/v3",
)

register_provider(volcano)
