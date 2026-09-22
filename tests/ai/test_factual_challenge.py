import json

import httpx
import pytest
from openai import AsyncOpenAI
from rio_bot.chat_llm import LLM
from rio_bot.config import Settings
from rio_bot.routing import Scope
from rio_bot.store import Store

from rio_bot.ai.factual_challenge import FACTUAL_CHALLENGE_POLICY, is_factual_challenge


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
            "content": [{"type": "output_text", "text": "정정할게", "annotations": []}],
        }],
    }


def test_factual_challenge_classifier_is_narrow_and_explicit():
    assert is_factual_challenge("리오, 그건 사실이 아니야. 틀렸어.")
    assert is_factual_challenge("없는 설정을 지어낸 거 아니야?")
    assert not is_factual_challenge("오늘 기분이 어때?")


@pytest.mark.asyncio
async def test_factual_challenge_adds_correction_first_instruction():
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
        await llm.answer(store, Scope(1, 10, 100), "사용자", "그건 틀렸어. 근거가 없어.")

        assert FACTUAL_CHALLENGE_POLICY.strip() in calls[-1]["instructions"]
    finally:
        await llm.close()
        store.close()
