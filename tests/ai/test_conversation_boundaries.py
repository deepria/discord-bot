import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store


@pytest.fixture
async def rio():
    model = AsyncMock(return_value=NS(status="completed", output_text="응.", output=[]))
    provider = NS(responses=NS(create=model), close=AsyncMock())
    llm = LLM(Settings("test", "test", summary_every=2, usage_log_path="", chat_web_search=False),
              client=provider)
    store = Store(":memory:")
    try:
        yield llm, store, model
    finally:
        await llm.close()
        store.close()


def reference(model):
    call = model.await_args.kwargs
    return json.loads(call["input"][0]["content"].split("\n", 1)[1]), call["instructions"]


async def test_casual_office_chat_treats_saved_habits_as_conditional_reference(rio):
    llm, store, model = rio
    scope = Scope(1, 10, 100)
    store.save_summary(scope, "이전에 평소 아침을 거른다고 말했다.", 0)

    await llm.answer(store, scope, "사용자", "오늘 사무실에 사람이 별로 없네")
    context, policy = reference(model)
    assert context["conversation_memory"] == "이전에 평소 아침을 거른다고 말했다."
    assert "사용자가 다시 꺼냈거나 현재 요청에 필요할 때만 참고하세요" in policy
    assert "사람이 적다는 말에는 사무실 상황에 반응하세요" in policy
    assert "현재 대화의 일회성 요청을 지속적인 선호" in policy

    await llm.answer(store, scope, "사용자", "내가 아침을 먹는다고 했는지 기억해?")
    assert reference(model)[0]["conversation_memory"] == context["conversation_memory"]


@pytest.mark.parametrize(("text", "kind"), [
    ("그만 분석해", "stop_analysis"),
    ("그 얘기는 그만해", "stop_topic"),
])
async def test_current_stop_request_carries_over_to_same_conversation_only(rio, text, kind):
    llm, store, model = rio
    dm = Scope(None, 10, 100)
    store.save_summary(dm, "사용자의 식습관을 더 분석하기로 했다.", 0)
    field = "current_conversation_boundaries"
    other_scopes = [Scope(None, 11, 100), Scope(1, 10, 100), Scope(None, 10, 200)]

    await llm.answer(store, dm, "사용자", text)
    assert reference(model)[0][field] == [{"kind": kind, "requested_this_turn": True}]
    store.add(dm, 1, text, "알겠어.")
    store.save_summary(dm, "사용자의 식습관을 더 분석하기로 했다.", store.pending(dm)[0]["id"])

    await llm.answer(store, dm, "사용자", "점심 뭐 먹지")
    context, policy = reference(model)
    assert context[field] == [{"kind": kind, "requested_this_turn": False}]
    assert "메뉴를 제안하고 사용자의 습관을 평가하지 마세요" in policy
    assert "현재 화자의 직접 요청을 이전 요약보다 우선하세요" in policy

    await llm.answer(store, dm, "사용자", "아까 식습관에 대해 다시 분석해 줄래?")
    assert "분석을 명시적으로 요청하면 현재 질문의 범위에서" in reference(model)[1]
    for scope in other_scopes:
        await llm.answer(store, scope, "사용자", "점심 뭐 먹지")
        assert field not in reference(model)[0]

    await llm.answer(store, dm, "사용자", "점심 뭐 먹지", use_memory=False)
    assert field not in reference(model)[0]
    with store.db:
        store.db.execute("UPDATE turns SET created_at=datetime('now', '-16 minutes')")
    await llm.answer(store, dm, "사용자", "점심 뭐 먹지")
    assert field not in reference(model)[0]


@pytest.mark.parametrize(("updates", "current", "active"), [
    ({}, "점심 뭐 먹지", True),
    ({"author_user_id": "200"}, "점심 뭐 먹지", False),
    ({"channel_id": "99"}, "점심 뭐 먹지", False),
    ({"role": "assistant"}, "점심 뭐 먹지", False),
    ({"context_kind": "replied_message"}, "점심 뭐 먹지", False),
    ({"context_kind": "target_user_history"}, "점심 뭐 먹지", False),
    ({"direct_trigger": False}, "점심 뭐 먹지", False),
    ({"context_truncated": True}, "점심 뭐 먹지", False),
    ({"content": "> 그만 분석해"}, "점심 뭐 먹지", False),
    ({"content": "그만 분석해. 라고 말했다"}, "점심 뭐 먹지", False),
    ({"author_user_id": "200"}, "> 그만 분석해\n점심 뭐 먹지", False),
    ({"author_user_id": "200"}, '“그만 분석해”라고 말했어', False),
    ({"author_user_id": "200"}, '"그만 분석해"', False),
    ({"author_user_id": "200"}, "앞으로 그 얘기는 먼저 꺼내지 마", False),
])
async def test_only_complete_direct_dialogue_from_current_speaker_carries_policy(
    rio, updates, current, active,
):
    llm, store, model = rio
    row = {"role": "user", "message_id": "123", "author_user_id": "100", "channel_id": "10",
           "direct_trigger": True, "context_kind": "speaker_thread", "content": "그만 분석해",
           "at": datetime.now(UTC).isoformat(), **updates}
    await llm.answer(store, Scope(1, 10, 100), "사용자", current, channel_context=[row])
    assert ("current_conversation_boundaries" in reference(model)[0]) is active


async def test_summary_excludes_bare_stop_turns_but_keeps_durable_request(rio):
    llm, store, model = rio
    scope = Scope(1, 10, 100, True)
    durable = "앞으로 그 얘기는 먼저 꺼내지 마"
    for message_id, text in enumerate(("그만 분석해", durable), 1):
        store.add(scope, message_id, text, "응", turn_id=str(message_id))
        store.add_shared_call(scope, message_id, "사용자", text)

    for summarize, field in ((llm.summarize, "new_turns"),
                             (llm.summarize_shared, "direct_calls")):
        await summarize(store, scope)
        call = model.await_args.kwargs
        assert [row["user"] for row in json.loads(call["input"])[field]] == [durable]
        assert "지속 범위가 명확할 때에만" in call["instructions"]
    assert [row["content"] for row in store.history(scope)] == ["그만 분석해", durable]
    assert not store.pending(scope)
    assert not store.pending_shared(scope)


async def test_all_ephemeral_batch_advances_summaries_and_shadow_without_request(rio):
    llm, store, model = rio
    scope = Scope(1, 10, 100, True)
    for message_id, text in enumerate(("그만 분석해", "그 얘기는 그만해"), 1):
        store.add(scope, message_id, text, "응", turn_id=str(message_id))
        store.add_shared_call(scope, message_id, "사용자", text)

    await llm.summarize(store, scope)
    await llm.summarize_shared(store, scope)
    assert store.summary(scope) == store.shared_summary(scope) == ("", 2)

    llm.settings = replace(llm.settings, structured_memory_shadow=True)
    result = await llm.extract_structured_memory(store, scope)
    assert result["rejected"] == {"temporary_conversation_boundary": 2}
    assert store.structured_memory_cursor(scope) == 2
    assert not store.active_structured_memory(scope)
    assert len(store.history(scope)) == 2
    model.assert_not_awaited()
