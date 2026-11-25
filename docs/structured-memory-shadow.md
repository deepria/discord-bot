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

## Metadata evaluation readiness

`rio-memory-eval --db /path/to/rio.sqlite3` opens an existing database with SQLite
`mode=ro` and `query_only`. It never creates tables, imports runtime settings, calls
a provider, reads memory content, or enables retrieval. Its JSON report contains
lifecycle counts, active-item provenance completeness, and observation timestamps.
Source IDs and owners are not returned.

The default screening thresholds are 50 **active** items and at least 7 elapsed days
since the first valid active item. Deleted/superseded rows do not satisfy the sample
threshold. Blank, partial, or mismatched source links and invalid/future timestamps
block readiness. Thresholds can be set explicitly with `--min-items` and `--min-days`.

`ready_for_metadata_review` only permits further metadata assessment: elapsed time
does not prove continuous collection or representative samples. Quality, attribution,
privacy, deletion/correction behavior, extraction errors, and token/latency costs still
need separate evaluation. Content review requires its own authorization; retrieval
must remain off until that evaluation passes.


## Context and purpose policy v4

The `context-purpose-v4` extractor reads chronological user/Rio exchanges in the current
conversation, with one retained preceding exchange for interpretation only. Only complete new
user turns are eligible sources; the server rejects citations to context-only or omitted turns.
Standalone requests to stop a topic or analysis in the current conversation are omitted from
eligible evidence and counted as `temporary_conversation_boundary`. An ongoing interaction
boundary requires explicit evidence of its intended duration and scope. Quoted or narrated
turns, attributable third-party speech, and clearly temporary moods are ineligible evidence.
Input is bounded to 3,000–12,000 characters (using the configured history budget within these
bounds). Oversized exchanges are omitted as a whole, never cut into misleading evidence; a
batch with no usable evidence advances its shadow cursor without a provider request.

Message creation time (derived with discord.py) and database write time are distinct. No user
local timezone is inferred. The policy must abstain when relative dates or a statement's meaning
cannot be established. Normal Hangul is normalized to NFC at validation; this is not a semantic
repair or a guarantee that garbled model prose is detected deterministically.

The policy separates usefulness and durability from accuracy. One-off menu acceptance, criticism
of Rio's proposals, temporary work decisions and vague evaluations do not establish lasting
preferences. Assistant replies are untrusted context, never independent user evidence.
Shadow extraction operates independently of summary generation and the recall opt-in. Version/input-size telemetry contains
no conversation text or source IDs. Existing candidates are not re-extracted or rewritten.

## Provenance and revision contract

`verified_structured_memory(scope)` is the disclosure contract used by structured recall.
It requires an active user-owned item in the exact owner, realm, and origin channel. Each source
requires a retained user turn with matching Discord message and processing turn IDs. For public
capture, a linked shared call or an exportable turn must also attest to its visibility. Legacy
items with incomplete provenance and items whose source has aged out are excluded.

In a one-to-one DM, the owner can list verified items or confirm a revision. A correction uses
the latest retained authored user message as the replacement evidence. It seeds the summary
with that statement only. Revocation clears the legacy summary and advances a conversation
watermark. Older unsegmented history, replies and candidates remain outside the answer and
extraction paths for that conversation. New summaries are built from post-revision user turns,
with the confirmed correction as the initial summary when one exists. The revision applies to its
origin conversation. Previously copied text in another conversation has no source link for this
operation to follow. Notes and Discord messages have their own deletion controls.

The database adds a revision table on startup without backfilling existing memories.
`/memory clear` and admin purge remove the revision record, stored items and extraction cursor
within the selected scope. A summary committing after a revision or purge must still have the
same eligible source batch.

## Owner opt-in for one-to-one DM recall

The existing answer call can receive up to three verified facts or preferences with a combined
600-character content budget. Selection checks at most 32 active candidates and uses the
current question's authored first-person cues and matching terms or topics. A relevant recent
correction takes precedence over the structured candidates and the older summary. This feature
is available only in the exact one-to-one DM and only while the effective mode permits memory
reads. Turns with web search skip this recall path.

Each DM starts with recall disabled. The owner can check or change it with `/memory recall`.
Disabling recall returns the answer path to its existing summary and history. Clearing memory
and administrative purge also reset recall for the affected scopes. The setting is stored as a
personal preference in the bot database and managed from Discord.

The answer usage log records selected item count, selection time, and estimated input tokens
for the extra reference fields. It also records whether a recent correction replaced older context.
Actual provider token counts still come from the existing usage fields. Text, source identifiers,
and the contents of corrections are excluded from telemetry.

## Synthetic extraction evaluation

`rio-memory-extraction-eval --validate-only` checks fixtures without credentials, provider calls,
DB creation, or output artifacts. This is distinct from the metadata-only `rio-memory-eval` and
from `rio-eval`, which evaluates conversational answers.

For real extraction comparisons, run from the Bot checkout with the existing memory provider
credentials and explicit `MEMORY_MODEL` (or `--provider`/`--model` overrides):

```bash
python -m rio_bot.tooling.memory_extraction_eval --policy baseline --output data/evals/memory-baseline.jsonl
python -m rio_bot.tooling.memory_extraction_eval --policy current --output data/evals/memory-current.jsonl
```

These commands make paid provider calls using synthetic fixtures only. They use isolated in-memory
stores and do not log in to Discord, read production memory, or change production settings. Output
contains synthetic candidate summaries for manual review. Count checks (including positive controls)
are automatic, but matching counts never establish semantic correctness: every result is marked
`manual_review_required`. Missing memory model configuration fails rather than silently evaluating
the answer model. Provider failures are recorded by exception type only and do not abort other cases.
The current policy and the frozen v1 baseline are selectable in this evaluation tool; production always
uses the current policy. Add `--usage-log` for content-free token and latency comparison.

Legacy turns without an existing processing turn ID are ineligible new evidence and are counted
as `missing_turn_provenance`. They are not backfilled or silently converted into linked facts.
Their completed shadow batch advances without manufacturing IDs; historical candidate rows are
not changed.
