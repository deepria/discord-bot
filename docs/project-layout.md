# Project layout

Python sources are grouped by responsibility instead of being kept directly under `src/hina_bot`.

```text
src/hina_bot/
├── ai/          # Responses API orchestration, RP output policy, web search, usage telemetry
├── core/        # configuration, persistence, routing, memory/knowledge primitives
├── discord/     # Discord client, slash commands, output guards, runtime entry point
├── tooling/     # lore/evaluation CLI and offline pipelines
├── data/        # packaged lore data
└── prompts/     # packaged character/relationship prompts

tests/
├── ai/          # LLM/search/output-policy tests
├── core/        # state, lore, storage, migration tests
├── discord/     # Discord command/runtime integration tests
└── tooling/     # CLI/evaluation pipeline tests
```

The console entry points use the new package paths.  A small compatibility layer in `hina_bot.__init__`
keeps the previous flat imports (for example `hina_bot.config`) working for existing local scripts/tests;
new code should prefer the feature package paths such as `hina_bot.core.config`.
