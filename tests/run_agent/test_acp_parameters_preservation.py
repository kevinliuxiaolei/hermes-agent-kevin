"""Tests that ACP client parameters (command, args, cwd) are preserved during initialization and fallback."""

from unittest.mock import MagicMock, patch
from run_agent import AIAgent

@patch("agent.auxiliary_client.resolve_provider_client")
@patch("run_agent.OpenAI")
def test_acp_params_preserved_on_init(mock_openai, mock_resolve):
    mock_openai.return_value = MagicMock()
    
    # Create a mock client representing a routed ACP client
    mock_routed_client = MagicMock()
    mock_routed_client.api_key = "test-acp-key"
    mock_routed_client.base_url = "acp://copilot"
    mock_routed_client._acp_command = "/path/to/my-custom-acp"
    mock_routed_client._acp_args = ["--foo", "--bar"]
    mock_routed_client._acp_cwd = "/my/working/dir"
    
    # Mock resolve_provider_client to return our routed client
    mock_resolve.return_value = (mock_routed_client, "models/gemini-flash-latest")
    
    agent = AIAgent(
        provider="antigravity-acp",
        model="models/gemini-flash-latest",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    
    # Verify client kwargs preserved the command/args/cwd properties
    assert agent._client_kwargs["command"] == "/path/to/my-custom-acp"
    assert agent._client_kwargs["args"] == ["--foo", "--bar"]
    assert agent._client_kwargs["acp_cwd"] == "/my/working/dir"


@patch("agent.auxiliary_client.resolve_provider_client")
@patch("run_agent.OpenAI")
def test_acp_params_preserved_on_fallback(mock_openai, mock_resolve):
    mock_openai.return_value = MagicMock()
    
    # Start with a primary agent (e.g. openrouter)
    agent = AIAgent(
        provider="openrouter",
        model="google/gemini-flash",
        api_key="sk-primary",
        base_url="https://openrouter.ai/api/v1",
        quiet_mode=True,
        skip_context_files=True,
        skip_memory=True,
    )
    
    # Setup fallback chain to use antigravity-acp
    agent._fallback_activated = False
    agent._fallback_model = {
        "provider": "antigravity-acp",
        "model": "models/gemini-flash-latest",
    }
    agent._fallback_chain = [agent._fallback_model]
    agent._fallback_index = 0
    
    # Create fallback client representing ACP
    mock_fb_client = MagicMock()
    mock_fb_client.api_key = "test-fallback-key"
    mock_fb_client.base_url = "acp://copilot"
    mock_fb_client._acp_command = "/path/to/fallback-acp"
    mock_fb_client._acp_args = ["--fallback-arg"]
    mock_fb_client._acp_cwd = "/fallback/cwd"
    
    mock_resolve.return_value = (mock_fb_client, "models/gemini-flash-latest")
    agent._emit_status = lambda msg: None
    
    result = agent._try_activate_fallback()
    
    assert result is True
    assert agent._fallback_activated is True
    assert agent._client_kwargs["command"] == "/path/to/fallback-acp"
    assert agent._client_kwargs["args"] == ["--fallback-arg"]
    assert agent._client_kwargs["acp_cwd"] == "/fallback/cwd"


def test_copilot_acp_client_streaming_support():
    from agent.copilot_acp_client import CopilotACPClient
    client = CopilotACPClient(
        command="echo",
        args=["hello"],
    )
    # Mock _run_prompt to bypass subprocess execution
    client._run_prompt = MagicMock(return_value=(
        "<tool_call>{\"id\": \"call_123\", \"type\": \"function\", \"function\": {\"name\": \"read_file\", \"arguments\": \"{}\"}}</tool_call>\nhello there",
        "reasoning steps"
    ))
    
    stream = client._create_chat_completion(
        model="models/gemini-flash-latest",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    
    assert hasattr(stream, "response")
    chunks = list(stream)
    assert len(chunks) > 0
    
    # 1st chunk should be reasoning
    assert chunks[0].choices[0].delta.reasoning == "reasoning steps"
    
    # 2nd chunk should be tool call
    tool_call = chunks[1].choices[0].delta.tool_calls[0]
    assert tool_call.id == "call_123"
    assert tool_call.function.name == "read_file"
    
    # Final chunk should have finish reason
    assert chunks[-1].choices[0].finish_reason == "tool_calls"
