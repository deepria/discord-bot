import pytest

from rio_bot.ai.chat_llm import LLM as LegacyChatLLM
from rio_bot.ai.chat_llm_v2 import LLM as LegacyV2LLM
from rio_bot.ai.information_pipeline import InformationPipeline
from rio_bot.ai.request_assembly import RequestAssembler
from rio_bot.ai.runtime_llm import LLM as RuntimeLLM
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store


def test_runtime_pipeline_has_named_responsibility_layers():
    assert issubclass(RuntimeLLM, InformationPipeline)
    assert issubclass(InformationPipeline, RequestAssembler)
    assert InformationPipeline in RuntimeLLM.__mro__
    assert RequestAssembler in RuntimeLLM.__mro__


def test_legacy_chat_module_names_keep_full_pipeline_behavior():
    assert LegacyChatLLM is InformationPipeline
    assert LegacyV2LLM is InformationPipeline


@pytest.mark.asyncio
async def test_runtime_settings_support_gemini_tier1_fallback_client():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(Settings(
            api_key="primary-key",
            discord_token="token",
            provider="gemini",
            gemini_api_key="primary-key",
            gemini_tier1_api_key="tier1-key",
            gemini_tier1_model="gemini-tier1",
            db_path=":memory:",
        ), store)
        llm = RuntimeLLM(settings)
        try:
            assert llm.client.provider_name == "gemini"
        finally:
            await llm.close()
    finally:
        store.close()


@pytest.mark.asyncio
async def test_runtime_llm_forwards_current_message_id_to_request_assembly(monkeypatch):
    received = {}

    async def parent_answer(*args, **kwargs):
        received.update(kwargs)
        return "ok"

    monkeypatch.setattr(InformationPipeline, "answer", parent_answer)
    store = Store(":memory:")
    try:
        llm = object.__new__(RuntimeLLM)
        answer = await llm.answer(
            store, Scope(1, 10, 100), "synthetic-user", "synthetic message", message_id="42",
        )
        assert answer == "ok"
        assert received["message_id"] == "42"
    finally:
        store.close()
