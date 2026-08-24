"""Helpers for reading the effective fallback provider chain from config."""

from __future__ import annotations

import json
from typing import Any


def _normalized_base_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().rstrip("/")


def resolve_entry_api_key(entry: dict[str, Any] | None) -> str | None:
    """API key for one fallback entry: inline ``api_key``, else ``key_env``.

    Mirrors the custom-provider convention (``key_env`` names the env var
    holding the key; ``api_key_env`` accepted as an alias). Returns None when
    neither yields a non-empty value, letting ``resolve_runtime_provider``
    fall through to the provider's standard credential resolution.

    ``key_env`` is resolved through ``agent.secret_scope.get_secret`` rather
    than a raw ``os.getenv`` — in a multiplexed gateway a bare env read would
    ignore the active profile's scope and can return another profile's
    credential. ``get_secret`` already implements the right fallback: it
    reads ``os.environ`` when there's no active multiplexed scope (matching
    prior single-profile behavior), and fails closed only when multiplexing
    is active with no scope installed.
    """
    if not isinstance(entry, dict):
        return None
    inline = str(entry.get("api_key") or "").strip()
    if inline:
        return inline
    key_env = str(entry.get("key_env") or entry.get("api_key_env") or "").strip()
    if key_env:
        from agent.secret_scope import get_secret

        return (get_secret(key_env) or "").strip() or None
    return None


def _iter_fallback_entries(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(raw, dict):
        candidates = [raw]
    elif isinstance(raw, list):
        candidates = raw
    else:
        return []

    entries: list[dict[str, Any]] = []
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        provider = str(entry.get("provider") or "").strip()
        model = str(entry.get("model") or "").strip()
        if not provider or not model:
            continue

        normalized = dict(entry)
        normalized["provider"] = provider
        normalized["model"] = model

        base_url = _normalized_base_url(entry.get("base_url"))
        if base_url:
            normalized["base_url"] = base_url

        entries.append(normalized)
    return entries


def _entry_identity(entry: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(entry.get("provider") or "").strip().lower(),
        str(entry.get("model") or "").strip().lower(),
        _normalized_base_url(entry.get("base_url")).lower(),
    )


def get_fallback_chain(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return the effective fallback chain merged across old and new config keys.

    ``fallback_providers`` remains the primary source of truth and keeps its
    order. Legacy ``fallback_model`` entries are appended afterwards unless
    they target the same provider/model/base_url route as an earlier entry.
    The returned list always contains fresh dict copies.
    """

    config = config or {}
    chain: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for key in ("fallback_providers", "fallback_model"):
        for entry in _iter_fallback_entries(config.get(key)):
            identity = _entry_identity(entry)
            if identity in seen:
                continue
            seen.add(identity)
            chain.append(entry)

    return chain


VOLC_SELECTION_FAMILY = "volc"
VOLC_SELECTION_POLICY = "coding-primary-agent-sibling"


def build_effective_fallback_chain(
    primary: dict[str, Any],
    selection: dict[str, Any] | None,
    configured: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Build a route-aware fallback chain without reading config or secrets."""
    chain: list[dict[str, Any]] = []
    selection = selection or {}
    if (
        selection.get("selection_family") == VOLC_SELECTION_FAMILY
        and selection.get("selection_policy") == VOLC_SELECTION_POLICY
        and str(primary.get("provider") or "").lower() == "volcengine-coding-plan"
        and primary.get("model")
    ):
        base_url = _normalized_base_url(primary.get("base_url"))
        if "/api/coding/" in base_url:
            base_url = base_url.replace("/api/coding/", "/api/plan/", 1)
        sibling: dict[str, Any] = {
            "provider": "volcengine-agent-plan",
            "model": str(primary["model"]),
        }
        if base_url:
            sibling["base_url"] = base_url
        if primary.get("api_mode"):
            sibling["api_mode"] = primary["api_mode"]
        chain.append(sibling)

    chain.extend(dict(entry) for entry in (configured or []) if isinstance(entry, dict))
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    seen_routes: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for entry in chain:
        provider = str(entry.get("provider") or "").strip().lower()
        model = str(entry.get("model") or "").strip().lower()
        base_url = _normalized_base_url(entry.get("base_url")).lower()
        api_mode = str(entry.get("api_mode") or "").strip().lower()
        identity = (provider, model, base_url, api_mode)
        weak_identity = (provider, model)
        prior_routes = seen_routes.get(weak_identity, [])
        overlaps_prior = any(
            (not base_url or not prior_base or base_url == prior_base)
            and (not api_mode or not prior_mode or api_mode == prior_mode)
            for prior_base, prior_mode in prior_routes
        )
        if not provider or not model or identity in seen or overlaps_prior:
            continue
        seen.add(identity)
        seen_routes.setdefault(weak_identity, []).append((base_url, api_mode))
        deduped.append(entry)
    return deduped
