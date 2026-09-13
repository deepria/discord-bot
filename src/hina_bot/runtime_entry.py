from . import web_bot
from .runtime_llm import LLM


def main():
    web_bot.LLM = LLM
    web_bot.main()
