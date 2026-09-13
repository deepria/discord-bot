"""Offline lore/evaluation command-line tooling."""

from importlib import import_module
import sys

for _name in ("config", "lore", "routing", "store"):
    _module = import_module(f"hina_bot.core.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

_module = import_module("hina_bot.ai.llm")
sys.modules[f"{__name__}.llm"] = _module
globals()["llm"] = _module

del _name, _module
