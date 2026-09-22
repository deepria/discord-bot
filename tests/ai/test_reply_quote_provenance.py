import json

import httpx
import pytest
from openai import AsyncOpenAI
from rio_bot.chat_llm import LLM
from rio_bot.config import Settings
from rio_bot.routing import Scope
from rio_bot.store import Store


def _response():
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": "completed",
        "model": "gpt-4.1-mini",
        "output": [{
            "type": "message",
            "id": "msg_test",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "응", "annotations": []}],
        }],
    }


@pytest.mark.asyncio
async def test_inline_quote_is_labeled_separately_from_the_current_speaker():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test"), client=client)
    store = Store(":memory:")
    try:
        await llm.answer(
            store,
            Scope(1, 10, 100),
            "사용자",
            "이 주장은 맞아?",
            quoted_text="다른 사람이 했다는 주장",
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["current_user_message"] == {
            "author_user_id": "100",
            "content": "이 주장은 맞아?",
            "context_kind": "current_message",
        }
        assert reference["inline_quoted_text"][0]["author_user_id"] is None
        assert reference["inline_quoted_text"][0]["content"] == "다른 사람이 했다는 주장"
        assert payload["input"][-1]["content"] == "이 주장은 맞아?"
        assert "현재 발화와 인용 출처" in payload["instructions"]
    finally:
        await llm.close()
        store.close()
