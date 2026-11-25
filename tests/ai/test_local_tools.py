import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI

from rio_bot.ai.local_tools import LocalTool, LocalToolAccessChanged, request_with_local_tools
from rio_bot.ai.providers import _GeminiResponses, _OpenRouterResponses
from rio_bot.ai.usage import UsageLogger


def fake_tool():
    return LocalTool(
        {"type": "function", "name": "get_chat_history", "description": "Read history",
         "parameters": {"type": "object", "properties": {}}},
        AsyncMock(return_value={"status": "ok", "messages": ["test evidence"]}),
        AsyncMock(return_value=True),
    )


@pytest.mark.asyncio
async def test_plain_answer_does_not_run_local_tool():
    tool = fake_tool()
    response = NS(output=[], output_text="ordinary answer")
    send = AsyncMock(return_value=response)
    assert await request_with_local_tools(send, {"input": []}, (tool,)) is response
    tool.run.assert_not_awaited()
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_only_registered_functions_execute_and_final_round_is_tool_free():
    tool = fake_tool()

    async def send(**kwargs):
        requests.append(kwargs)
        if len(requests) > 3:
            return NS(output=[], output_text="done")
        name = "invented_function" if len(requests) == 1 else "get_chat_history"
        return NS(output=[{"type": "function_call", "call_id": f"call_{len(requests)}",
                               "name": name, "arguments": "{}"}])

    requests = []
    response = await request_with_local_tools(send, {"input": []}, (tool,))
    assert response.output_text == "done"
    assert tool.run.await_count == 2
    assert "tools" not in requests[-1]
    assert json.loads(requests[-1]["input"][1]["output"]) == {"status": "unknown_tool"}


@pytest.mark.asyncio
async def test_access_change_discards_tool_output_before_provider_egress():
    tool = fake_tool()
    tool.validate.return_value = False
    send = AsyncMock(return_value=NS(output=[
        {"type": "function_call", "call_id": "1", "name": "get_chat_history", "arguments": "{}"},
    ]))
    with pytest.raises(LocalToolAccessChanged):
        await request_with_local_tools(send, {"input": []}, (tool,))
    send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["openai", "openrouter", "gemini"])
async def test_provider_round_trip_uses_stateless_function_results(provider):
    requests = []
    gemini_steps = [
        {"type": "thought", "signature": "signed"},
        {"type": "function_call", "id": "call_1", "name": "get_chat_history", "arguments": {}},
    ]

    async def handle(request):
        requests.append(json.loads(request.content))
        if provider == "gemini":
            # Even a completed, text-free tool response must not trigger the empty-text retry.
            steps = gemini_steps if len(requests) == 1 else [
                {"type": "model_output", "content": [{"type": "text", "text": "done"}]},
            ]
            return httpx.Response(200, json={"status": "completed", "steps": steps})
        if len(requests) == 1:
            output = [{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                       "name": "get_chat_history", "arguments": "{}", "status": "completed"}]
        else:
            output = [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                       "content": [{"type": "output_text", "text": "done", "annotations": []}]}]
        return httpx.Response(200, json={"status": "completed", "output": output})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        if provider == "gemini":
            responses = _GeminiResponses(http)
        else:
            responses = AsyncOpenAI(api_key="test", http_client=http).responses
            if provider == "openrouter":
                responses = _OpenRouterResponses(responses)
        usage = UsageLogger("")
        client = NS(provider_name=provider, responses=responses)

        async def send(**kwargs):
            return await usage.request(client, "answer", **kwargs)

        request = {"model": "test", "input": [{"role": "user", "content": "What did we decide?"}],
                   "store": False}
        response = await request_with_local_tools(send, request, (fake_tool(),))
    assert response.output_text == "done"
    assert len(requests) == 2 and not any(row["store"] for row in requests)
    assert requests[0]["tools"][0]["name"] == "get_chat_history"
    continuation = requests[1]["input"]
    if provider == "gemini":
        assert continuation[1:3] == gemini_steps
        result = continuation[3]
        assert result["name"] == "get_chat_history"
        body = result["result"][0]["text"]
    else:
        assert continuation[1]["type"] == "function_call"
        result = continuation[2]
        body = result["output"]
    assert result["call_id"] == "call_1"
    assert json.loads(body)["messages"] == ["test evidence"]
