import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from weakref import WeakSet

from .conversation_boundaries import is_standalone_boundary
from .memory_evidence import evidence_rejection, explicit_boundary
from .routing import Scope


@dataclass(eq=False)
class MemoryTurn:
    scope: Scope
    valid: bool = True


class Store:
    """Small single-process SQLite store. All calls run on the event-loop thread."""

    def __init__(self, path: str, history_turns: int = 12):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.history_turns = history_turns
        self._memory_turns: WeakSet[MemoryTurn] = WeakSet()
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE, turn_id TEXT,
                content TEXT NOT NULL, reply TEXT NOT NULL, exportable INTEGER NOT NULL,
                search_outcome TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS turns_scope ON turns(scope, id);
            CREATE INDEX IF NOT EXISTS turns_owner ON turns(realm, user_id);
            CREATE TABLE IF NOT EXISTS summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                text TEXT NOT NULL, through_id INTEGER NOT NULL, exportable INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope TEXT NOT NULL,
                realm TEXT NOT NULL, user_id TEXT NOT NULL, message_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS shared_calls_scope ON shared_calls(scope, id);
            CREATE INDEX IF NOT EXISTS shared_calls_owner_time ON shared_calls(user_id, created_at);
            CREATE TABLE IF NOT EXISTS shared_summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL, text TEXT NOT NULL, through_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS structured_memory_cursors (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                through_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS memory_revisions (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                turn_floor INTEGER NOT NULL, shared_floor INTEGER NOT NULL,
                replacement_id INTEGER, revised_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dm_memory_recall (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS structured_memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
                origin_realm TEXT NOT NULL, origin_channel_id TEXT NOT NULL,
                origin_public_at_capture INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('fact','event','preference','relationship','boundary','task')),
                content TEXT NOT NULL, disclosure TEXT NOT NULL CHECK(disclosure IN ('channel','owner_private')),
                source_message_ids TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                source_turn_ids TEXT NOT NULL DEFAULT '', owner_type TEXT NOT NULL DEFAULT 'user',
                status TEXT NOT NULL DEFAULT 'active', superseded_by INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS emoji_registry (
                alias TEXT PRIMARY KEY, emoji_id TEXT NOT NULL UNIQUE, description TEXT NOT NULL,
                source_guild_id TEXT
            );
            CREATE TABLE IF NOT EXISTS memory_modes (
                scope TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('normal','read_only','write_only','off'))
            );
            CREATE TABLE IF NOT EXISTS chat_log_modes (
                scope TEXT PRIMARY KEY,
                mode TEXT NOT NULL CHECK(mode IN ('on','off'))
            );
            CREATE TABLE IF NOT EXISTS policy_config_audit (
                id TEXT PRIMARY KEY,
                occurred_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actor_kind TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                policy TEXT NOT NULL,
                scope TEXT NOT NULL,
                outcome TEXT NOT NULL,
                request_id TEXT
            );
            CREATE TABLE IF NOT EXISTS policy_config_requests (
                request_id TEXT PRIMARY KEY,
                policy TEXT NOT NULL,
                scope TEXT NOT NULL,
                payload_digest TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manual_notes (
                scope TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT OR IGNORE INTO manual_notes(scope, text)
                SELECT scope, text FROM notes;
        """)
        self._ensure_column("turns", "turn_id", "TEXT")
        self._ensure_column("turns", "search_outcome", "TEXT")
        self._ensure_column("structured_memory_items", "source_turn_ids", "TEXT NOT NULL DEFAULT ''")
        self._ensure_column("structured_memory_items", "owner_type", "TEXT NOT NULL DEFAULT 'user'")
        self._ensure_column("structured_memory_items", "status", "TEXT NOT NULL DEFAULT 'active'")
        self._ensure_column("structured_memory_items", "superseded_by", "INTEGER")
        # SQLite only permits constant defaults in ALTER TABLE ADD COLUMN. New databases
        # receive the timestamp default from the CREATE statement; migrated rows are seeded here.
        self._ensure_column("structured_memory_items", "updated_at", "TEXT")
        self.db.execute(
            "UPDATE structured_memory_items SET updated_at=created_at "
            "WHERE updated_at IS NULL")
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS structured_memory_active_owner "
            "ON structured_memory_items(owner_id, origin_realm, status, id)")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {row["name"] for row in self.db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def checkpoint(self):
        """Flush this connection's WAL changes before the process exits."""
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()

    def close(self):
        self.db.close()

    def note(self, key: str) -> str:
        row = self.db.execute("SELECT text FROM manual_notes WHERE scope=?", (key,)).fetchone()
        if row:
            return row[0]
        row = self.db.execute("SELECT text FROM notes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else ""

    def set_note(self, key: str, text: str):
        with self.db:
            if text:
                self.db.execute(
                    "INSERT OR REPLACE INTO manual_notes(scope,text,updated_at) "
                    "VALUES (?,?,CURRENT_TIMESTAMP)", (key, text[:1500]))
                self.db.execute("DELETE FROM notes WHERE scope=?", (key,))
            else:
                self.db.execute("DELETE FROM manual_notes WHERE scope=?", (key,))
                self.db.execute("DELETE FROM notes WHERE scope=?", (key,))

    def summary(self, scope: Scope):
        row = self.db.execute("SELECT text, through_id FROM summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return (row[0], row[1]) if row else ("", 0)

    def _memory_revision(self, scope: Scope):
        row = self.db.execute(
            "SELECT turn_floor, shared_floor, replacement_id FROM memory_revisions WHERE scope=?",
            (scope.conversation,),
        ).fetchone()
        return tuple(row) if row else (0, 0, None)

    def memory_turn(self, scope: Scope) -> MemoryTurn:
        turn = MemoryTurn(scope)
        self._memory_turns.add(turn)
        return turn

    def _invalidate_memory_turns(self, matches):
        for turn in list(self._memory_turns):
            if matches(turn.scope):
                turn.valid = False

    def context_after_memory_revision(self, scope: Scope, rows: list[dict]) -> list[dict]:
        revision = self.db.execute(
            "SELECT revised_at FROM memory_revisions WHERE scope=?", (scope.conversation,),
        ).fetchone()
        if not revision:
            return rows
        selected = []
        for row in rows:
            timestamp = row.get("unix_time")
            if timestamp is None:
                try:
                    at = datetime.fromisoformat(row.get("at", ""))
                    timestamp = at.timestamp() if at.tzinfo is not None else None
                except (ValueError, TypeError):
                    continue
            if isinstance(timestamp, (int, float)) and timestamp > revision["revised_at"]:
                selected.append(row)
        return selected

    def history(self, scope: Scope):
        return list(reversed(self.db.execute(
            "SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id DESC LIMIT ?",
            (scope.conversation, self._memory_revision(scope)[0], self.history_turns)).fetchall()))

    def pending(self, scope: Scope):
        _, through = self.summary(scope)
        return self.db.execute("SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id",
                               (scope.conversation, max(through, self._memory_revision(scope)[0]))).fetchall()

    def seen(self, message_id: int):
        return self.db.execute("SELECT 1 FROM turns WHERE message_id=?",
                               (str(message_id),)).fetchone() is not None

    def last_search_outcome(self, scope: Scope) -> str | None:
        row = self.db.execute(
            "SELECT search_outcome FROM turns WHERE scope=? AND user_id=? ORDER BY id DESC LIMIT 1",
            (scope.conversation, str(scope.user_id)),
        ).fetchone()
        return row["search_outcome"] if row else None

    def invalidate_last_search_outcome(self, scope: Scope) -> None:
        """An untracked reply must supersede any older recorded search outcome."""
        with self.db:
            self.db.execute(
                "UPDATE turns SET search_outcome=NULL WHERE search_outcome IS NOT NULL AND id=("
                "SELECT id FROM turns WHERE scope=? AND user_id=? ORDER BY id DESC LIMIT 1)",
                (scope.conversation, str(scope.user_id)),
            )

    def add(self, scope: Scope, message_id: int, content: str, reply: str,
            *, turn_id: str | None = None, search_outcome: str | None = None):
        exportable = scope.public_at_capture and self.summary_exportable(scope)
        exportable = exportable and all(row["exportable"] for row in self.history(scope))
        with self.db:
            self.db.execute(
                "INSERT INTO turns(scope,realm,user_id,message_id,turn_id,content,reply,exportable,"
                "search_outcome) VALUES (?,?,?,?,?,?,?,?,?)",
                (scope.conversation, scope.realm, str(scope.user_id), str(message_id), turn_id,
                 content, reply, int(exportable), search_outcome),
            )
            # Bound raw retention even if the summary API keeps failing.
            self.db.execute("DELETE FROM turns WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def save_summary(self, scope: Scope, text: str, through: int):
        floor = self._memory_revision(scope)[0]
        if floor and through <= floor:
            return
        exportable = self.summary_exportable(scope) and all(
            row["exportable"] for row in self.pending(scope) if row["id"] <= through)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO summaries VALUES (?,?,?,?,?,?)",
                            (scope.conversation, scope.realm, str(scope.user_id), text, through,
                             int(exportable)))

    def summary_exportable(self, scope: Scope):
        row = self.db.execute("SELECT exportable FROM summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return row is None or bool(row[0])

    def structured_memory_cursor(self, scope: Scope) -> int:
        row = self.db.execute("SELECT through_id FROM structured_memory_cursors WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return int(row[0]) if row else 0

    def pending_structured_memory(self, scope: Scope, *, limit: int = 8):
        if not 2 <= limit <= 8:
            raise ValueError("structured memory batch limit must be 2~8")
        return self.db.execute("SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id LIMIT ?",
                               (scope.conversation, max(self.structured_memory_cursor(scope),
                                                        self._memory_revision(scope)[0]), limit)).fetchall()

    def structured_memory_context(self, scope: Scope, *, before_id: int):
        """One retained preceding exchange, for interpretation only, never new evidence."""
        return self.db.execute(
            "SELECT * FROM turns WHERE scope=? AND id>? AND id<? ORDER BY id DESC LIMIT 1",
            (scope.conversation, self._memory_revision(scope)[0], before_id)).fetchall()

    def summary_batch_is_current(self, scope: Scope, pending, *, shared: bool = False) -> bool:
        current = self.pending_shared(scope) if shared else self.pending(scope)
        ids = {row["id"] for row in current}
        return all(row["id"] in ids for row in pending)

    def save_structured_memory(self, scope: Scope, *, through_id: int, items: list[dict]):
        pending = self.pending_structured_memory(scope)
        sources = {str(row["message_id"]): row for row in pending if row["id"] <= through_id}
        if through_id not in {int(row["id"]) for row in pending}:
            raise ValueError("structured memory cursor must end inside the pending batch")
        normalized = [self._validate_structured_item(scope, item, sources) for item in items]
        accepted, rejected = [], {}
        for item in normalized:
            reason = self._structured_memory_rejection_reason(item["content"])
            source_rows = [sources[value] for value in item["source_message_ids"]]
            if not reason:
                reason = self._evidence_rejection(item["kind"], source_rows)
            if not reason and scope.public_at_capture and (
                scope.guild_id is None or not all(self._public_source(row) for row in source_rows)
            ):
                reason = "unverified_capture_visibility"
            if reason:
                rejected[reason] = rejected.get(reason, 0) + 1
            else:
                accepted.append(item)
        turn_ids = {
            str(row["message_id"]): row["turn_id"]
            for row in pending if row["turn_id"]
        }
        with self.db:
            for item in accepted:
                self._insert_structured_memory(scope, item, turn_ids)
            self.db.execute(
                "INSERT INTO structured_memory_cursors(scope,realm,user_id,through_id,updated_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(scope) DO UPDATE SET "
                "through_id=excluded.through_id,updated_at=CURRENT_TIMESTAMP",
                (scope.conversation, scope.realm, str(scope.user_id), through_id),
            )
        return {"candidates": len(normalized), "written": len(accepted), "rejected": rejected}

    @staticmethod
    def _evidence_rejection(kind, rows):
        for row in rows:
            if not row["turn_id"]:
                return "missing_turn_provenance"
            if is_standalone_boundary(row["content"]):
                return "temporary_conversation_boundary"
            reason = evidence_rejection(row["content"])
            if reason:
                return reason
        if kind == "boundary" and not any(explicit_boundary(row["content"]) for row in rows):
            return "missing_durable_boundary"
        return None

    def _insert_structured_memory(self, scope, item, turn_ids):
        cursor = self.db.execute(
            "INSERT INTO structured_memory_items(owner_id,origin_realm,origin_channel_id,"
            "origin_public_at_capture,kind,content,disclosure,source_message_ids,confidence,"
            "source_turn_ids,owner_type,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(scope.user_id), scope.realm, str(scope.channel_id), int(scope.public_at_capture),
             item["kind"], item["content"], item["disclosure"],
             ",".join(item["source_message_ids"]), item["confidence"],
             ",".join(turn_ids[message_id] for message_id in item["source_message_ids"]),
             "user", "active"),
        )
        return cursor.lastrowid

    def _public_source(self, row) -> bool:
        return bool(row["exportable"] or self.db.execute(
            "SELECT 1 FROM shared_calls WHERE scope=? AND user_id=? AND message_id=?",
            (row["scope"], row["user_id"], row["message_id"]),
        ).fetchone())

    @staticmethod
    def _structured_memory_rejection_reason(content: str) -> str | None:
        """Block obvious high-risk values before durable storage.

        This is a deliberately conservative backstop for the extractor policy, not a classifier.
        Rejected items are never written and only their category is suitable for telemetry.
        """
        value = content.casefold()
        restricted = {
            "credential": ("password", "passwd", "api key", "access token", "인증 토큰", "비밀번호", "비밀키"),
            "financial": ("card number", "credit card", "계좌번호", "카드번호", "주민등록번호"),
            "health": ("diagnosis", "medical record", "정신과", "병력", "진단"),
            "precise_location": ("home address", "street address", "집 주소", "자택 주소", "상세 주소"),
        }
        for reason, terms in restricted.items():
            if any(term in value for term in terms):
                return reason
        return None

    @staticmethod
    def _validate_structured_item(scope: Scope, item: dict, sources: set[str]) -> dict:
        if not isinstance(item, dict):
            raise TypeError("structured memory item must be an object")
        kind, content = item.get("kind"), item.get("content")
        source_ids, confidence, disclosure = item.get("source_message_ids"), item.get("confidence"), item.get("disclosure")
        if kind not in {"fact", "event", "preference", "relationship", "boundary", "task"}:
            raise ValueError("invalid structured memory kind")
        if isinstance(content, str):
            content = unicodedata.normalize("NFC", content)
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 600:
            raise ValueError("structured memory content must be 1~600 chars")
        if not isinstance(source_ids, list) or not source_ids or len(source_ids) > 8:
            raise ValueError("structured memory item requires 1~8 source message ids")
        source_ids = [str(value) for value in source_ids]
        if len(source_ids) != len(set(source_ids)) or any(value not in sources for value in source_ids):
            raise ValueError("structured memory sources must belong to the pending scope batch")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise ValueError("structured memory confidence must be 0~1")
        expected = "channel" if scope.public_at_capture else "owner_private"
        if disclosure != expected:
            raise ValueError("structured memory disclosure does not match capture scope")
        return {"kind": kind, "content": unicodedata.normalize("NFC", content.strip()), "disclosure": disclosure,
                "source_message_ids": source_ids, "confidence": float(confidence)}

    def active_structured_memory(self, scope: Scope):
        """Inventory for shadow evaluation. Use verified_structured_memory for disclosure."""
        return self.db.execute(
            "SELECT * FROM structured_memory_items WHERE owner_id=? AND origin_realm=? "
            "AND status='active' ORDER BY id DESC",
            (str(scope.user_id), scope.realm),
        ).fetchall()

    def _owned_structured_item(self, scope: Scope, item_id: int):
        return self.db.execute(
            "SELECT * FROM structured_memory_items WHERE id=? AND owner_id=? AND origin_realm=? "
            "AND origin_channel_id=? AND owner_type='user' AND status='active'",
            (item_id, str(scope.user_id), scope.realm, str(scope.channel_id)),
        ).fetchone()

    def _verified_sources(self, scope, item):
        message_ids = item["source_message_ids"].split(",")
        turn_ids = item["source_turn_ids"].split(",")
        floor, _, replacement_id = self._memory_revision(scope)
        if (
            len(message_ids) != len(turn_ids) or not 1 <= len(message_ids) <= 8
            or len(set(message_ids)) != len(message_ids) or not all(message_ids) or not all(turn_ids)
            or item["origin_public_at_capture"] not in (0, 1)
            or self._structured_memory_rejection_reason(item["content"])
        ):
            return []
        marks = ",".join("?" for _ in message_ids)
        sources = {
            row["message_id"]: row for row in self.db.execute(
                f"SELECT t.*, EXISTS(SELECT 1 FROM shared_calls c WHERE c.scope=t.scope "
                f"AND c.message_id=t.message_id AND c.user_id=t.user_id) AS shared "
                f"FROM turns t WHERE t.scope=? AND t.realm=? AND t.user_id=? "
                f"AND t.message_id IN ({marks})",
                (scope.conversation, scope.realm, str(scope.user_id), *message_ids),
            )
        }
        public = item["origin_public_at_capture"] == 1
        if item["disclosure"] != ("channel" if public else "owner_private"):
            return []
        if public and scope.guild_id is None:
            return []
        verified = []
        for message_id, turn_id in zip(message_ids, turn_ids):
            row = sources.get(message_id)
            if (
                row is None or row["turn_id"] != turn_id
                or (row["id"] <= floor and item["id"] != replacement_id)
                or (public and not (row["exportable"] or row["shared"]))
            ):
                return []
            verified.append(row)
        return [] if self._evidence_rejection(item["kind"], verified) else verified

    def verified_structured_memory(self, scope: Scope, *, limit: int = 12,
                                   scan_limit: int | None = None):
        """Disclose candidates only in the owning conversation with retained source pairs."""
        if limit < 1:
            return []
        query = (
            "SELECT * FROM structured_memory_items WHERE owner_id=? AND origin_realm=? "
            "AND origin_channel_id=? AND owner_type='user' AND status='active'"
        )
        params = (str(scope.user_id), scope.realm, str(scope.channel_id))
        if scan_limit is None:
            rows = self.db.execute(query + " ORDER BY id DESC", params)
        else:
            replacement = self.confirmed_memory_replacement(scope)
            rows = self.db.execute(
                query + " ORDER BY (id=?) DESC, id DESC LIMIT ?",
                (*params, replacement, max(0, scan_limit)),
            )
        verified = []
        for row in rows:
            if self._verified_sources(scope, row):
                verified.append(row)
                if len(verified) >= limit:
                    break
        return verified

    def dm_structured_recall_enabled(self, scope: Scope) -> bool:
        if scope.guild_id is not None:
            return False
        return self.db.execute(
            "SELECT 1 FROM dm_memory_recall WHERE scope=? AND user_id=?",
            (scope.conversation, str(scope.user_id)),
        ).fetchone() is not None

    def set_dm_structured_recall(self, scope: Scope, enabled: bool) -> None:
        if scope.guild_id is not None:
            raise ValueError("Structured recall can only be configured in a DM")
        with self.db:
            if enabled:
                self.db.execute(
                    "INSERT OR IGNORE INTO dm_memory_recall VALUES (?,?,?)",
                    (scope.conversation, scope.realm, str(scope.user_id)),
                )
            else:
                self.db.execute("DELETE FROM dm_memory_recall WHERE scope=?", (scope.conversation,))

    def confirmed_memory_replacement(self, scope: Scope) -> int | None:
        return self._memory_revision(scope)[2]

    def _revise_memory(self, scope, replacement_id=None, *, correction=""):
        turn_floor = self.db.execute(
            "SELECT COALESCE(MAX(id),0) FROM turns WHERE scope=?", (scope.conversation,),
        ).fetchone()[0]
        shared_floor = self.db.execute(
            "SELECT COALESCE(MAX(id),0) FROM shared_calls WHERE scope=?", (scope.conversation,),
        ).fetchone()[0]
        old_turn_floor, old_shared_floor, _ = self._memory_revision(scope)
        turn_floor, shared_floor = max(turn_floor, old_turn_floor), max(shared_floor, old_shared_floor)
        self.db.execute(
            "INSERT OR REPLACE INTO memory_revisions VALUES (?,?,?,?,?,?,?)",
            (scope.conversation, scope.realm, str(scope.user_id), turn_floor, shared_floor,
             replacement_id, datetime.now(UTC).timestamp()),
        )
        for table in ("summaries", "shared_summaries"):
            self.db.execute(f"DELETE FROM {table} WHERE scope=?", (scope.conversation,))
        if correction:
            # Seed from the newly confirmed user utterance only, never from its old reply.
            self.db.execute(
                "INSERT INTO summaries VALUES (?,?,?,?,?,0)",
                (scope.conversation, scope.realm, str(scope.user_id), correction, turn_floor),
            )

    def supersede_structured_memory(self, scope: Scope, item_id: int, replacement_id: int) -> bool:
        original = self._owned_structured_item(scope, item_id)
        replacement = self._owned_structured_item(scope, replacement_id)
        if original is None or replacement is None or replacement["kind"] != original["kind"]:
            return False
        original_sources = self._verified_sources(scope, original)
        replacement_sources = self._verified_sources(scope, replacement)
        if not original_sources or not replacement_sources or (
            min(row["id"] for row in replacement_sources) <= max(row["id"] for row in original_sources)
        ):
            return False
        with self.db:
            self.db.execute(
                "UPDATE structured_memory_items SET status='superseded', superseded_by=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (replacement_id, item_id),
            )
            self._revise_memory(scope, replacement_id)
        self._invalidate_memory_turns(lambda active: active.conversation == scope.conversation)
        return True

    def correct_structured_memory(self, scope: Scope, item_id: int, message_id: str) -> bool:
        """Replace a DM item from the latest user-authored, owner-confirmed source."""
        original = self._owned_structured_item(scope, item_id)
        if scope.guild_id is not None or original is None:
            return False
        original_sources = self._verified_sources(scope, original)
        turns = self.history(scope)
        if not original_sources or not turns:
            return False
        source = turns[-1]
        if (
            source["message_id"] != message_id or source["id"] <= max(row["id"] for row in original_sources)
            or self._evidence_rejection(original["kind"], [source])
            or self._structured_memory_rejection_reason(source["content"])
            or not 1 <= len(source["content"].strip()) <= 600
            or source["content"].strip() == original["content"]
        ):
            return False
        item = self._validate_structured_item(scope, {
            "kind": original["kind"], "content": source["content"], "source_message_ids": [message_id],
            "disclosure": "owner_private", "confidence": 1.0,
        }, {message_id})
        with self.db:
            replacement_id = self._insert_structured_memory(scope, item, {message_id: source["turn_id"]})
            self.db.execute(
                "UPDATE structured_memory_items SET status='superseded', superseded_by=?, "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?", (replacement_id, item_id),
            )
            self._revise_memory(scope, replacement_id, correction=item["content"])
        self._invalidate_memory_turns(lambda active: active.conversation == scope.conversation)
        return True

    def delete_structured_memory(self, scope: Scope, item_id: int) -> bool:
        if self._owned_structured_item(scope, item_id) is None:
            return False
        with self.db:
            self.db.execute(
                "UPDATE structured_memory_items SET status='deleted', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (item_id,),
            )
            self._revise_memory(scope)
        self._invalidate_memory_turns(lambda active: active.conversation == scope.conversation)
        return True

    def forget(self, scope: Scope):
        """Delete this user's history and manual notes across channels in the current realm."""
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                self.db.execute(f"DELETE FROM {table} WHERE realm=? AND user_id=?",
                                (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM structured_memory_items WHERE origin_realm=? AND owner_id=?",
                            (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM structured_memory_cursors WHERE realm=? AND user_id=?",
                            (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM memory_revisions WHERE realm=? AND user_id=?",
                            (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM dm_memory_recall WHERE realm=? AND user_id=?",
                            (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM manual_notes WHERE scope=?", (scope.user_note,))
            self.db.execute("DELETE FROM notes WHERE scope=?", (scope.user_note,))
        self._invalidate_memory_turns(lambda active: active.user_note == scope.user_note)

    @staticmethod
    def _rowcount(cursor) -> int:
        return max(0, int(cursor.rowcount or 0))

    def purge_channel_memory(self, scope: Scope) -> int:
        """Delete persistent user memory for every user in one channel."""
        prefix = scope.channel + ":user:%"
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries",
                          "structured_memory_cursors", "memory_revisions", "dm_memory_recall"):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE scope LIKE ?", (prefix,))
                deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM structured_memory_items WHERE origin_realm=? AND origin_channel_id=?",
                (scope.realm, str(scope.channel_id)))
            deleted += self._rowcount(cursor)
        self._invalidate_memory_turns(lambda active: active.channel == scope.channel)
        return deleted

    def purge_realm_memory(self, scope: Scope) -> int:
        """Delete all user-owned persistent memory in a guild/realm, preserving realm notes."""
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE realm=?", (scope.realm,))
                deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM structured_memory_items WHERE origin_realm=?",
                                     (scope.realm,))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM structured_memory_cursors WHERE realm=?", (scope.realm,))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM memory_revisions WHERE realm=?", (scope.realm,))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM dm_memory_recall WHERE realm=?", (scope.realm,))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM manual_notes WHERE scope LIKE ?", (scope.realm + ":user:%",))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM notes WHERE scope LIKE ?", (scope.realm + ":user:%",))
            deleted += self._rowcount(cursor)
        self._invalidate_memory_turns(lambda active: active.realm == scope.realm)
        return deleted

    def purge_all_memory(self) -> int:
        """Delete all user-owned persistent memory while preserving config and shared realm notes."""
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                cursor = self.db.execute(f"DELETE FROM {table}")
                deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM structured_memory_items")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM structured_memory_cursors")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM memory_revisions")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM dm_memory_recall")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM manual_notes WHERE instr(scope, ':user:') > 0")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM notes WHERE instr(scope, ':user:') > 0")
            deleted += self._rowcount(cursor)
        self._invalidate_memory_turns(lambda _active: True)
        return deleted

    def add_shared_call(self, scope, message_id, name, content):
        if scope.guild_id is None or not scope.public_at_capture:
            return
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO shared_calls "
                            "(scope,realm,user_id,message_id,name,content) VALUES (?,?,?,?,?,?)",
                            (scope.conversation, scope.realm, str(scope.user_id), str(message_id),
                             name[:100], content))
            self.db.execute("DELETE FROM shared_calls WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM shared_calls WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def shared_summary(self, scope):
        row = self.db.execute("SELECT text,through_id FROM shared_summaries WHERE scope=?",
                              (scope.conversation,)).fetchone()
        return (row[0], row[1]) if row else ("", 0)

    def pending_shared(self, scope):
        return self.db.execute("SELECT * FROM shared_calls WHERE scope=? AND id>? ORDER BY id",
                               (scope.conversation, max(self.shared_summary(scope)[1],
                                                        self._memory_revision(scope)[1]))).fetchall()

    def save_shared_summary(self, scope, name, text, through):
        floor = self._memory_revision(scope)[1]
        if floor and through <= floor:
            return
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO shared_summaries VALUES (?,?,?,?,?,?)",
                            (scope.conversation, scope.realm, str(scope.user_id), name,
                             text[:1500], through))

    def public_candidates(self, user_id: int, guild_id: int | None = None):
        # Server queries include all speakers in this guild. DM queries include only its owner.
        condition, value = (("realm=?", f"guild:{guild_id}") if guild_id is not None
                            else ("user_id=?", str(user_id)))
        rows = self.db.execute(
            "SELECT scope, user_id, MAX(id) AS recent FROM shared_calls WHERE " + condition +
            " AND id>COALESCE((SELECT shared_floor FROM memory_revisions m "
            "WHERE m.scope=shared_calls.scope),0)"
            " GROUP BY scope,user_id ORDER BY recent DESC LIMIT 30", (value,)).fetchall()
        return [Scope(int(r["scope"].split(":")[1]), int(r["scope"].split(":")[3]),
                      int(r["user_id"])) for r in rows]

    def recent_public_sources(self, user_id: int, *, since: str, until: str) -> list[Scope]:
        rows = self.db.execute(
            "SELECT scope FROM shared_calls WHERE user_id=? AND realm LIKE 'guild:%' "
            "AND created_at BETWEEN ? AND ? "
            "AND id>COALESCE((SELECT shared_floor FROM memory_revisions m "
            "WHERE m.scope=shared_calls.scope),0) GROUP BY scope "
            "ORDER BY MAX(created_at) DESC, MAX(id) DESC LIMIT 30",
            (str(user_id), since, until),
        ).fetchall()
        return [Scope(int(row["scope"].split(":")[1]), int(row["scope"].split(":")[3]), user_id)
                for row in rows]

    def recent_public_turns(self, source: Scope, user_id: int, *, since: str, until: str):
        if source.guild_id is None or source.user_id != user_id:
            return []
        # A shared call is the capture-time proof that the user's own input was public.
        # The reply has a separate export check since it may depend on earlier turns.
        return self.db.execute(
            "SELECT c.message_id, c.created_at, c.content, t.reply FROM shared_calls c "
            "LEFT JOIN turns t ON t.message_id=c.message_id AND t.scope=c.scope "
            "AND t.user_id=c.user_id AND t.exportable=1 "
            "WHERE c.scope=? AND c.realm=? AND c.user_id=? AND c.created_at BETWEEN ? AND ? "
            "AND c.id>? "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT 2",
            (source.conversation, source.realm, str(user_id), since, until,
             self._memory_revision(source)[1]),
        ).fetchall()

    def public_context(self, allowed_scopes):
        context = []
        for source in allowed_scopes[:4]:
            rows = self.db.execute(
                "SELECT * FROM shared_calls WHERE scope=? AND id>? ORDER BY id DESC LIMIT 2",
                (source.conversation, self._memory_revision(source)[1]),
            ).fetchall()
            context.append({"source": source.conversation, "user_id": str(source.user_id),
                            "name": rows[0]["name"] if rows else "",
                            "summary": self.shared_summary(source)[0],
                            "recent_user_messages": [r["content"] for r in reversed(rows)]})
        return context

    def emoji_rows(self):
        return self.db.execute("SELECT * FROM emoji_registry ORDER BY alias").fetchall()

    def add_emoji(self, alias, emoji_id, description, source_guild_id=None):
        with self.db:
            self.db.execute("INSERT INTO emoji_registry VALUES (?,?,?,?)",
                            (alias, emoji_id, description, source_guild_id))

    def edit_emoji(self, alias, description):
        with self.db:
            return self.db.execute("UPDATE emoji_registry SET description=? WHERE alias=?",
                                   (description, alias)).rowcount > 0

    def remove_emoji(self, alias):
        with self.db:
            return self.db.execute("DELETE FROM emoji_registry WHERE alias=?", (alias,)).rowcount > 0

    def memory_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM memory_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def memory_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        global_mode = self.memory_mode_override("global")
        server_mode = self.memory_mode_override(scope.realm) if scope.guild_id is not None else None
        channel_mode = self.memory_mode_override(scope.channel)
        if channel_mode is not None:
            effective, source = channel_mode, "channel"
        elif server_mode is not None:
            effective, source = server_mode, "server"
        elif global_mode is not None:
            effective, source = global_mode, "global"
        else:
            effective, source = "normal", "default"
        return {
            "global": global_mode,
            "server": server_mode,
            "channel": channel_mode,
            "effective": effective,
            "source": source,
        }

    def memory_mode(self, scope: Scope) -> str:
        return str(self.memory_mode_chain(scope)["effective"])

    def set_memory_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"normal", "read_only", "write_only", "off"}:
            raise ValueError("Invalid memory mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM memory_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO memory_modes VALUES (?,?)", (key, mode))

    def set_memory_mode(self, scope: Scope, mode: str):
        """Backward-compatible channel override setter."""
        self.set_memory_mode_override(scope.channel, mode)

    def memory_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM memory_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}

    def chat_log_mode_override(self, key: str) -> str | None:
        row = self.db.execute("SELECT mode FROM chat_log_modes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else None

    def chat_log_mode_chain(self, scope: Scope) -> dict[str, str | None]:
        global_mode = self.chat_log_mode_override("global")
        server_mode = self.chat_log_mode_override(scope.realm) if scope.guild_id is not None else None
        channel_mode = self.chat_log_mode_override(scope.channel)
        if channel_mode is not None:
            effective, source = channel_mode, "channel"
        elif server_mode is not None:
            effective, source = server_mode, "server"
        elif global_mode is not None:
            effective, source = global_mode, "global"
        else:
            effective, source = "on", "default"
        return {
            "global": global_mode,
            "server": server_mode,
            "channel": channel_mode,
            "effective": effective,
            "source": source,
        }

    def chat_log_enabled(self, scope: Scope) -> bool:
        return self.chat_log_mode_chain(scope)["effective"] == "on"

    def set_chat_log_mode_override(self, key: str, mode: str | None):
        if mode is not None and mode not in {"on", "off"}:
            raise ValueError("Invalid chat log mode")
        with self.db:
            if mode is None:
                self.db.execute("DELETE FROM chat_log_modes WHERE scope=?", (key,))
            else:
                self.db.execute("INSERT OR REPLACE INTO chat_log_modes VALUES (?,?)", (key, mode))

    def chat_log_mode_overrides(self) -> dict[str, str]:
        rows = self.db.execute("SELECT scope,mode FROM chat_log_modes ORDER BY scope").fetchall()
        return {row["scope"]: row["mode"] for row in rows}
