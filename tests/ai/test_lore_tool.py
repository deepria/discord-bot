import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from rio_bot.ai.lore_tool import register_lore_tool
from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store


@pytest.mark.asyncio
async def test_lore_tool_keeps_reviewed_and_admin_sources_distinct(tmp_path):
    settings = Settings("test", "test", db_path=str(tmp_path / "db.sqlite"), lore_tool_enabled=True)
    client = NS(responses=NS(), close=AsyncMock())
    llm = LLM(settings, client=client)
    try:
        llm.runtime_lore.add("profile", "시험용 설정", "리오", "리오", "self", "시험")
        telemetry = {}
        tool = register_lore_tool(llm, telemetry)
        result = await tool.run({"query": "리오 밀레니엄 세미나"})
        assert result["status"] == "ok"
        admin = next(row for row in result["results"] if row["reference"] == "runtime_lore.profile")
        canon = next(row for row in result["results"] if row["reference"] == "canon.rio.basic.profile")
        assert admin["source"]["type"] == "administrator" and not admin["canon"]
        assert canon["source"]["locator"] and canon["canon"]
        assert (await tool.run({"query": "qzxvwqzxvw"})) == {"status": "empty", "results": []}
        assert (await tool.run({"query": "리오", "channel_id": "9"}))["status"] == "invalid_arguments"
        assert telemetry["lore_tool_calls"] == 2 and telemetry["lore_tool_elapsed_ms"] >= 0
    finally:
        await llm.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True])
async def test_lore_tool_switch_and_answer_input(tmp_path, enabled):
    requests = []

    async def create(**kwargs):
        requests.append(kwargs)
        if enabled and len(requests) == 1:
            return NS(status="completed", output=[{
                "type": "function_call", "call_id": "lookup", "name": "search_lore",
                "arguments": json.dumps({"query": "리오 나이 키 취미"}, ensure_ascii=False),
            }])
        return NS(status="completed", output_text="응", output=[])

    settings = Settings(
        "test", "test", chat_web_search=False, db_path=str(tmp_path / "db.sqlite"),
        usage_log_path="", lore_tool_enabled=enabled,
    )
    llm = LLM(settings, client=NS(responses=NS(create=create), close=AsyncMock()))
    store = Store(":memory:")
    try:
        assert await llm.answer(store, Scope(None, 10, 100), "질문자", "너 키가 몇이야?") == "응"
    finally:
        store.close()
        await llm.close()
    reference = json.loads(requests[0]["input"][0]["content"].split("\n", 1)[1])["lore_reference"]
    if enabled:
        assert not reference and requests[0]["tools"][0]["name"] == "search_lore"
        found = json.loads(requests[1]["input"][-1]["output"])["results"]
        assert any("171센티미터" in item["content"] and item["canon"] for item in found)
    else:
        assert "tools" not in requests[0] and len(requests) == 1
        assert any("171센티미터" in item["content"] for item in reference)
