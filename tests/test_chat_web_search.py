import json
from dataclasses import replace

import httpx
import pytest
from openai import AsyncOpenAI

from hina_bot.chat_llm import LLM
from hina_bot.config import Settings
from hina_bot.lore import LoreIndex
from hina_bot.routing import Scope
from hina_bot.store import Store


@pytest.fixture
async def chat_llm():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "resp_test",
            "object": "response",
            "created_at": 0,
            "status": "completed",
            "model": "test-model",
            "output": [{
                "type": "message",
                "id": "msg_test",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "응.", "annotations": []}],
            }],
        })

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test", model="test-model", summary_every=2,
                       usage_log_path="", chat_web_search=True), client=client)
    llm.lore = LoreIndex([])
    yield llm, calls
    await llm.close()


@pytest.mark.asyncio
async def test_world_fact_without_local_evidence_requires_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert payload["tools"] == [{"type": "web_search"}]
        assert payload["tool_choice"] == "required"
        assert "세계관 사실 질문의 답변 방식" in payload["instructions"]
        assert "실제로 어느 사건이나" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_strong_local_world_fact_keeps_search_optional(chat_llm):
    llm, calls = chat_llm
    llm.lore = LoreIndex([{
        "id": "test.hina.nagisa.meeting",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "히나는 나기사와 특정 사건에서 직접 만난 적이 있다.",
        "keywords": ["나기사", "만남"],
        "subjects": ["히나", "나기사"],
        "knowledge": "direct_experience",
        "timeline": "테스트 사건",
    }])
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert payload["tool_choice"] == "auto"
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["lore_reference"][0]["reference"] == "test.hina.nagisa.meeting"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_current_release_question_requires_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "한섭에 지금 어디까지 공개됐어?")
        assert calls[-1]["tool_choice"] == "required"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_web_search_can_be_disabled_in_settings(chat_llm):
    llm, calls = chat_llm
    llm.settings = replace(llm.settings, chat_web_search=False)
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert "tools" not in payload
        assert "tool_choice" not in payload
        assert "현재 응답의 웹 검색" not in payload["instructions"]
    finally:
        store.close()
