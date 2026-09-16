"""Rio Discord bot package."""

import sys
from importlib import abc, import_module, util

# Preserve the existing public LLM behavior after splitting implementation modules
# into feature-oriented subpackages.
from .ai import llm as _llm
from .ai.information_pipeline import LLM as _ChatLLM

_llm.LLM = _ChatLLM

# Temporary compatibility for the repository's existing flat import paths. Runtime
# entry points use the new package paths; these aliases keep scripts/tests and local
# integrations working while callers migrate naturally.
_LEGACY_MODULES = {
    "admin_db": "rio_bot.core.admin_db",
    "admin_list": "rio_bot.core.admin_list",
    "config": "rio_bot.core.config",
    "emojis": "rio_bot.core.emojis",
    "events": "rio_bot.core.events",
    "instructions": "rio_bot.core.instructions",
    "knowledge_ingest": "rio_bot.core.knowledge_ingest",
    "lore": "rio_bot.core.lore",
    "recent": "rio_bot.core.recent",
    "routing": "rio_bot.core.routing",
    "runtime_knowledge": "rio_bot.core.runtime_knowledge",
    "runtime_migration": "rio_bot.core.runtime_migration",
    "store": "rio_bot.core.store",
    "chat_llm": "rio_bot.ai.chat_llm",
    "chat_llm_v2": "rio_bot.ai.chat_llm_v2",
    "freshness": "rio_bot.ai.freshness",
    "information_pipeline": "rio_bot.ai.information_pipeline",
    "llm": "rio_bot.ai.llm",
    "request_assembly": "rio_bot.ai.request_assembly",
    "routing_plan": "rio_bot.ai.routing_plan",
    "rp_output_policy": "rio_bot.ai.rp_output_policy",
    "runtime_context": "rio_bot.ai.runtime_context",
    "runtime_llm": "rio_bot.ai.runtime_llm",
    "usage": "rio_bot.ai.usage",
    "web_search_runtime": "rio_bot.ai.web_search_runtime",
    "web_search_text": "rio_bot.ai.web_search_text",
    "bot": "rio_bot.discord.bot",
    "chatlog_commands": "rio_bot.discord.chatlog_commands",
    "emoji_commands": "rio_bot.discord.emoji_commands",
    "instruction_commands": "rio_bot.discord.instruction_commands",
    "knowledge_commands": "rio_bot.discord.knowledge_commands",
    "memory_commands": "rio_bot.discord.memory_commands",
    "output_safety": "rio_bot.discord.output_safety",
    "runtime_entry": "rio_bot.discord.runtime_entry",
    "slash_commands": "rio_bot.discord.slash_commands",
    "web_bot": "rio_bot.discord.web_bot",
    "eval_runner": "rio_bot.tooling.eval_runner",
    "lore_cli": "rio_bot.tooling.lore_cli",
    "lore_pipeline": "rio_bot.tooling.lore_pipeline",
    "lore_web": "rio_bot.tooling.lore_web",
}


class _LegacyAliasLoader(abc.Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return import_module(self.target)

    def exec_module(self, module):
        return None


class _LegacyAliasFinder(abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        prefix = __name__ + "."
        if not fullname.startswith(prefix):
            return None
        target_name = _LEGACY_MODULES.get(fullname[len(prefix):])
        if target_name is None:
            return None
        return util.spec_from_loader(fullname, _LegacyAliasLoader(target_name))


if not any(isinstance(finder, _LegacyAliasFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _LegacyAliasFinder())
