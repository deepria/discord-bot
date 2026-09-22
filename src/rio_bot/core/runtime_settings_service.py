"""Shared runtime-setting write service for Discord and the control plane."""

from __future__ import annotations

from typing import Any

from .runtime_config import (
    RUNTIME_SETTING_SPECS,
    RuntimeConfigAudit,
    RuntimeSettings,
    format_runtime_value,
    runtime_setting_attr,
)


def apply_runtime_setting(
    settings: RuntimeSettings,
    *,
    key: str,
    value: str | None,
    actor_kind: str,
    actor_id: str,
    request_id: str,
) -> dict[str, Any]:
    """Apply a set/reset using the canonical parser, store and audit writer.

    Values are deliberately returned to the immediate caller only and are never
    included in the persistent audit record.
    """
    attr = runtime_setting_attr(key)
    action = "runtime_config.set" if value is not None else "runtime_config.reset"
    audit = RuntimeConfigAudit(
        actor_kind=actor_kind,
        actor_id=actor_id,
        action=action,
        target=RUNTIME_SETTING_SPECS[attr].env_name,
        outcome="success",
        request_id=request_id,
    )
    digest = settings.request_digest(action, attr, value)
    if value is None:
        applied = settings.reset(attr, audit=audit, payload_digest=digest)
    else:
        applied = settings.set_text(attr, value, audit=audit, payload_digest=digest)
    spec = RUNTIME_SETTING_SPECS[attr]
    return {
        "key": attr,
        "env_name": spec.env_name,
        "value": applied,
        "display_value": format_runtime_value(applied),
        "source": settings.source(attr),
        "kind": spec.kind,
        "minimum": spec.minimum,
        "maximum": spec.maximum,
        "empty_allowed": spec.empty_allowed,
        "changed_at": settings.changed_at(attr),
    }


def apply_runtime_side_effects(client, key: str) -> None:
    """Keep mutable in-memory collaborators aligned with RuntimeSettings."""
    if key == "channel_context_chars":
        client.recent.budget = client.settings.channel_context_chars
