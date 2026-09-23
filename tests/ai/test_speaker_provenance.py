import json

import httpx
import pytest
from openai import AsyncOpenAI
from rio_bot.config import Settings
from rio_bot.llm import LLM
from rio_bot.routing import Scope
from rio_bot.store import Store

from rio_bot.ai.factual_challenge import SPEAKER_ATTRIBUTION_CORRECTION_POLICY
from rio_bot.ai.request_assembly import RequestAssembler


def _response():
    return {
        "id": "resp_test", "object": "response", "created_at": 0,
        "status": "completed", "model": "gpt-4.1-mini",
        "output": [{"type": "message", "id": "msg_test", "role": "assistant",
                    "status": "completed", "content": [
                        {"type": "output_text", "text": "확인했어.", "annotations": []}
                    ]}],
    }


@pytest.mark.asyncio
async def test_recent_speaker_question_uses_discord_provenance_over_memory():
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
    scope = Scope(1, 10, 200)
    try:
        store.save_summary(scope, "B가 A의 Docker 질문을 했다는 잘못된 요약", 0)
        rows = [
            {"message_id": "101", "author_user_id": "100", "name": "A",
             "content": "Docker가 WSL2 위에서 실행되면 일반 VM보다 성능상 유리한가?",
             "role": "user", "channel_id": "10", "unix_time": 1_700_000_000,
             "direct_trigger": True},
            {"message_id": "102", "author_user_id": "200", "name": "B",
             "content": "CT와 VM의 CPU-bound 차이는 체감되는가?", "role": "user",
             "channel_id": "10", "unix_time": 1_700_000_001, "direct_trigger": True},
            {"message_id": "103", "author_user_id": "200", "name": "B",
             "content": "virtio를 쓰면 I/O 차이는 어떻게 달라지는가?", "role": "user",
             "channel_id": "10", "unix_time": 1_700_000_002, "direct_trigger": True},
        ]
        await llm.answer(
            store, scope, "B", "A는 아까 무엇을 물었어?", channel_context=rows,
            message_id="104",
        )

        payload = calls[-1]
        reference = json.loads(payload["input"][0]["content"].split("\n", 1)[1])
        assert reference["conversation_memory"] == ""
        assert reference["personal_recent_conversation"] == []
        turns = reference["channel_recent_messages"]
        assert [(turn["author_id"], turn["content"]) for turn in turns] == [
            ("100", "Docker가 WSL2 위에서 실행되면 일반 VM보다 성능상 유리한가?"),
            ("200", "CT와 VM의 CPU-bound 차이는 체감되는가?"),
            ("200", "virtio를 쓰면 I/O 차이는 어떻게 달라지는가?"),
        ]
        assert turns[0]["speaker_type"] == "participant"
        assert all(turn["channel_id"] == "10" and turn["timestamp"] for turn in turns)
        assert reference["current_user_message"]["speaker_type"] == "current_user"
        assert reference["current_user_message"]["message_id"] == "104"
    finally:
        await llm.close()
        store.close()


@pytest.mark.asyncio
async def test_speaker_correction_adds_apology_withdrawal_policy():
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
        await llm.answer(store, Scope(1, 10, 200), "B", "그건 내가 물은 거야.")
        assert SPEAKER_ATTRIBUTION_CORRECTION_POLICY.strip() in calls[-1]["instructions"]
        assert "기술적 답변" in calls[-1]["instructions"]
        assert "실행·조회 사실" in calls[-1]["instructions"]
    finally:
        await llm.close()
        store.close()


def test_context_selection_telemetry_is_content_and_identity_free():
    input_rows = [
        {"message_id": "secret-message-id", "author_user_id": "secret-author-id",
         "name": "secret-name", "content": "secret Discord content", "role": "user"},
        {"message_id": "another-id", "author_user_id": "another-author",
         "name": "another-name", "content": "assistant context", "role": "assistant"},
    ]
    selected_rows = [
        {**input_rows[0], "speaker_type": "participant", "context_kind": "channel_ambient"},
    ]

    telemetry = RequestAssembler._channel_context_telemetry(
        input_rows, selected_rows, budget_chars=6000, recent_speaker_query=True,
    )

    assert telemetry == {
        "channel_context_input_turns": 2,
        "channel_context_selected_turns": 1,
        "channel_context_filtered_turns": 1,
        "channel_context_budget_chars": 6000,
        "channel_context_selected_chars": len("secret Discord content"),
        "channel_context_selection_reasons": ["channel_ambient"],
        "channel_context_speaker_types": {"participant": 1},
        "recent_speaker_provenance_priority": True,
    }
    assert "secret" not in str(telemetry)
