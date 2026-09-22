"""Canonical, content-free writes for memory and chatlog policy overrides."""

from __future__ import annotations

import hashlib
import json
import re
import uuid

_SCOPE = re.compile(r"^(?:global|guild:\d+(?::channel:\d+)?)$")
_VALUES = {
    "memory": {"normal", "read_only", "write_only", "off"},
    "chatlog": {"on", "off"},
    "capture": {"all", "direct"},
}


def set_policy_override(
    store, *, policy: str, scope: str, value: str, actor_kind: str, actor_id: str, request_id: str
) -> dict[str, str]:
    """Validate, persist and audit one override; `inherit` removes it."""
    if policy not in _VALUES or not _SCOPE.fullmatch(scope):
        raise ValueError("지원하지 않는 정책 또는 범위예요.")
    if value != "inherit" and value not in _VALUES[policy]:
        raise ValueError("지원하지 않는 정책 값이에요.")
    if scope == "global" and value == "inherit":
        raise ValueError("전역 설정은 상속할 수 없어요.")
    if actor_kind not in {"console", "discord"} or not actor_id or len(actor_id) > 200:
        raise ValueError("유효하지 않은 변경 주체예요.")
    digest = hashlib.sha256(json.dumps([policy, scope, value]).encode()).hexdigest()
    with store.db:
        prior = store.db.execute(
            "SELECT policy,scope,payload_digest FROM policy_config_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if prior is not None:
            if tuple(prior) != (policy, scope, digest):
                raise ValueError("request_id가 다른 정책 변경에 이미 사용됐어요.")
        else:
            override = None if value == "inherit" else value
            if policy == "memory":
                store.set_memory_mode_override(scope, override)
            elif policy == "chatlog":
                store.set_chat_log_mode_override(scope, override)
            else:
                store.set_note(f"config:chatlog_capture:{scope}", override or "")
            store.db.execute(
                "INSERT INTO policy_config_requests(request_id,policy,scope,payload_digest) VALUES (?,?,?,?)",
                (request_id, policy, scope, digest),
            )
            store.db.execute(
                """INSERT INTO policy_config_audit(id,actor_kind,actor_id,policy,scope,outcome,request_id)
                   VALUES (?,?,?,?,?,'success',?)""",
                (str(uuid.uuid4()), actor_kind, actor_id, policy, scope, request_id),
            )
    return {"policy": policy, "scope": scope, "override": value}
