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
from hina_bot.web_bot import LLM as DiscordLLM


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


def test_discord_runtime_uses_chat_llm():
    assert DiscordLLM is LLM


@pytest.mark.asyncio
async def test_relation_question_requires_search_without_local_evidence(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "나기사 만나본 적 있어?")
        payload = calls[-1]
        assert payload["tools"] == [{"type": "web_search"}]
        assert payload["tool_choice"] == "required"
        assert "세계관 사실 질문의 답변 방식" in payload["instructions"]
        assert "직접 확인되는 대면" in payload["instructions"]
        assert "같은 사건에 관여했다는 사실만으로" in payload["instructions"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_relation_question_still_searches_with_one_local_fact(chat_llm):
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
        assert payload["tool_choice"] == "required"
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["lore_reference"][0]["reference"] == "test.hina.nagisa.meeting"
    finally:
        store.close()


@pytest.mark.asyncio
async def test_simple_fact_with_local_world_fact_keeps_search_optional(chat_llm):
    llm, calls = chat_llm
    llm.lore = LoreIndex([{
        "id": "test.hina.weapon",
        "lane": "canon",
        "fact_type": "fact_direct",
        "summary": "히나의 기관총 이름은 종막의 디스트로이어다.",
        "keywords": ["무기", "총 이름", "종막의 디스트로이어"],
        "subjects": ["히나"],
        "knowledge": "self",
        "timeline": "상시",
    }])
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "총 이름 뭐야?")
        assert calls[-1]["tool_choice"] == "auto"
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
async def test_self_identity_question_is_not_forced_into_fact_search(chat_llm):
    llm, calls = chat_llm
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(None, 20, 100), "사용자", "너 누구야?")
        payload = calls[-1]
        assert payload["tool_choice"] == "auto"
        assert "세계관 사실 질문의 답변 방식" not in payload["instructions"]
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
