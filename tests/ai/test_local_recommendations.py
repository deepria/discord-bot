import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock

import discord
import httpx
import pytest

from rio_bot.ai.providers import _gemini_output
from rio_bot.ai.public_dm_context import public_dm_routing
from rio_bot.ai.routing_plan import RoutingPlan
from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store
from rio_bot.discord.web_bot import RioClient

QUERY = "증미역 주변에 묵직한 메뉴 파는 곳 있어?"
SHOP = "매장 | 가상돌솥 | 서울 강서구 가상로 10"
MENU = "메뉴 | 가상돌솥 | 돌솥정식"
UNSUPPORTED = "매장 | 합성분식 | 서울 강서구 가상로 12"
SOURCE = "https://example.com/venues/stonepot"


def citation(text, line, *, url=SOURCE, title="가상돌솥 매장 안내"):
    start = text.index(line)
    return {"type": "url_citation", "start_index": start, "end_index": start + len(line),
            "url": url, "title": title}


def response(text, annotations=(), *, search=True, status="completed"):
    output = [NS(type="web_search_call", status="completed")] if search else []
    output.append(NS(type="message", content=[
        NS(type="output_text", text=text, annotations=annotations)
    ]))
    return NS(status=status, output_text=text, output=output)


def request_reference(model):
    call = model.await_args.kwargs
    reference = json.loads(call["input"][0]["content"].split("\n", 1)[1])
    return call, reference


@pytest.fixture
async def rio(tmp_path):
    store = Store(":memory:")
    model = AsyncMock()
    client = NS(responses=NS(create=model), close=AsyncMock())
    settings = Settings(
        "test", "test", cooldown=0, db_path=str(tmp_path / "rio.sqlite"),
        usage_log_path="", telemetry_db_path="", event_log_path=str(tmp_path / "events.jsonl"),
        dm_always_reply=True, chat_web_search=True, summary_every=100,
    )
    llm = LLM(settings, client=client)
    bot = RioClient(settings, store=store, llm=llm)
    bot._connection.user = NS(id=99)
    guild = NS(id=1, emojis=[], me=NS(), default_role=NS())
    channel = MagicMock(spec=discord.TextChannel, id=10, guild=guild)
    channel.permissions_for.return_value = NS(view_channel=False, read_message_history=True)
    channel.typing.return_value.__aenter__ = AsyncMock()
    channel.typing.return_value.__aexit__ = AsyncMock()
    channel.send = AsyncMock(return_value=NS(id=1000))
    scope = Scope(1, 10, 100)
    bot.recent.mark_hydrated(scope)

    async def send(message_id, content, *, owner=100):
        message = NS(id=message_id, content="리오야 " + content, channel=channel, guild=guild,
                     author=NS(id=owner, bot=False, display_name="시험 사용자"),
                     webhook_id=None, mentions=[], reference=None, attachments=[], stickers=[])
        await bot.on_message(message)
        return channel.send.await_args.args[0]

    try:
        yield NS(llm=llm, store=store, model=model, bot=bot, send=send, scope=scope)
    finally:
        await bot.close()


async def test_discord_local_followups_and_search_audit_are_scoped_to_one_speaker(rio):
    text = f"{SHOP}\n{MENU}\n평점 | 가상돌솥 | 4.9\n{UNSUPPORTED}"
    rio.model.return_value = response(
        text, [citation(text, SHOP), citation(text, MENU),
               citation(text, UNSUPPORTED, url="https://example.com/district", title="증미역 소개")]
    )
    answer = await rio.send(1, QUERY)
    assert ("가상돌솥" in answer and "가상로 10" in answer and SOURCE in answer
            and "돌솥정식" in answer)
    assert "합성분식" not in answer and "4.9" not in answer
    rio.model.return_value = response("응.", search=False)
    await rio.send(2, "홍대입구역 주변에 가게 추천해줘", owner=200)
    rio.model.return_value = response(text, [citation(text, SHOP)])
    for message_id, followup in ((3, "가게 이름도"), (4, "리스트로")):
        await rio.send(message_id, followup)
        request, refs = request_reference(rio.model)
        assert request["input"][-1]["content"] == followup
        assert request["tool_choice"] == "required"
        assert refs["local_recommendation_request"]["area"] == "증미역"
        assert "묵직한 메뉴" in refs["local_recommendation_request"]["query"]
        assert "홍대입구역" not in refs["local_recommendation_request"]["query"]
        assert rio.store.last_search_outcome(rio.scope) == "partial"

    model_calls = rio.model.await_count
    reply = await rio.send(5, "찾아보고 답한 거야?")
    assert "검색했고" in reply and "매장만 보여줬어" in reply
    assert rio.model.await_count == model_calls
    assert rio.store.last_search_outcome(rio.scope) == "not_run"

    rio.model.return_value = response("응.", search=False)
    await rio.send(6, "오늘 사무실에 사람이 별로 없네")
    await rio.send(7, "리스트로")
    request, refs = request_reference(rio.model)
    assert "local_recommendation_request" not in refs
    assert all(tool["type"] == "function" for tool in request.get("tools", []))
    await rio.send(8, "점심 뭐 먹지")
    request, refs = request_reference(rio.model)
    assert "local_recommendation_request" not in refs
    assert all(tool["type"] == "function" for tool in request.get("tools", []))

    events = [json.loads(line)
              for line in Path(rio.bot.settings.event_log_path).read_text().splitlines()]
    event = next(row for row in events if row.get("search_outcome") == "partial")
    assert event["search_decision"] == "required" and event["verified_places"] == 1
    assert event["web_search_calls"] == 1
    assert SOURCE not in json.dumps(events)


@pytest.mark.parametrize(("mode", "receipt", "message"), [
    ("not_run", "not_run", "검색이 실행되지"),
    ("failed", "failed", "실패"),
    ("exception", "failed", "실패"),
    ("empty", "no_evidence", "출처가 있는 매장"),
    ("generic", "no_evidence", "출처가 있는 매장"),
    ("disabled", "disabled", "꺼져"),
    ("confirmed", "confirmed", "이름과 위치를 확인했어"),
])
async def test_local_evidence_and_audit_do_not_promote_generated_claims(rio, mode, receipt, message):
    if mode == "disabled":
        rio.llm.settings = replace(rio.llm.settings, chat_web_search=False)
    elif mode == "failed":
        rio.model.return_value = response(SHOP, status="failed")
    elif mode == "exception":
        rio.model.side_effect = httpx.ReadTimeout("test")
    elif mode == "not_run":
        rio.model.return_value = response(SHOP, [citation(SHOP, SHOP)], search=False)
    elif mode == "empty":
        rio.model.return_value = response("찾아봤어. 결과가 없어.")
    elif mode == "generic":
        rio.model.return_value = response(SHOP, [citation(SHOP, SHOP, title="증미역 맛집 목록")])
    else:
        rio.model.return_value = response(SHOP, [citation(SHOP, SHOP)])
    reply = await rio.send(1, QUERY)
    assert message in reply
    assert ("가상돌솥" in reply) is (receipt == "confirmed")
    assert rio.store.last_search_outcome(rio.scope) == receipt

    calls = rio.model.await_count
    outcome_reply = await rio.send(2, "찾아봤어?")
    assert "직전" in outcome_reply and "확인할 수 없어" not in outcome_reply
    assert rio.model.await_count == calls
    if mode == "disabled":
        assert calls == 0


async def test_audit_reports_unknown_for_legacy_cross_scope_or_memory_off(rio):
    rio.store.add(rio.scope, 1, "예전 질문", "찾아봤어.")
    unknown = await rio.send(2, "실제로 검색했어?")
    assert "확인할 수 없어" in unknown
    rio.store.add(rio.scope, 3, "예전 질문", "찾아봤어.", search_outcome="confirmed")
    assert "확인할 수 없어" in await rio.send(4, "찾아봤어?", owner=200)
    rio.store.set_memory_mode(rio.scope, "off")
    assert "확인할 수 없어" in await rio.send(5, "찾아봤어?")
    rio.model.assert_not_awaited()


async def test_untracked_reply_does_not_report_an_older_search(rio):
    rio.store.add(rio.scope, 1, QUERY, "찾아봤어.", search_outcome="confirmed")
    rio.store.set_memory_mode(rio.scope, "read_only")
    rio.model.return_value = response("응.", search=False)
    await rio.send(2, "그렇구나")
    assert not rio.store.seen(2)
    assert "확인할 수 없어" in await rio.send(3, "찾아봤어?")


async def test_local_search_does_not_guess_location_from_other_speakers_or_spaces(rio):
    rio.store.add(Scope(1, 11, 100), 1, QUERY, "응")
    foreign = {
        "role": "user", "author_user_id": "200", "channel_id": "10", "message_id": "2",
        "context_kind": "speaker_thread", "direct_trigger": True, "content": QUERY,
    }
    reply = await rio.llm.answer(
        rio.store, rio.scope, "시험 사용자", "가게 이름도", channel_context=[foreign],
    )
    assert "어느 지역" in reply
    rio.model.assert_not_awaited()


async def test_area_answer_after_clarification_retains_menu_conditions(rio):
    assert "어느 지역" in await rio.send(1, "묵직한 메뉴 파는 곳 있어?")
    rio.model.assert_not_awaited()
    rio.model.return_value = response(SHOP, [citation(SHOP, SHOP)])
    for message_id, content in ((2, "증미역"), (3, "리스트로")):
        assert SOURCE in await rio.send(message_id, content)
        _, refs = request_reference(rio.model)
        assert refs["local_recommendation_request"]["area"] == "증미역"
        assert "묵직한 메뉴" in refs["local_recommendation_request"]["query"]


def test_public_dm_local_followup_reuses_the_topic_and_stops_at_a_new_one():
    scope_source = "guild:1:channel:10:user:100"
    prior = {"source": scope_source, "user": QUERY}
    followup = RoutingPlan("가게 이름도", "가게 이름도")
    recent = [prior, {"source": scope_source, "user": "내일 이어서 얘기하자"}]
    assert "증미역" in public_dm_routing(followup, recent).routing_query
    recent = [prior, {"source": scope_source, "user": "오늘 야구 경기 봤어?"}]
    assert public_dm_routing(followup, recent) == followup


async def test_gemini_citation_offsets_survive_adapter_and_openrouter_uses_tool_counter(rio):
    raw = SHOP + "\n" + UNSUPPORTED
    rio.model.return_value = _gemini_output({"status": "completed", "steps": [
        {"type": "google_search_call"},
        {"type": "model_output", "content": [
            {"type": "text", "text": raw, "annotations": [citation(raw, SHOP)]},
        ]},
    ]})
    assert SOURCE in await rio.send(1, QUERY)
    assert "합성분식" not in await rio.send(2, "가게 이름도")

    rio.llm.settings = replace(rio.llm.settings, provider="openrouter")
    reply = response(SHOP, [citation(SHOP, SHOP)], search=False)
    reply.usage = NS(server_tool_use_details={"web_search_requests": 1})
    rio.model.return_value = reply
    assert SOURCE in await rio.send(3, QUERY)
    request, _ = request_reference(rio.model)
    assert request["tools"][0]["type"] == "openrouter:web_search"
    assert rio.store.last_search_outcome(rio.scope) == "confirmed"

    reply.usage = None
    assert "검색 실행을 확인할 수 없어서" in await rio.send(4, QUERY)
    assert rio.store.last_search_outcome(rio.scope) == "unknown"


async def test_openai_inline_citation_is_attributed_to_its_own_row_only(rio):
    marker = f"([가상돌솥]({SOURCE}))"
    text = SHOP + " " + marker + "\n" + UNSUPPORTED
    rio.model.return_value = response(text, [citation(text, marker)])
    reply = await rio.send(1, QUERY)
    assert SOURCE in reply and "가상돌솥" in reply and "합성분식" not in reply
