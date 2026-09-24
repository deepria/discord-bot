# Structured memory shadow storage

`STRUCTURED_MEMORY_SHADOW=true` enables an asynchronous, write-only extraction pass after a
successfully delivered Discord response. It does not alter prompt assembly, existing summary
memory, routing, or the response sent to Discord.

## Stored data and ownership

`structured_memory_items` stores only an extractor-approved short summary plus its metadata:

- the user owner, origin realm/channel, capture visibility, item kind, disclosure, and confidence;
- source Discord message IDs and the matching Phase 1 `turn_id` values;
- lifecycle state: `active`, `superseded`, or `deleted`, with `superseded_by` where relevant.

The Phase 4 writer always creates `owner_type=user` and never creates shared or global items.
The store's active-candidate helper is deliberately unused by the answer path; it exists only so
the later evaluation phase can exclude superseded and deleted items consistently.

## Privacy and telemetry

The extraction instruction rejects inference and asks for an empty list when no safe durable item
exists. A deterministic backstop also rejects obvious credentials, financial/government identifiers,
health information, and precise addresses before any SQLite insert.

`structured_memory_shadow` events contain only `turn_id`, candidate/written counts, and fixed
rejection categories. They never include a memory value, source message text, prompt, or provider
response. A malformed provider response leaves the extraction cursor untouched; a safely rejected
candidate advances it so the same raw conversation is not repeatedly sent for extraction.

## Deployment and rollback

Before enabling the flag in production, stop the Bot and back up the SQLite database according to
the operating procedure. Startup adds nullable/additive columns to an existing database; it does not
rewrite or delete existing rows. Keep the flag `false` until the migration backup and a normal turn
check are complete. Setting the flag back to `false` immediately stops new shadow writes; it does
not change answer behavior in either direction.
