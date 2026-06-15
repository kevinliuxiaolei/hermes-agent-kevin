"""Tests for title-generation auxiliary routing through the unified model selector."""

from types import SimpleNamespace
from unittest.mock import MagicMock


class _FakeClient:
    def __init__(self, label, *, fail=False, content="Generated Title"):
        self.label = label
        self.fail = fail
        self.content = content
        self.base_url = f"https://{label}.example/v1"
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            exc = RuntimeError(f"{self.label} HTTP 503 UNAVAILABLE")
            setattr(exc, "status_code", 503)
            raise exc
        msg = SimpleNamespace(content=self.content)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


def test_title_generation_auto_uses_model_selector_chat_route(monkeypatch):
    import agent.auxiliary_client as aux

    aux._client_cache.clear()

    selected = SimpleNamespace(
        provider="volcengine-agent-plan",
        model="ark-code-latest",
        alias="volcengine-agent-ark-code-latest",
        codex_home=None,
    )
    selector_calls = []

    def fake_select_model_with_reason(task="chat", preferred_alias=None, refresh_quota=False):
        selector_calls.append((task, preferred_alias, refresh_quota))
        return SimpleNamespace(entry=selected, reason="ok", skipped=[])

    client = _FakeClient("volcengine")
    resolve_calls = []

    def fake_resolve_provider_client(provider, model=None, async_mode=False, **kwargs):
        resolve_calls.append((provider, model, async_mode, kwargs))
        return client, model

    monkeypatch.setattr(aux, "_get_auxiliary_task_config", lambda task: {"provider": "auto", "model": ""})
    monkeypatch.setattr(aux, "_get_task_extra_body", lambda task: {})
    monkeypatch.setattr(aux, "_get_task_timeout", lambda task: 30.0)
    monkeypatch.setattr("agent.model_selector.select_model_with_reason", fake_select_model_with_reason)
    monkeypatch.setattr(aux, "resolve_provider_client", fake_resolve_provider_client)

    resp = aux.call_llm(
        task="title_generation",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=50,
    )

    assert resp.choices[0].message.content == "Generated Title"
    assert selector_calls == [("chat", None, False)]
    assert resolve_calls[0][0] == "volcengine-agent-plan"
    assert resolve_calls[0][1] == "ark-code-latest"


def test_title_generation_auto_falls_back_to_next_system_route_on_503(monkeypatch):
    import agent.auxiliary_client as aux

    aux._client_cache.clear()

    first = SimpleNamespace(
        provider="gemini",
        model="gemini-flash-latest",
        alias="gemini-low",
        codex_home=None,
    )
    second = SimpleNamespace(
        provider="volcengine-agent-plan",
        model="ark-code-latest",
        alias="volcengine-agent-ark-code-latest",
        codex_home=None,
    )

    monkeypatch.setattr(aux, "_get_auxiliary_task_config", lambda task: {"provider": "auto", "model": ""})
    monkeypatch.setattr(aux, "_get_task_extra_body", lambda task: {})
    monkeypatch.setattr(aux, "_get_task_timeout", lambda task: 30.0)
    monkeypatch.setattr("agent.model_selector.build_fallback_chain", lambda task, max_candidates=4, preferred_alias=None: [first, second])
    monkeypatch.setattr(
        "agent.model_selector.select_model_with_reason",
        lambda task="chat", preferred_alias=None, refresh_quota=False: SimpleNamespace(entry=first, reason="ok", skipped=[]),
    )

    clients = {
        "gemini": _FakeClient("gemini", fail=True),
        "volcengine-agent-plan": _FakeClient("volcengine", content="Fallback Title"),
    }
    resolve_calls = []

    def fake_resolve_provider_client(provider, model=None, async_mode=False, **kwargs):
        resolve_calls.append((provider, model))
        return clients[provider], model

    monkeypatch.setattr(aux, "resolve_provider_client", fake_resolve_provider_client)
    mark_unhealthy = MagicMock()
    monkeypatch.setattr(aux, "_mark_provider_unhealthy", mark_unhealthy)

    resp = aux.call_llm(
        task="title_generation",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=50,
    )

    assert resp.choices[0].message.content == "Fallback Title"
    assert resolve_calls == [
        ("gemini", "gemini-flash-latest"),
        ("volcengine-agent-plan", "ark-code-latest"),
    ]
    mark_unhealthy.assert_called_once_with("gemini")


def test_compression_explicit_gemini_falls_back_to_paid_registry_route_on_503(monkeypatch):
    import agent.auxiliary_client as aux

    aux._client_cache.clear()
    paid = SimpleNamespace(
        provider="volcengine-coding-plan",
        model="ark-code-latest",
        alias="volcengine-coding-ark-code-latest",
        codex_home=None,
    )

    monkeypatch.setattr(
        aux,
        "_get_auxiliary_task_config",
        lambda task: {"provider": "gemini", "model": "gemini-flash-latest"},
    )
    monkeypatch.setattr(aux, "_get_task_extra_body", lambda task: {})
    monkeypatch.setattr(aux, "_get_task_timeout", lambda task: 30.0)
    monkeypatch.setattr(
        "agent.model_selector.build_fallback_chain",
        lambda task, max_candidates=4, preferred_alias=None: [paid],
    )

    clients = {
        "gemini": _FakeClient("gemini", fail=True),
        "volcengine-coding-plan": _FakeClient("volcengine", content="Compression Summary"),
    }
    resolve_calls = []

    def fake_resolve_provider_client(provider, model=None, async_mode=False, **kwargs):
        resolve_calls.append((provider, model))
        return clients[provider], model

    monkeypatch.setattr(aux, "resolve_provider_client", fake_resolve_provider_client)
    monkeypatch.setattr(aux, "_mark_provider_unhealthy", MagicMock())

    resp = aux.call_llm(
        task="compression",
        messages=[{"role": "user", "content": "summarize"}],
        max_tokens=100,
    )

    assert resp.choices[0].message.content == "Compression Summary"
    assert resolve_calls == [
        ("gemini", "gemini-flash-latest"),
        ("volcengine-coding-plan", "ark-code-latest"),
    ]
