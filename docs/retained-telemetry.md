# Retained telemetry: trace 30 days / Usage 90 days

## Storage and compatibility

Bot writes a dedicated `data/telemetry.sqlite3` alongside the existing size-rotated JSONL.
`TELEMETRY_DB_PATH` overrides its path; an explicitly empty value disables retained writes.
The default follows the parent directory of `DATABASE_PATH`. It is independent of the memory DB.
New files use mode 0600. The store verifies application ID `0x52494F54` and schema version 1;
foreign databases and final-path symlinks are refused. Schema changes require a new contract.

Only `turn.started`, `turn.completed`, `turn.failed` and content-free provider `turn.usage`
links are retained as trace for 30 days. UUID turn IDs stay in this 30-day table.
The 90-day Usage table holds only answer-call time, provider/model, status, token total and
latency. It has no user/channel/turn ID, scope, content, prompt, provider body or exception text.
Summary/shadow extraction calls are excluded from the existing answer analytics contract.
Each logical UsageLogger answer row counts once, preserving existing retry accounting.
Usage facts retain the exact numeric samples needed for arbitrary time ranges and p50/p95;
this is not a new daily-rollup contract. Missing token totals remain unknown.

Existing JSONL files are neither migrated nor deleted. Their size-based rotation remains
unchanged. Those diagnostic copies are not subject to the new store's expiry, so this does
not implement a global maximum lifetime for every existing log or host backup.
No memory database, structured-memory policy, provider request or response behavior changes.
Write failures emit fixed warnings and do not fail Discord replies. Successful collection does
not certify that no writes were ever dropped; failures/rollbacks can create gaps.

## Expiry: preview first

After Bot deployment and at least one turn has created the store, on CT 101:

```bash
cd /opt/rio-discord-bot
.venv/bin/python -m rio_bot.tooling.telemetry_retention --db /opt/rio-discord-bot/data/telemetry.sqlite3
```

This is a read-only preview reporting expired row counts for each table. It does not create
a missing database or alter JSONL/memory. `--apply` expires only records strictly older than
now minus 30/90 elapsed days, normalized to UTC. Boundary records are retained. There is no
count/size cap that silently discards in-window rows. Future or invalid timestamps are never
accepted by the writer. Expiry verifies the dedicated database identity before any DELETE.
SQLite secure_delete is enabled for deletion; filesystem snapshots/backups are separate.
Space inside the database is reusable; deleting records need not shrink the file on disk.

Hourly scheduling is supplied but **not installed by a Git deployment**. Review the preview
and actual database path, then install the included units on CT 101:

```bash
install -m 0644 deploy/rio-telemetry-retention.service /etc/systemd/system/rio-telemetry-retention.service
install -m 0644 deploy/rio-telemetry-retention.timer /etc/systemd/system/rio-telemetry-retention.timer
systemctl daemon-reload
systemctl enable --now rio-telemetry-retention.timer
systemctl list-timers rio-telemetry-retention.timer --no-pager
```

The supplied unit assumes `/opt/rio-discord-bot/data/telemetry.sqlite3`; adjust it if overridden.
The timer runs hourly with up to two minutes jitter and catches missed runs. Physical expiry
can lag the cutoff until the next successful run; Agent excludes expired rows immediately at
query time. If the timer is absent/failing, coverage reports `cleanup_pending` when old rows
exist. A stopped host cannot guarantee cleanup or continued collection.

## Rollout and rollback

Deploy Bot -> Agent -> Console. Agent uses the same path via `RIO_TELEMETRY_DB_PATH`, opens
SQLite read-only and reports collection start, available record bounds and partial-query state.
It does not combine retained rows with the legacy tail, avoiding duplicate counts. Before the
new store exists it explicitly labels legacy results as partial; a corrupt retained store is
unavailable rather than silently replaced with legacy results.

Trace queries cover 30 days; Usage can cover 90 days. Query bounds and actual available data
are distinct. No retrospective 30/90-day history is claimed on installation, and old records are
not imported automatically. The Agent enforces a query-work budget and a 100,000-row Usage
limit, reporting truncation rather than complete totals when reached. Percentiles are exact
for the observed rows; a partial result is visibly labeled.

Rollback: disable the retention timer before reverting Bot code; empty TELEMETRY_DB_PATH
stops new writes. Revert Agent/Console as required to restore legacy-tail views. Do not delete
or overwrite the new database to roll back, and do not point its maintenance command at memory.

Acceptance requires local/CI tests plus deployed source readability, matching collection paths,
preview review and timer installation/status. A deployment record alone does not complete
30/90-day collection coverage or validate real memory/model quality.
