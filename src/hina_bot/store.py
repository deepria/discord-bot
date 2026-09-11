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
            CREATE TABLE IF NOT EXISTS notes (scope TEXT PRIMARY KEY, text TEXT NOT NULL);
        """)

    def close(self):
        self.db.close()

    def note(self, key: str) -> str:
        row = self.db.execute("SELECT text FROM notes WHERE scope=?", (key,)).fetchone()
        return row[0] if row else ""

    def set_note(self, key: str, text: str):
        with self.db:
            if text:
                self.db.execute("INSERT OR REPLACE INTO notes VALUES (?,?)", (key, text[:1500]))
            else:
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

    def forget(self, scope: Scope):
        """Delete this user's history and notes across channels in the current realm."""
        with self.db:
            for table in ("turns", "summaries"):
                self.db.execute(f"DELETE FROM {table} WHERE realm=? AND user_id=?",
                                (scope.realm, str(scope.user_id)))
            self.db.execute("DELETE FROM notes WHERE scope=?", (scope.user_note,))

    def public_candidates(self, user_id: int):
        # Candidates only. The Discord adapter MUST recheck membership and visibility.
        rows = self.db.execute(
            "SELECT scope, MAX(id) AS recent FROM turns WHERE user_id=? "
            "AND realm LIKE 'guild:%' AND exportable=1 "
            "GROUP BY scope ORDER BY recent DESC LIMIT 30",
            (str(user_id),)).fetchall()
        return [Scope(int(row["scope"].split(":")[1]),
                      int(row["scope"].split(":")[3]), user_id) for row in rows]

    def public_context(self, allowed_scopes: list[Scope]):
        context = []
        for source in allowed_scopes[:4]:
            summary, _ = self.summary(source)
            context.append({
                "source": source.conversation,
                "summary": summary if self.summary_exportable(source) else "",
                "recent_user_messages": [row["content"] for row in self.history(source)
                                         if row["exportable"]][-2:],
            })
        return context
