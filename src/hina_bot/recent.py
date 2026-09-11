"""Bounded, ephemeral channel context; never an input to long-term summarization."""
import time
from collections import OrderedDict, deque


class RecentMessages:
    def __init__(self, limit=30, ttl=900, channels=128, budget=6000):
        self.limit, self.ttl, self.channels = limit, ttl, channels
        self.budget = budget
        self.buffers = OrderedDict()

    def add(self, scope, message_id, name, content, *, role="user"):
        now = time.monotonic()
        key = (scope.realm, scope.channel_id)
        self.prune(now)
        rows = self.buffers.setdefault(key, deque(maxlen=self.limit))
        if any(r["message_id"] == message_id for r in rows):
            return
        rows.append({"message_id": message_id, "user_id": str(scope.user_id),
                     "name": name[:100], "content": content[:4000], "role": role,
                     "received_at": now, "unix_time": time.time()})
        self.buffers.move_to_end(key)
        while len(self.buffers) > self.channels:
            self.buffers.popitem(last=False)

    def prune(self, now):
        for key, rows in list(self.buffers.items()):
            while rows and now - rows[0]["received_at"] >= self.ttl:
                rows.popleft()
            if not rows:
                del self.buffers[key]

    def context(self, scope, before_id):
        self.prune(time.monotonic())
        result, remaining = [], self.budget
        for row in reversed(self.buffers.get((scope.realm, scope.channel_id), ())):
            if row["message_id"] >= before_id:
                continue
            if remaining <= 0 or len(result) >= 12:
                break
            item = dict(row)
            item["content"] = item["content"][:remaining]
            remaining -= len(item["content"])
            result.append(item)
        return list(reversed(result))

    def forget(self, scope):
        # Bot replies can paraphrase deleted material: invalidate all recent context in this realm.
        for key in list(self.buffers):
            if key[0] == scope.realm:
                del self.buffers[key]
