"""LLM orchestration, RP output policy, web search, and usage telemetry."""

import sys
from importlib import import_module

# Existing modules use local relative imports. Keep those imports readable while
# the shared primitives live in rio_bot.core.
for _name in (
    "admin_db",
    "config",
    "instructions",
    "lore",
    "routing",
    "runtime_knowledge",
    "store",
):
    _module = import_module(f"rio_bot.core.{_name}")
    sys.modules[f"{__name__}.{_name}"] = _module
    globals()[_name] = _module

del _name, _module
