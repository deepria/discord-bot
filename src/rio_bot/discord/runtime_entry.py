import logging

from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store

from . import web_bot
from .config_commands import ConfigCommands


def main():
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("rio").setLevel(logging.INFO)
    try:
        base_settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None

    store = Store(base_settings.db_path, base_settings.history_turns)
    settings = RuntimeSettings(base_settings, store)
    llm = LLM(settings)
    bot = web_bot.RioClient(settings, store=store, llm=llm)
    bot.tree.add_command(ConfigCommands(bot))
    bot.run(settings.discord_token, log_handler=None)
