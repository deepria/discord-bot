from pathlib import Path

import pytest

from rio_bot.core.config import Settings, parse_external_context_policy
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store


def _base(**overrides):
    values = {
        "api_key": "key",
        "discord_token": "token",
    }
    values.update(overrides)
    return Settings(**values)


def test_external_context_policy_parser():
    assert parse_external_context_policy("bot_interactions_only") == "bot_interactions_only"
    assert parse_external_context_policy("FULL") == "full"
    with pytest.raises(ValueError):
        parse_external_context_policy("everything")


def test_settings_load_uses_code_defaults_when_runtime_env_is_absent(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("OPENAI_API_KEY", "key")
    for name in (
        "CALL_PREFIXES",
        "DM_ALWAYS_REPLY",
        "PUBLIC_SERVER_MEMORY_IN_DM",
        "EXTERNAL_CONTEXT_POLICY",
        "CHAT_WEB_SEARCH",
        "COMMUNITY_LORE",
        "MAX_OUTPUT_TOKENS",
        "CHANNEL_CONTEXT_CHARS",
        "HISTORY_MAX_CHARS",
        "LORE_MAX_ITEMS",
        "LORE_MAX_CHARS",
        "RUNTIME_DEFAULT_LOCATION",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.load()
    assert settings.call_prefixes == ("리오야",)
    assert settings.dm_always_reply is False
    assert settings.public_memory_in_dm is True
    assert settings.external_context_policy == "bot_interactions_only"
    assert settings.chat_web_search is True
    assert settings.community_lore is True
    assert settings.output_tokens == 1000
    assert settings.channel_context_chars == 6000
    assert settings.history_max_chars == 12000
    assert settings.lore_max_items == 6
    assert settings.lore_max_chars == 3200
    assert settings.runtime_default_location == ""


def test_settings_load_accepts_ollama_without_api_key(monkeypatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LLM_MODEL", "qwen3.5:9b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://172.30.1.71:11434")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    settings = Settings.load()

    assert settings.provider == "ollama"
    assert settings.model == "qwen3.5:9b"
    assert settings.ollama_base_url == "http://172.30.1.71:11434"
    assert settings.api_key == ""


def test_runtime_settings_fall_back_to_code_defaults_without_db_override():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        assert settings.call_prefixes == ("리오야",)
        assert settings.dm_always_reply is False
        assert settings.public_memory_in_dm is True
        assert settings.chat_web_search is True
        assert settings.community_lore is True
        assert settings.output_tokens == 1000
        assert settings.channel_context_chars == 6000
        assert settings.history_max_chars == 12000
        assert settings.lore_max_items == 6
        assert settings.lore_max_chars == 3200
        assert settings.runtime_default_location == ""
    finally:
        store.close()


def test_runtime_override_is_immediate_and_survives_reload(tmp_path: Path):
    db = tmp_path / "runtime.sqlite3"
    base = _base(channel_context_chars=5000, chat_web_search=True)

    store = Store(str(db))
    settings = RuntimeSettings(base, store)
    assert settings.set_text("CHANNEL_CONTEXT_CHARS", "8000") == 8000
    assert settings.set_text("chat_web_search", "off") is False
    assert settings.set_text("CALL_PREFIXES", "리오야, 리오") == ("리오야", "리오")
    store.close()

    store = Store(str(db))
    try:
        reloaded = RuntimeSettings(base, store)
        assert reloaded.channel_context_chars == 8000
        assert reloaded.chat_web_search is False
        assert reloaded.call_prefixes == ("리오야", "리오")
        assert reloaded.source("CHANNEL_CONTEXT_CHARS") == "db"
    finally:
        store.close()


def test_reset_removes_db_override_and_restores_startup_value():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(lore_max_items=9), store)
        settings.set_text("LORE_MAX_ITEMS", "3")
        assert settings.lore_max_items == 3
        assert settings.reset("LORE_MAX_ITEMS") == 9
        assert settings.lore_max_items == 9
        assert settings.source("LORE_MAX_ITEMS") == "startup"
    finally:
        store.close()


def test_runtime_location_can_explicitly_override_env_value_with_empty_string():
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(runtime_default_location="Seoul"), store)
        assert settings.set_text("RUNTIME_DEFAULT_LOCATION", "none") == ""
        assert settings.runtime_default_location == ""
        settings.reset("RUNTIME_DEFAULT_LOCATION")
        assert settings.runtime_default_location == "Seoul"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("MAX_OUTPUT_TOKENS", "127"),
        ("CHANNEL_CONTEXT_CHARS", "12001"),
        ("HISTORY_MAX_CHARS", "-1"),
        ("LORE_MAX_ITEMS", "21"),
        ("LORE_MAX_CHARS", "12001"),
        ("DM_ALWAYS_REPLY", "maybe"),
        ("CALL_PREFIXES", ""),
    ],
)
def test_runtime_setting_validation_rejects_invalid_values(key: str, value: str):
    store = Store(":memory:")
    try:
        settings = RuntimeSettings(_base(), store)
        with pytest.raises(ValueError):
            settings.set_text(key, value)
    finally:
        store.close()
