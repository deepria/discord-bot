import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from rio_bot.ai.runtime_llm import LLM
from rio_bot.ai.structured_memory import POLICY_VERSION, build_extraction_input
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store


def client(text):
    return NS(responses=NS(create=AsyncMock(return_value=NS(
        status="completed", output_text=text, usage=None, output=[]))), close=AsyncMock())


def memory(scope):
    store = Store(":memory:")
    store.add(scope, 1550000000000000000, "synthetic user 1", "synthetic Rio proposal", turn_id="t1")
    store.add(scope, 1550000000000000001, "synthetic user 2", "synthetic Rio response", turn_id="t2")
    return store


@pytest.mark.asyncio
async def test_real_extraction_sends_roles_time_policy_and_advances_zero_cursor(tmp_path):
    scope = Scope(None, 10, 20)
    store = memory(scope)
    fake = client('{"items":[]}')
    log = tmp_path / "usage.jsonl"
    llm = LLM(Settings("test", "test", structured_memory_shadow=True, usage_log_path=str(log)),
              client=fake)
    result = await llm.extract_structured_memory(store, scope)
    assert result["written"] == 0
    assert store.structured_memory_cursor(scope) == 2
    call = fake.responses.create.call_args.kwargs
    payload = json.loads(call["input"])
    assert payload["policy_version"] == POLICY_VERSION
    assert payload["timezone"] is None
    assert payload["turns"][0]["rio"] == "synthetic Rio proposal"
    assert payload["turns"][1]["user"] == "synthetic user 2"
    assert payload["turns"][0]["message_at_utc"].endswith("+00:00")
    await llm.extract_structured_memory(store, scope)
    assert fake.responses.create.await_count == 1
    contents = log.read_text()
    assert POLICY_VERSION in contents
    assert "synthetic user" not in contents and "synthetic Rio" not in contents
    assert "1550000000000000000" not in contents
    store.close()
    await llm.close()


@pytest.mark.asyncio
async def test_context_is_scope_local_and_cannot_be_new_source():
    scope = Scope(None, 10, 20)
    store = memory(scope)
    store.save_structured_memory(scope, through_id=2, items=[])
    other = Scope(None, 11, 21)
    store.add(other, 1550000000000000002, "other user's secret", "other reply", turn_id="other")
    store.add(scope, 1550000000000000003, "yes", "response", turn_id="t3")
    store.add(scope, 1550000000000000004, "yes again", "response", turn_id="t4")
    candidate = {"kind": "preference", "content": "synthetic", "confidence": 0.8,
                 "disclosure": "owner_private", "source_message_ids": ["1550000000000000001"]}
    fake = client(json.dumps({"items": [candidate]}))
    llm = LLM(Settings("test", "test", structured_memory_shadow=True), client=fake)
    result = await llm.extract_structured_memory(store, scope)
    assert result["rejected"] == {"invalid_output": 1}
    assert store.structured_memory_cursor(scope) == 2
    payload = json.loads(fake.responses.create.call_args.kwargs["input"])
    assert payload["context_only"][0]["message_id"] == "1550000000000000001"
    assert "other user's secret" not in json.dumps(payload)
    assert store.active_structured_memory(scope) == []
    store.close()
    await llm.close()


def test_budget_omits_whole_evidence_and_never_makes_it_eligible():
    scope = Scope(None, 10, 20)
    store = Store(":memory:")
    store.add(scope, 1550000000000000000, "x" * 20000, "reply", turn_id="t1")
    store.add(scope, 1550000000000000001, "intelligible short turn", "response", turn_id="t2")
    payload = build_extraction_input(store.pending_structured_memory(scope), [],
                                     capture="owner_private", max_chars=3000)
    assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 3000
    assert payload["turns"][0]["complete"] is False
    assert payload["turns"][0]["user"] == ""
    assert payload["eligible_source_message_ids"] == ["1550000000000000001"]
    store.close()


@pytest.mark.asyncio
async def test_all_oversized_turns_consume_batch_without_provider_call():
    scope = Scope(None, 10, 20)
    store = Store(":memory:")
    for i in range(2):
        store.add(scope, 1550000000000000000 + i, "x" * 20000, "r", turn_id=f"t{i}")
    fake = client('{"items":[]}')
    llm = LLM(Settings("test", "test", structured_memory_shadow=True), client=fake)
    result = await llm.extract_structured_memory(store, scope)
    assert result["rejected"] == {"context_budget": 2}
    assert store.structured_memory_cursor(scope) == 2
    fake.responses.create.assert_not_awaited()
    store.close()
    await llm.close()


def test_sources_after_consumed_boundary_are_rejected():
    scope = Scope(None, 10, 20)
    store = memory(scope)
    with pytest.raises(ValueError, match="sources"):
        store.save_structured_memory(scope, through_id=1, items=[{
            "kind": "preference", "content": "synthetic", "confidence": 0.8,
            "disclosure": "owner_private", "source_message_ids": ["1550000000000000001"],
        }])
    assert store.structured_memory_cursor(scope) == 0
    store.close()


@pytest.mark.asyncio
async def test_legacy_unlinked_turns_cannot_create_new_unlinked_candidates():
    scope = Scope(None, 10, 20)
    store = Store(":memory:")
    store.add(scope, 1550000000000000000, "old input", "old reply")
    store.add(scope, 1550000000000000001, "old input again", "old reply")
    fake = client('{"items":[]}')
    llm = LLM(Settings("test", "test", structured_memory_shadow=True), client=fake)
    result = await llm.extract_structured_memory(store, scope)
    assert result["rejected"] == {"missing_turn_provenance": 2}
    assert store.structured_memory_cursor(scope) == 2
    fake.responses.create.assert_not_awaited()
    store.close()
    await llm.close()
