import logging

from .bot import HinaClient as BaseHinaClient
from .chat_llm import LLM
from .config import Settings

log = logging.getLogger("hina")


class HinaClient(BaseHinaClient):
    """Discord client wired to the chat-time lore/web-search LLM."""

    def __init__(self, settings: Settings, *, store=None, llm=None):
        if llm is None:
            llm = LLM(settings)
        super().__init__(settings, store=store, llm=llm)


def main():
    # Do not log SDK request bodies, prompts, credentials, or Discord message content.
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    log.setLevel(logging.INFO)
    try:
        settings = Settings.load()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    bot = HinaClient(settings)
    bot.run(settings.discord_token, log_handler=None)


if __name__ == "__main__":
    main()
