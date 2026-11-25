import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from rio_bot.ai.dm_memory_recall import CURRENT_PRIVATE_DM
from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store
from rio_bot.discord.web_bot import RioClient

OWNER = Scope(None, 10, 100)
SIBLING = Scope(None, 12, 100)
OTHER = Scope(None, 10, 200)
GUILD = Scope(1, 15, 100, True)
ASK = "내가 좋아하는 음료 기억해?"
COFFEE = "쓴 커피를 좋아한다."
CORRECTION = "이제 커피 안 마셔"


def seed(store, scope, message_id, item, *, kind="preference", raw="유효한 합성 발화"):
    store.add(scope, message_id, raw, "응.", turn_id=f"turn-{message_id}")
    through = store.pending_structured_memory(scope)[-1]["id"]
    report = store.save_structured_memory(scope, through_id=through, items=[{
        "kind": kind, "content": item, "source_message_ids": [str(message_id)], "confidence": 0.9,
        "disclosure": "channel" if scope.public_at_capture else "owner_private",
    }])
    assert report["written"] == 1
    return store.verified_structured_memory(scope)[0]["id"]


@pytest.fixture
async def rio(tmp_path):
    store = Store(":memory:")
    settings = RuntimeSettings(Settings(
        "test", "test", db_path=str(tmp_path / "admin.db"), cooldown=0, dm_always_reply=True,
        usage_log_path=str(tmp_path / "usage.jsonl"), event_log_path="", telemetry_db_path="",
        summary_every=100, chat_web_search=False, history_turns=20,
    ), store)

    async def answer(**kwargs):
        reference = json.loads(kwargs["input"][0]["content"].split("\n", 1)[1])
        selected = reference.get("verified_dm_memory", ())
        text = reference.get("latest_dm_correction") or (
            selected[0]["content"] if selected else "합성 일반 답변"
        )
        return NS(status="completed", output_text=text, output=[])

    model = AsyncMock(side_effect=answer)
    llm = LLM(settings, client=NS(responses=NS(create=model), close=AsyncMock()))
    bot = RioClient(settings, store=store, llm=llm)
    bot._connection.user = NS(id=99)

    def channel(scope, channel_type):
        cls = discord.DMChannel if scope.guild_id is None else discord.TextChannel
        result = MagicMock(spec=cls, id=scope.channel_id)
        result.type = channel_type
        result.typing.return_value.__aenter__ = AsyncMock()
        result.typing.return_value.__aexit__ = AsyncMock()
        result.send = AsyncMock(return_value=NS(id=9000))
        return result

    async def call(message_id, text, *, scope=OWNER, channel_type=discord.ChannelType.private):
        target = channel(scope, channel_type)
        guild = NS(id=scope.guild_id, emojis=[], me=NS(), default_role=NS()) if scope.guild_id else None
        if guild:
            target.permissions_for.return_value = NS(view_channel=True, read_message_history=True)
        message = NS(id=message_id, content=f"리오야 {text}", channel=target, guild=guild,
                     author=NS(id=scope.user_id, display_name="시험 계정", bot=False),
                     webhook_id=None, mentions=[], attachments=[], stickers=[], reference=None)
        await bot.on_message(message)
        request = model.await_args.kwargs
        ref = json.loads(request["input"][0]["content"].split("\n", 1)[1])
        return ref, request, target.send.await_args.args[0]

    async def toggle(value, *, scope=OWNER, channel_type=discord.ChannelType.private):
        response = NS(send_message=AsyncMock(), defer=AsyncMock())
        response.is_done = lambda: response.defer.await_count > 0
        inter = NS(guild_id=scope.guild_id, channel_id=scope.channel_id, user=NS(id=scope.user_id),
                   channel=NS(type=channel_type), response=response, followup=NS(send=AsyncMock()))
        command = bot.tree.get_command("memory").get_command("recall")
        await command.callback(inter, value)
        return (inter.followup.send.await_args or response.send_message.await_args).args[0]

    try:
        yield NS(store=store, call=call, toggle=toggle, model=model, llm=llm, settings=settings)
    finally:
        await bot.close()


async def test_recall_defaults_off_can_enable_and_rolls_back_to_legacy_summary(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    rio.store.save_summary(OWNER, "기존 요약", 0)
    assert "꺼져" in await rio.toggle(None)
    ref, request, _ = await rio.call(101, ASK)
    assert ref["conversation_memory"] == "기존 요약"
    assert "verified_dm_memory" not in ref

    assert "켜져" in await rio.toggle(True)
    ref, enabled_request, answer = await rio.call(102, ASK)
    assert ref["verified_dm_memory"] == [{"kind": "preference", "content": COFFEE}]
    assert answer == COFFEE
    assert ref["current_user_message"]["relationship"] == "participant"
    assert "기존 요약은 그다음에" in enabled_request["instructions"]
    assert enabled_request["model"] == request["model"]

    ref, _, answer = await rio.call(103, "오늘 사무실에 사람이 적네")
    assert "verified_dm_memory" not in ref and COFFEE not in answer
    ref, _, _ = await rio.call(104, "친구가 어떤 음료를 좋아하는지 기억해?")
    assert "verified_dm_memory" not in ref

    assert "꺼져" in await rio.toggle(False)
    ref, _, _ = await rio.call(105, ASK)
    assert "verified_dm_memory" not in ref
    assert ref["conversation_memory"] == "기존 요약"
    assert rio.model.await_count == 5

    log = Path(rio.settings.usage_log_path).read_text()
    enabled = [json.loads(line) for line in log.splitlines()
               if '"structured_dm_read_items"' in line]
    assert len(enabled) == 1
    assert enabled[0]["structured_dm_read_items"] == 1
    assert enabled[0]["structured_dm_read_lookup_ms"] >= 0
    assert enabled[0]["structured_dm_read_estimated_input_tokens"] > 0
    assert COFFEE not in log and ASK not in log


async def test_scope_gate_blocks_guild_group_dm_and_other_conversations(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    seed(rio.store, GUILD, 101, "서버에서는 홍차를 좋아한다.")
    seed(rio.store, OTHER, 102, "다른 사용자는 라테를 좋아한다.")
    seed(rio.store, SIBLING, 103, "별도 DM에서는 녹차를 좋아한다.")
    assert "켜져" in await rio.toggle(True)
    for message_id, scope, kind in [
        (104, OWNER, discord.ChannelType.group),
        (105, GUILD, discord.ChannelType.text),
        (106, OTHER, discord.ChannelType.private),
        (107, SIBLING, discord.ChannelType.private),
    ]:
        if kind == discord.ChannelType.private:
            assert not rio.store.dm_structured_recall_enabled(scope)
        else:
            assert "1:1 DM" in await rio.toggle(True, scope=scope, channel_type=kind)
        ref, _, _ = await rio.call(message_id, ASK, scope=scope, channel_type=kind)
        assert "verified_dm_memory" not in ref
    ref, _, _ = await rio.call(108, ASK)
    assert ref["verified_dm_memory"] == [{"kind": "preference", "content": COFFEE}]


async def test_newer_direct_correction_suppresses_old_candidate_summary_and_reply(rio):
    seed(rio.store, OWNER, 100, COFFEE, raw="커피가 좋아")
    rio.store.save_summary(OWNER, "예전에는 커피가 좋아", 0)
    rio.store.set_dm_structured_recall(OWNER, True)
    await rio.call(101, CORRECTION)
    rio.store.add(OWNER, 102, CORRECTION, "아까 커피가 좋아라고 했지", turn_id="turn-102")
    ref, _, response = await rio.call(103, ASK)
    assert ref["latest_dm_correction"] == response == CORRECTION
    assert ref["conversation_memory"] == "" and ref["conversation_history"] == []
    assert "verified_dm_memory" not in ref


async def test_confirmed_replacement_wins_and_withdrawal_excludes_it(rio):
    item_id = seed(rio.store, OWNER, 100, COFFEE)
    rio.store.add(OWNER, 101, CORRECTION, "예전에는 커피를 좋아했지.", turn_id="turn-101")
    assert rio.store.correct_structured_memory(OWNER, item_id, "101")
    rio.store.set_dm_structured_recall(OWNER, True)
    ref, _, answer = await rio.call(102, ASK)
    [replacement] = rio.store.verified_structured_memory(OWNER)
    assert ref["verified_dm_memory"] == [{"kind": "preference", "content": CORRECTION}]
    assert answer == CORRECTION and COFFEE not in json.dumps(ref, ensure_ascii=False)
    assert rio.store.delete_structured_memory(OWNER, replacement["id"])
    ref, _, _ = await rio.call(103, ASK)
    assert "verified_dm_memory" not in ref
    assert CORRECTION not in json.dumps(ref, ensure_ascii=False)


async def test_read_policy_and_missing_provenance_exclude_candidates(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    rio.store.set_dm_structured_recall(OWNER, True)
    rio.store.set_memory_mode(OWNER, "write_only")
    assert "읽기가 꺼져" in await rio.toggle(None)
    ref, _, _ = await rio.call(101, ASK)
    assert "verified_dm_memory" not in ref
    with rio.store.db:
        rio.store.db.execute("UPDATE structured_memory_items SET source_turn_ids=''")
    rio.store.set_memory_mode(OWNER, "normal")
    ref, _, _ = await rio.call(102, ASK)
    assert "verified_dm_memory" not in ref


async def test_read_failure_uses_legacy_summary_and_does_not_retry_provider(rio, monkeypatch):
    rio.store.set_dm_structured_recall(OWNER, True)
    rio.store.save_summary(OWNER, "기존 요약", 0)
    lookup = MagicMock(side_effect=sqlite3.OperationalError("synthetic lookup error"))
    monkeypatch.setattr(rio.store, "verified_structured_memory", lookup)
    ref, _, _ = await rio.call(101, ASK)
    assert ref["conversation_memory"] == "기존 요약"
    assert "verified_dm_memory" not in ref
    assert rio.model.await_count == 1


async def test_expired_source_and_clear_exclude_candidate_and_reset_opt_in(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    rio.store.set_dm_structured_recall(OWNER, True)
    for message_id in range(101, 114):
        rio.store.add(OWNER, message_id, "새 대화", "응.", turn_id=f"turn-{message_id}")
    assert rio.store.active_structured_memory(OWNER)
    ref, _, _ = await rio.call(114, ASK)
    assert "verified_dm_memory" not in ref
    rio.store.forget(OWNER)
    assert "꺼져" in await rio.toggle(None)


async def test_multiple_relevant_topics_respect_count_and_character_limits(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    seed(rio.store, OWNER, 101, "우분투를 쓴다.", kind="fact")
    seed(rio.store, OWNER, 102, "국수를 좋아한다.")
    seed(rio.store, OWNER, 103, "반말을 선호한다.")
    rio.store.set_dm_structured_recall(OWNER, True)
    ref, _, _ = await rio.call(104, "내 음료 음식 운영체제 말투 기억해?")
    items = ref["verified_dm_memory"]
    assert len(items) == 3
    assert {item["content"] for item in items} <= {
        COFFEE, "우분투를 쓴다.", "국수를 좋아한다.", "반말을 선호한다.",
    }
    seed(rio.store, OWNER, 105, "우분투를 쓴다. " + "근거 " * 190, kind="fact")
    ref, _, _ = await rio.call(106, "내 음료 음식 운영체제 말투 기억해?")
    assert sum(len(item["content"]) for item in ref["verified_dm_memory"]) <= 600


async def test_direct_answers_and_external_search_do_not_receive_structured_memory(rio):
    seed(rio.store, OWNER, 100, COFFEE)
    rio.store.set_dm_structured_recall(OWNER, True)
    await rio.llm.answer(rio.store, OWNER, "시험", ASK)
    assert "verified_dm_memory" not in str(rio.model.await_args.kwargs)

    rio.settings.set_text("CHAT_WEB_SEARCH", "true")
    token = CURRENT_PRIVATE_DM.set(OWNER.conversation)
    try:
        await rio.llm.answer(rio.store, OWNER, "시험", "내 커피 취향에 맞는 메뉴의 출처 링크 찾아줘")
    finally:
        CURRENT_PRIVATE_DM.reset(token)
    request = rio.model.await_args.kwargs
    assert request["tools"]
    assert "verified_dm_memory" not in str(request)
