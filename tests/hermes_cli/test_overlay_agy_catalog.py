from __future__ import annotations


def test_external_process_catalog_merges_cached_models_curated_first(monkeypatch):
    import hermes_cli.models as models

    class CachedExternalProfile:
        auth_type = "external_process"
        fallback_models = (
            "agy-gemini-flash-high-latest",
            "claude-sonnet-4-6",
        )

        def fetch_models(self, *, api_key=None, base_url=None, timeout=8.0):
            assert api_key is None
            assert base_url is None
            return ["gemini-3.7-flash-high", "claude-sonnet-4-6"]

    monkeypatch.setattr(
        "providers.get_provider_profile",
        lambda provider: CachedExternalProfile() if provider == "antigravity-acp" else None,
    )
    result = models.provider_model_ids("antigravity-acp")
    curated = models._PROVIDER_MODELS["antigravity-acp"]
    assert result[: len(curated)] == curated
    assert result.count("agy-gemini-flash-high-latest") == 1
    assert result.count("gemini-3.7-flash-high") == 1
    assert "gpt-oss-120b-medium" in result


def test_external_process_catalog_keeps_latest_alias_when_cache_is_empty(monkeypatch):
    import hermes_cli.models as models

    class EmptyCachedExternalProfile:
        auth_type = "external_process"
        fallback_models = ()

        def fetch_models(self, **kwargs):
            return None

    monkeypatch.setattr(
        "providers.get_provider_profile",
        lambda provider: EmptyCachedExternalProfile() if provider == "antigravity-acp" else None,
    )
    result = models.provider_model_ids("antigravity-acp")
    assert result[:5] == [
        "agy-gemini-flash-high-latest",
        "agy-gemini-flash-medium-latest",
        "agy-gemini-flash-low-latest",
        "agy-gemini-flash-lite-latest",
        "agy-gemini-pro-high-latest",
    ]
    assert "gemini-3.7-flash-high" not in result
    assert "gpt-oss-120b-medium" in result
