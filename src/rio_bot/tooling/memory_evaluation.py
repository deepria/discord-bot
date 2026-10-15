"""Read-only metadata screening for structured-memory shadow evaluation."""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

_COLUMNS = {"status", "source_message_ids", "source_turn_ids", "created_at"}
_STATUSES = ("active", "superseded", "deleted")


def _links(value: object) -> list[str]:
    # Store writes ordered, comma-separated source IDs. Missing entries must not
    # count as provenance just because another entry in the same string exists.
    return value.split(",") if isinstance(value, str) else []


def _timestamp(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except ValueError:
        return None


def evaluate_shadow_memory(db_path: str, *, min_items: int = 50, min_days: int = 7,
                           now: datetime | None = None) -> dict:
    """Screen metadata only; readiness never authorizes retrieval or content access."""
    if min_items < 1 or min_days < 1:
        raise ValueError("minimum item count and observation days must be positive")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must include a timezone")
    result = {
        "source_status": "UNAVAILABLE",
        "items": {"total": 0, **{status: 0 for status in _STATUSES}, "other_status": 0},
        "provenance": {"active_message_linked": 0, "active_turn_linked": 0},
        "observation": {"first_item_at": None, "last_item_at": None, "elapsed_days": 0},
        "readiness": {"minimum_active_items": min_items, "minimum_days": min_days,
                      "ready_for_metadata_review": False, "reasons": []},
    }
    reasons = result["readiness"]["reasons"]
    database = Path(db_path).resolve()
    if not database.is_file():
        reasons.append("database_unavailable")
        return result
    first = last = None
    invalid_dates = False
    try:
        # as_uri escapes spaces, '?' and '#' in paths; never instantiate runtime Store.
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=1)
        try:
            connection.execute("PRAGMA query_only=ON")
            columns = {row[1] for row in connection.execute(
                "PRAGMA table_info(structured_memory_items)")}
            if not _COLUMNS <= columns:
                reasons.append("shadow_schema_unavailable")
                return result
            for status, messages, turns, created_at in connection.execute(
                "SELECT status, source_message_ids, source_turn_ids, created_at "
                "FROM structured_memory_items"
            ):
                result["items"]["total"] += 1
                key = status if status in _STATUSES else "other_status"
                result["items"][key] += 1
                if status != "active":
                    continue
                message_links, turn_links = _links(messages), _links(turns)
                message_ok = bool(message_links) and all(x.strip() for x in message_links)
                turn_ok = (message_ok and len(turn_links) == len(message_links)
                           and all(x.strip() for x in turn_links))
                result["provenance"]["active_message_linked"] += int(message_ok)
                result["provenance"]["active_turn_linked"] += int(turn_ok)
                timestamp = _timestamp(created_at)
                if timestamp is None or timestamp > current:
                    invalid_dates = True
                else:
                    first = timestamp if first is None else min(first, timestamp)
                    last = timestamp if last is None else max(last, timestamp)
        finally:
            connection.close()
    except sqlite3.Error:
        # A partial scan is never reported as usable evidence.
        reasons.append("database_read_failed")
        return result
    result["source_status"] = "HEALTHY"
    if first is not None:
        result["observation"] = {
            "first_item_at": first.isoformat(), "last_item_at": last.isoformat(),
            "elapsed_days": (current - first).days,
        }
    active = result["items"]["active"]
    if active < min_items:
        reasons.append("insufficient_active_items")
    if result["observation"]["elapsed_days"] < min_days:
        reasons.append("insufficient_observation_days")
    if invalid_dates:
        reasons.append("invalid_item_timestamp")
    for field, reason in [("active_message_linked", "missing_message_provenance"),
                          ("active_turn_linked", "missing_turn_provenance")]:
        if result["provenance"][field] != active:
            reasons.append(reason)
    if result["items"]["other_status"]:
        reasons.append("unknown_lifecycle_status")
    result["readiness"]["ready_for_metadata_review"] = not reasons
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/rio.sqlite3")
    parser.add_argument("--min-items", default=50, type=int)
    parser.add_argument("--min-days", default=7, type=int)
    args = parser.parse_args()
    try:
        print(json.dumps(evaluate_shadow_memory(
            args.db, min_items=args.min_items, min_days=args.min_days), ensure_ascii=False))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
