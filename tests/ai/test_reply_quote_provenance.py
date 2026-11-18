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
    llm = LLM(Settings("test", "test", bot_admin_ids=frozenset({100})), client=client)
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
        current = reference["current_user_message"]
        assert current["author_user_id"] == "100"
        assert current["author_id"] == "100"
        assert current["author_name"] == "사용자"
        assert current["speaker_type"] == "current_user"
        assert current["channel_id"] == "10"
        assert current["content"] == "이 주장은 맞아?"
        assert current["context_kind"] == "current_message"
        assert current["is_current_turn"] is True
        assert current["relationship"] == "husband_admin"
        assert reference["inline_quoted_text"][0]["author_user_id"] is None
        assert reference["inline_quoted_text"][0]["content"] == "다른 사람이 했다는 주장"
        assert payload["input"][-1]["content"] == "이 주장은 맞아?"
        assert "현재 발화와 인용 출처" in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_relationship_uses_current_author_id_not_name_or_guild_permissions():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test", bot_admin_ids=frozenset({100})), client=client)
    store = Store(":memory:")
    try:
        await llm.answer(store, Scope(1, 10, 200), "deepria", "안녕")

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["current_user_message"]["relationship"] == "participant"
        assert "현재는 일반 관계 모드" in payload["instructions"]
        assert "디프" not in payload["instructions"]
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_narration_is_delivered_in_order_with_dialogue_only_output_policy():
    from rio_bot.core.message_provenance import message_segments

    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json=_response())

    client = AsyncOpenAI(
        api_key="test-not-a-real-key",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    llm = LLM(Settings("test", "test", external_context_policy="full"), client=client)
    store = Store(":memory:")
    scene = message_segments("> 선생은 리오를 내려다보며 웃었다.\n> 리오는 웃기로 했다.")
    try:
        await llm.answer(
            store, Scope(1, 10, 100), "사용자", "", message_segments=scene,
            channel_context=[{
                "role": "user", "user_id": "200", "name": "다른 사람",
                "message_id": "1", "content": "> 문을 열었다.\n안녕",
            }],
        )
        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        current = reference["current_user_message"]
        assert current["content"] == ""
        assert current["message_segments"] == scene
        assert current["author_id"] == "100"
        assert reference["inline_quoted_text"] == []
        prior, = reference["channel_recent_messages"]
        assert prior["author_id"] == "200"
        assert prior["message_segments"] == [
            {"kind": "narration", "content": "문을 열었다."},
            {"kind": "dialogue", "content": "안녕"},
        ]
        assert payload["input"][-1]["content"]
        assert "리오는 대사로만 답하세요" in payload["instructions"]
        assert "속마음은 들은 말처럼 다루지" in payload["instructions"]
    finally:
        await llm.close()
        store.close()
