"""One event-loop owner for manual, automatic and reconnect presence updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4

import discord

from rio_bot.core.presence import REQUEST_TTL, PresenceStore, validate_presence

log = logging.getLogger("rio")
MIN_SEND_INTERVAL = 5.0  # Stay within five sends per 20s, including reconnects and compensation.
SEND_TIMEOUT = 3.0


class PresenceController:
    def __init__(self, client, *, interval: float = MIN_SEND_INTERVAL):
        self.client = client
        self.store = PresenceStore(client.store.db)
        self.store.initialize(str(uuid4()))
        self.interval = interval
        attempt_at = self.store.db.execute("SELECT attempt_at FROM bot_presence WHERE id=1").fetchone()[0]
        delay = max(0, interval - (time.time() - attempt_at)) if attempt_at else 0
        self.next_send = time.monotonic() + delay
        self.connected = False
        self.generation = 0
        self.restore = True

    def connection_changed(self, connected: bool) -> None:
        self.connected = connected
        self.generation += 1
        self.restore = True
        try:
            self.store.heartbeat(connected)
            if connected:
                with self.store.db:
                    self.store.db.execute("UPDATE bot_presence SET apply_state='pending' WHERE id=1")
        except Exception as exc:  # noqa: BLE001 - telemetry/delivery must survive a presence DB failure.
            log.warning("Presence connection state write failed (%s)", type(exc).__name__)

    async def send(self, candidate: dict) -> None:
        candidate = validate_presence(candidate)
        generation = self.generation
        self.next_send = time.monotonic() + self.interval
        with self.store.db:
            self.store.db.execute("UPDATE bot_presence SET attempt_at=? WHERE id=1", (time.time(),))
        activity_type = getattr(discord.ActivityType, candidate["activity_type"])
        activity = discord.Activity(type=activity_type, name=candidate["activity_text"])
        await asyncio.wait_for(self.client.change_presence(
            status=getattr(discord.Status, candidate["status"]), activity=activity,
        ), timeout=SEND_TIMEOUT)
        if not self.connected or generation != self.generation:
            raise ConnectionError("connection changed during presence send")

    async def tick(self) -> None:
        self.store.heartbeat(self.connected)
        with self.store.db:
            for row in self.store.db.execute(
                "SELECT request_id FROM bot_presence_requests WHERE state='queued' AND created_at<?",
                (time.time() - REQUEST_TTL,),
            ).fetchall():
                self.store.finish(row["request_id"], "failure", "Presence 요청 대기 시간이 만료되었습니다.")
        if not self.connected or time.monotonic() < self.next_send:
            return
        snapshot = self.store.snapshot()
        # A prior SQLite failure may have left the claimed command without an outcome.
        with self.store.db:
            for row in self.store.db.execute(
                "SELECT request_id FROM bot_presence_requests WHERE state='applying'"
            ).fetchall():
                self.store.finish(row["request_id"], "unknown", "이전 Presence 적용 결과가 불확실합니다.")
                self.restore = True
        # Restore the committed value before accepting more commands after uncertain IO.
        if self.restore:
            await self.send(snapshot["configured"])
            self.store.applied(snapshot["configured"])
            self.restore = False
            return
        request = self.store.claim()
        if request is None:
            return
        try:
            candidate = validate_presence(json.loads(request["payload"]))
            # A same-value request still commits mode/manual and gets an audited outcome.
            if candidate != snapshot["last_sent"]:
                await self.send(candidate)
            self.store.applied(candidate, request["request_id"])
        except asyncio.CancelledError:
            self.store.failed(request["request_id"], "Bot이 종료되어 적용 결과가 불확실합니다.", uncertain=True)
            raise
        except Exception:  # noqa: BLE001 - one failed request must not stop the bot.
            self.restore = True
            self.store.failed(
                request["request_id"],
                "Presence 전송 또는 저장에 실패했습니다. 이전 설정을 유지하며 전송 상태를 재확인합니다.",
                uncertain=True,
            )

    async def run(self) -> None:
        try:
            while not self.client.stopping:
                try:
                    await self.tick()
                except Exception as exc:  # noqa: BLE001 - retry storage/reconnect failures independently.
                    self.restore = True
                    log.warning("Presence processing failed (%s)", type(exc).__name__)
                await asyncio.sleep(1)
        finally:
            self.store.heartbeat(False)
