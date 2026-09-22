import sqlite3
from pathlib import Path

from .routing import Scope


class Store:
    """Small single-process SQLite store. All calls run on the event-loop thread."""

    def __init__(self, path: str, history_turns: int = 12):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.history_turns = history_turns
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA busy_timeout=5000;
            PRAGMA secure_delete=ON;
            CREATE TABLE IF NOT EXISTS turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope TEXT NOT NULL, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                message_id TEXT NOT NULL UNIQUE,
                content TEXT NOT NULL, reply TEXT NOT NULL, exportable INTEGER NOT NULL,
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
            CREATE TABLE IF NOT EXISTS shared_summaries (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                name TEXT NOT NULL, text TEXT NOT NULL, through_id INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS structured_memory_cursors (
                scope TEXT PRIMARY KEY, realm TEXT NOT NULL, user_id TEXT NOT NULL,
                through_id INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS structured_memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL,
                origin_realm TEXT NOT NULL, origin_channel_id TEXT NOT NULL,
                origin_public_at_capture INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK(kind IN ('fact','event','preference','relationship','boundary','task')),
                content TEXT NOT NULL, disclosure TEXT NOT NULL CHECK(disclosure IN ('channel','owner_private')),
                source_message_ids TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    def history(self, scope: Scope):
        return list(reversed(self.db.execute(
            "SELECT * FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?",
            (scope.conversation, self.history_turns)).fetchall()))

    def pending(self, scope: Scope):
        _, through = self.summary(scope)
        return self.db.execute("SELECT * FROM turns WHERE scope=? AND id>? ORDER BY id",
                               (scope.conversation, through)).fetchall()

    def seen(self, message_id: int):
        return self.db.execute("SELECT 1 FROM turns WHERE message_id=?",
                               (str(message_id),)).fetchone() is not None

    def add(self, scope: Scope, message_id: int, content: str, reply: str):
        exportable = scope.public_at_capture and self.summary_exportable(scope)
        exportable = exportable and all(row["exportable"] for row in self.history(scope))
        with self.db:
            self.db.execute("INSERT INTO turns(scope,realm,user_id,message_id,content,reply,exportable) "
                            "VALUES (?,?,?,?,?,?,?)", (scope.conversation, scope.realm,
                            str(scope.user_id), str(message_id), content, reply, int(exportable)))
            # Bound raw retention even if the summary API keeps failing.
            self.db.execute("DELETE FROM turns WHERE scope=? AND id NOT IN "
                            "(SELECT id FROM turns WHERE scope=? ORDER BY id DESC LIMIT ?)",
                            (scope.conversation, scope.conversation, self.history_turns))

    def save_summary(self, scope: Scope, text: str, through: int):
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
                               (scope.conversation, self.structured_memory_cursor(scope), limit)).fetchall()

    def save_structured_memory(self, scope: Scope, *, through_id: int, items: list[dict]):
        pending = self.pending_structured_memory(scope)
        sources = {str(row["message_id"]) for row in pending}
        if through_id not in {int(row["id"]) for row in pending}:
            raise ValueError("structured memory cursor must end inside the pending batch")
        normalized = [self._validate_structured_item(scope, item, sources) for item in items]
        with self.db:
            for item in normalized:
                self.db.execute(
                    "INSERT INTO structured_memory_items(owner_id,origin_realm,origin_channel_id,"
                    "origin_public_at_capture,kind,content,disclosure,source_message_ids,confidence) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (str(scope.user_id), scope.realm, str(scope.channel_id), int(scope.public_at_capture),
                     item["kind"], item["content"], item["disclosure"],
                     ",".join(item["source_message_ids"]), item["confidence"]),
                )
            self.db.execute(
                "INSERT INTO structured_memory_cursors(scope,realm,user_id,through_id,updated_at) "
                "VALUES (?,?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(scope) DO UPDATE SET "
                "through_id=excluded.through_id,updated_at=CURRENT_TIMESTAMP",
                (scope.conversation, scope.realm, str(scope.user_id), through_id),
            )

    @staticmethod
    def _validate_structured_item(scope: Scope, item: dict, sources: set[str]) -> dict:
        if not isinstance(item, dict):
            raise TypeError("structured memory item must be an object")
        kind, content = item.get("kind"), item.get("content")
        source_ids, confidence, disclosure = item.get("source_message_ids"), item.get("confidence"), item.get("disclosure")
        if kind not in {"fact", "event", "preference", "relationship", "boundary", "task"}:
            raise ValueError("invalid structured memory kind")
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
        return {"kind": kind, "content": content.strip(), "disclosure": disclosure,
                "source_message_ids": source_ids, "confidence": float(confidence)}

    def forget(self, scope: Scope):
        """Delete this user's history and manual notes across channels in the current realm."""
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                self.db.execute(f"DELETE FROM {table} WHERE realm=? AND user_id=?",
                                (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM manual_notes WHERE scope=?", (scope.user_note,))
            self.db.execute("DELETE FROM notes WHERE scope=?", (scope.user_note,))

    @staticmethod
    def _rowcount(cursor) -> int:
        return max(0, int(cursor.rowcount or 0))

    def purge_channel_memory(self, scope: Scope) -> int:
        """Delete persistent user memory for every user in one channel."""
        prefix = scope.channel + ":user:%"
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE scope LIKE ?", (prefix,))
                deleted += self._rowcount(cursor)
        return deleted

    def purge_realm_memory(self, scope: Scope) -> int:
        """Delete all user-owned persistent memory in a guild/realm, preserving realm notes."""
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                cursor = self.db.execute(f"DELETE FROM {table} WHERE realm=?", (scope.realm,))
                deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM manual_notes WHERE scope LIKE ?", (scope.realm + ":user:%",))
            deleted += self._rowcount(cursor)
            cursor = self.db.execute(
                "DELETE FROM notes WHERE scope LIKE ?", (scope.realm + ":user:%",))
            deleted += self._rowcount(cursor)
        return deleted

    def purge_all_memory(self) -> int:
        """Delete all user-owned persistent memory while preserving config and shared realm notes."""
        deleted = 0
        with self.db:
            for table in ("turns", "summaries", "shared_calls", "shared_summaries"):
                cursor = self.db.execute(f"DELETE FROM {table}")
                deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM manual_notes WHERE instr(scope, ':user:') > 0")
            deleted += self._rowcount(cursor)
            cursor = self.db.execute("DELETE FROM notes WHERE instr(scope, ':user:') > 0")
            deleted += self._rowcount(cursor)
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
                               (scope.conversation, self.shared_summary(scope)[1])).fetchall()

    def save_shared_summary(self, scope, name, text, through):
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
            " GROUP BY scope,user_id ORDER BY recent DESC LIMIT 30", (value,)).fetchall()
        return [Scope(int(r["scope"].split(":")[1]), int(r["scope"].split(":")[3]),
                      int(r["user_id"])) for r in rows]

    def public_context(self, allowed_scopes):
        context = []
        for source in allowed_scopes[:4]:
            rows = self.db.execute("SELECT * FROM shared_calls WHERE scope=? ORDER BY id DESC LIMIT 2",
                                   (source.conversation,)).fetchall()
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
