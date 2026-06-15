import json


def test_route_health_failure_blocks_until_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(tmp_path / "health.json"))

    from agent.route_health import record_route_failure, route_health_allows

    record_route_failure("provider-a", reason="rate_limit", status_code=429, message="limited")

    allowed, reason = route_health_allows("provider-a")
    assert allowed is False
    assert reason == "rate_limit"


def test_route_health_success_clears_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(tmp_path / "health.json"))

    from agent.route_health import record_route_failure, record_route_success, route_health_allows

    record_route_failure("provider-a", reason="server_error", status_code=503)
    record_route_success("provider-a")

    assert route_health_allows("provider-a") == (True, None)


def test_route_health_auth_failure_stays_blocked_after_cooldown(tmp_path, monkeypatch):
    state_file = tmp_path / "health.json"
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(state_file))

    from agent.route_health import record_route_failure, route_health_allows

    record_route_failure("provider-a", reason="auth", status_code=401)
    data = json.loads(state_file.read_text())
    data["provider-a"]["cooldown_until"] = 1
    state_file.write_text(json.dumps(data))

    assert route_health_allows("provider-a") == (False, "auth")


def test_route_health_auth_failure_recovers_after_explicit_success(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(tmp_path / "health.json"))

    from agent.route_health import record_route_failure, record_route_success, route_health_allows

    record_route_failure("provider-a", reason="auth", status_code=401)
    record_route_success("provider-a")

    assert route_health_allows("provider-a") == (True, None)


def test_custom_volcengine_route_records_concrete_provider(tmp_path, monkeypatch):
    state_file = tmp_path / "health.json"
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(state_file))

    from agent.route_health import record_route_failure, route_health_allows

    record_route_failure(
        "custom",
        reason="rate_limit",
        status_code=429,
        model="ark-code-latest",
        base_url="https://ark.cn-beijing.volces.com/api/plan/v3",
    )

    assert route_health_allows("volcengine-agent-plan") == (False, "rate_limit")


def test_explicit_clear_recovers_auth_failed_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_ROUTE_HEALTH_STATE_FILE", str(tmp_path / "health.json"))

    from agent.route_health import clear_route_health, record_route_failure, route_health_allows

    record_route_failure("openai-codex", reason="auth", status_code=401)
    clear_route_health("openai-codex")

    assert route_health_allows("openai-codex") == (True, None)
