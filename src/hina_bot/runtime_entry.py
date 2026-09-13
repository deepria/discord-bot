from . import web_bot
from .chat_llm_v2 import LLM


def main():
    web_bot.LLM = LLM
    web_bot.main()
