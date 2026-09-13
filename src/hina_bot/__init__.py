"""Hina Discord bot."""

# Keep the large base prompt/summary implementation in llm.py while routing all public LLM imports
# through the chat-aware subclass. This avoids duplicating the memory and safety code while letting
# chat-specific retrieval/search policy evolve independently.
from . import llm as _llm
from .chat_llm import LLM as _ChatLLM

_llm.LLM = _ChatLLM
