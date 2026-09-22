import asyncio
import logging
import signal

from rio_bot.ai.runtime_llm import LLM
from rio_bot.core.config import Settings
from rio_bot.core.runtime_config import RuntimeSettings
from rio_bot.core.store import Store

from . import web_bot
from .config_commands import ConfigCommands


def install_sigterm_handler(bot):
    """Ask Discord's event loop to close cleanly when a container sends SIGTERM."""
    previous = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum, _frame):
        loop = getattr(bot, "loop", None)
        is_running = getattr(loop, "is_running", lambda: False)
        if loop is None or not is_running() or bot.is_closed():
            return

        def begin_shutdown():
            # Mark this before spawning the close coroutine so no later dispatched message starts.
            bot.stopping = True
            asyncio.create_task(bot.close())

        loop.call_soon_threadsafe(begin_shutdown)

    signal.signal(signal.SIGTERM, handle_sigterm)
    return previous


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
    install_sigterm_handler(bot)
    bot.run(settings.discord_token, log_handler=None)
