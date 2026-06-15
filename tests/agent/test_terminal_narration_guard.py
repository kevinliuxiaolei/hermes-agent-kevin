from agent.agent_runtime_helpers import looks_like_codex_intermediate_ack


class _Agent:
    @staticmethod
    def _strip_think_blocks(text):
        return text


def _is_narration(text, user="Please improve the model list in this codebase."):
    return looks_like_codex_intermediate_ack(_Agent(), user, text, [])


def test_detects_provider_independent_process_narration():
    assert _is_narration(
        "I will update agent/model_registry.py and modify the model list rendering."
    )
    assert _is_narration(
        "I will ask for permission to read files, then perform a targeted search."
    )


def test_does_not_reject_completed_result():
    assert not _is_narration(
        "Updated agent/model_registry.py. The model list now shows context sizes."
    )


def test_tool_result_history_disables_narration_guard():
    messages = [{"role": "tool", "content": "done", "tool_call_id": "1"}]
    assert not looks_like_codex_intermediate_ack(
        _Agent(),
        "Improve this codebase.",
        "I will update the relevant files.",
        messages,
    )


def test_old_tool_history_does_not_disable_current_turn_guard():
    messages = [
        {"role": "tool", "content": "old result", "tool_call_id": "1"},
        {"role": "user", "content": "Improve the model list."},
    ]
    assert looks_like_codex_intermediate_ack(
        _Agent(),
        "Improve the model list.",
        "I will update the relevant files.",
        messages,
    )
