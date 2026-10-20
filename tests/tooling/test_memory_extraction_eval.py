import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.tooling import memory_extraction_eval as module


def test_synthetic_cases_have_negative_and_positive_controls():
    cases = module.read_cases(module.DEFAULT_CASES)
    assert len(cases) == 12
    assert sum(case["expected_count"] > 0 for case in cases) == 3


@pytest.mark.asyncio
async def test_validate_only_never_loads_settings_or_creates_output(monkeypatch, tmp_path):
    def forbidden(*args):
        pytest.fail("validation accessed provider configuration")
    monkeypatch.setattr(module, "evaluation_settings", forbidden)
    target = tmp_path / "must-not-exist.jsonl"
    args = module.parser().parse_args(["--validate-only", "--output", str(target)])
    assert await module.run(args) == []
    assert not target.exists()


@pytest.mark.asyncio
async def test_runner_executes_real_extractor_and_store_without_answer_generation():
    def reply(**kwargs):
        payload = json.loads(kwargs["input"])
        return NS(status="completed", usage=None, output=[], output_text=json.dumps({"items": [{
            "kind": "preference", "content": "synthetic durable preference", "confidence": 0.8,
            "disclosure": "owner_private",
            "source_message_ids": [payload["eligible_source_message_ids"][0]],
        }]}))
    client = NS(responses=NS(create=AsyncMock(side_effect=reply)), close=AsyncMock())
    llm = LLM(Settings("test", "test", structured_memory_shadow=True), client=client)
    case = next(c for c in module.read_cases(module.DEFAULT_CASES) if c["id"] == "P01_durable_preference")
    result = await module.run_case(llm, case)
    assert result["actual_count"] == 1 and result["count_matches"]
    assert result["cursor_advanced"]
    assert result["manual_review_required"]
    assert result["candidates"][0]["source_turn_ids"]
    assert client.responses.create.await_count == 1
    await llm.close()



def test_memory_model_is_explicit_and_does_not_silently_use_answer_model(monkeypatch):
    monkeypatch.setattr(module, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("MEMORY_MODEL", raising=False)
    monkeypatch.setenv("LLM_MODEL", "answer-model")
    with pytest.raises(ValueError, match="MEMORY_MODEL"):
        module.evaluation_settings(module.parser().parse_args([]))
