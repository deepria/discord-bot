"""Bot-owned SQLite control mailbox; no Discord client is created by the agent."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

STATUSES = ("online", "idle", "dnd", "invisible")
ACTIVITY_TYPES = ("playing", "watching", "listening")
MODES = ("auto", "manual")
TEXT_MAX_LENGTH = 128
HEARTBEAT_MAX_AGE = 5
REQUEST_TTL = 30
AUTO = {"mode": "auto", "status": "online", "activity_type": "playing",
        "activity_text": "대기 중"}


class PresenceError(ValueError):
    def __init__(self, detail: str, status_code: int = 422):
        super().__init__(detail)
        self.status_code = status_code


def validate_presence(value: dict) -> dict:
    if value.get("mode") not in MODES:
        raise PresenceError("Mode는 auto 또는 manual이어야 합니다.")
    if value.get("status") not in STATUSES:
        raise PresenceError("지원하지 않는 Discord 상태입니다.")
    if value.get("activity_type") not in ACTIVITY_TYPES:
        raise PresenceError("Activity는 playing, watching, listening만 지원합니다.")
    text = value.get("activity_text")
    if not isinstance(text, str):
        raise PresenceError("Activity Text는 문자열이어야 합니다.")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in text):
        raise PresenceError("Activity Text에 제어 문자를 사용할 수 없습니다.")
    text = text.strip()
    if not 1 <= len(text) <= TEXT_MAX_LENGTH:
        raise PresenceError(f"Activity Text는 공백을 제외하고 1~{TEXT_MAX_LENGTH}자여야 합니다.")
    return dict(AUTO) if value["mode"] == "auto" else {
        "mode": value["mode"], "status": value["status"],
        "activity_type": value["activity_type"], "activity_text": text,
    }


def encoded(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def timestamp(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, UTC).isoformat() if value else None


class PresenceStore:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def initialize(self, run_id: str) -> None:
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS bot_presence (
                id INTEGER PRIMARY KEY CHECK(id=1), configured TEXT NOT NULL,
                manual TEXT NOT NULL, last_sent TEXT, sent_at REAL, attempt_at REAL,
                connected INTEGER NOT NULL DEFAULT 0, heartbeat REAL NOT NULL DEFAULT 0,
                run_id TEXT NOT NULL, apply_state TEXT NOT NULL DEFAULT 'pending'
            );
            CREATE TABLE IF NOT EXISTS bot_presence_requests (
                request_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, digest TEXT NOT NULL,
                payload TEXT, state TEXT NOT NULL, error TEXT, created_at REAL NOT NULL,
                completed_at REAL
            );
            CREATE TABLE IF NOT EXISTS bot_presence_audit (
                id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                actor_kind TEXT NOT NULL, actor_id TEXT NOT NULL,
                action TEXT NOT NULL, outcome TEXT NOT NULL, occurred_at REAL NOT NULL
            );
        """)
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO bot_presence(id,configured,manual,run_id) VALUES(1,?,?,?)",
                (encoded(AUTO), encoded({**AUTO, "mode": "manual"}), run_id),
            )
            self.db.execute(
                "UPDATE bot_presence SET connected=0,heartbeat=?,run_id=?,apply_state='pending'",
                (time.time(), run_id),
            )
            for row in self.db.execute(
                "SELECT request_id FROM bot_presence_requests WHERE state IN ('queued','applying')"
            ).fetchall():
                self.finish(row["request_id"], "unknown", "Bot이 재시작되어 적용 결과를 확인할 수 없습니다.")

    def snapshot(self, request_id: str | None = None) -> dict:
        row = self.db.execute("SELECT * FROM bot_presence WHERE id=1").fetchone()
        if row is None:
            raise PresenceError("Bot Presence가 아직 초기화되지 않았습니다.", 503)
        connected = bool(row["connected"]) and time.time() - row["heartbeat"] <= HEARTBEAT_MAX_AGE
        if request_id:
            operation = self.db.execute(
                "SELECT * FROM bot_presence_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if operation is None:
                raise PresenceError("Presence 요청을 찾을 수 없습니다.", 404)
        else:
            operation = self.db.execute(
                "SELECT * FROM bot_presence_requests ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        audits = self.db.execute(
            "SELECT request_id,actor_kind,actor_id,action,outcome,occurred_at "
            "FROM bot_presence_audit ORDER BY occurred_at DESC LIMIT 20"
        ).fetchall()
        return {
            "configured": json.loads(row["configured"]),
            "manual": json.loads(row["manual"]),
            "last_sent": json.loads(row["last_sent"]) if row["last_sent"] else None,
            "last_sent_at": timestamp(row["sent_at"]), "connected": connected,
            "apply_state": row["apply_state"] if connected else "unavailable",
            "operation": {
                "request_id": operation["request_id"], "state": operation["state"],
                "error": operation["error"], "completed_at": timestamp(operation["completed_at"]),
            } if operation else None,
            "audit": [{**dict(item), "occurred_at": timestamp(item["occurred_at"])} for item in audits],
            "capabilities": {"statuses": list(STATUSES), "activity_types": list(ACTIVITY_TYPES),
                             "text_max_length": TEXT_MAX_LENGTH},
        }

    def submit(self, payload: dict, actor_id: str, request_id: str) -> dict:
        request_id = str(UUID(request_id))
        candidate = validate_presence(payload)
        digest = hashlib.sha256(encoded(candidate).encode()).hexdigest()
        # Serialize submissions across agent processes, without holding a transaction over Discord IO.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            prior = self.db.execute(
                "SELECT * FROM bot_presence_requests WHERE request_id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior["digest"] != digest or prior["actor_id"] != actor_id:
                    raise PresenceError("이미 다른 요청에 사용한 request_id입니다.", 409)
                return self.snapshot(request_id)
            if not self.snapshot()["connected"]:
                raise PresenceError("Discord 연결이 끊겼거나 Bot 응답이 오래되었습니다.", 503)
            if self.db.execute(
                "SELECT 1 FROM bot_presence_requests WHERE state IN ('queued','applying')"
            ).fetchone():
                raise PresenceError("다른 Presence 변경을 처리 중입니다. 잠시 후 다시 시도해 주세요.", 409)
            self.db.execute(
                "INSERT INTO bot_presence_requests VALUES(?,?,?,?,'queued',NULL,?,NULL)",
                (request_id, actor_id, digest, encoded(candidate), time.time()),
            )
        return self.snapshot(request_id)

    def heartbeat(self, connected: bool) -> None:
        with self.db:
            self.db.execute(
                "UPDATE bot_presence SET heartbeat=?,connected=? WHERE id=1",
                (time.time(), int(connected)),
            )

    def claim(self) -> sqlite3.Row | None:
        with self.db:
            row = self.db.execute(
                "SELECT * FROM bot_presence_requests WHERE state='queued' ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            if time.time() - row["created_at"] > REQUEST_TTL:
                self.finish(row["request_id"], "failure", "Presence 요청 대기 시간이 만료되었습니다.")
                return None
            self.db.execute(
                "UPDATE bot_presence_requests SET state='applying' WHERE request_id=?",
                (row["request_id"],),
            )
        return row

    def finish(self, request_id: str, state: str, error: str | None = None) -> None:
        # The caller owns the transaction: outcome/audit/configuration commit together.
        row = self.db.execute(
            "SELECT actor_id FROM bot_presence_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        self.db.execute(
            "UPDATE bot_presence_requests SET state=?,error=?,payload=NULL,completed_at=? "
            "WHERE request_id=?", (state, error, time.time(), request_id),
        )
        self.db.execute(
            "INSERT OR IGNORE INTO bot_presence_audit VALUES(?,?,? ,?,'presence.set',?,?)",
            (str(uuid4()), request_id, "console", row["actor_id"], state, time.time()),
        )

    def applied(self, candidate: dict, request_id: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "UPDATE bot_presence SET last_sent=?,sent_at=?,apply_state='sent' WHERE id=1",
                (encoded(candidate), time.time()),
            )
            if request_id:
                self.db.execute("UPDATE bot_presence SET configured=? WHERE id=1", (encoded(candidate),))
                if candidate["mode"] == "manual":
                    self.db.execute("UPDATE bot_presence SET manual=? WHERE id=1", (encoded(candidate),))
                self.finish(request_id, "success")

    def failed(self, request_id: str, error: str, *, uncertain: bool = False) -> None:
        with self.db:
            self.finish(request_id, "unknown" if uncertain else "failure", error)
            if uncertain:
                self.db.execute("UPDATE bot_presence SET apply_state='unknown' WHERE id=1")


def presence_snapshot(db_path: str, request_id: str | None = None) -> dict:
    """Read only: agent polling must not create tables or revive a stale bot heartbeat."""
    try:
        with sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            return PresenceStore(db).snapshot(request_id)
    except sqlite3.Error as exc:
        raise PresenceError("Bot Presence를 조회할 수 없습니다. Bot 배포와 기동을 확인해 주세요.", 503) from exc


def control_main() -> None:
    """Same agent -> Bot Python service boundary as runtime settings, via stdin JSON."""
    from .config import Settings

    db = None
    try:
        request = json.load(sys.stdin)
        settings = Settings.load()
        if request["action"] == "get":
            result = presence_snapshot(settings.db_path, request.get("request_id"))
        else:
            db = sqlite3.connect(f"{Path(settings.db_path).resolve().as_uri()}?mode=rw", uri=True)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=1000")
            result = PresenceStore(db).submit(
                request["payload"], request["actor_id"], request["request_id"],
            )
        print(json.dumps({"ok": True, "presence": result}, ensure_ascii=False))
    except PresenceError as exc:
        print(json.dumps({"ok": False, "status_code": exc.status_code, "detail": str(exc)}))
    except Exception:  # noqa: BLE001 - no settings, payload or traceback may escape to the agent.
        print(json.dumps({"ok": False, "status_code": 503,
                          "detail": "Bot Presence 서비스가 응답하지 않습니다."}))
    finally:
        if db is not None:
            db.close()
