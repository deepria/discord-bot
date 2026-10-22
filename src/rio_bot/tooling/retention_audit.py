"""Read-only inventory of telemetry timestamps; never emit log rows or contents."""

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

# These are policy targets, not claims about the size-based runtime handlers.
TARGETS = {"events.jsonl": 30, "usage.jsonl": None, "discord-usage.jsonl": 90}
MAX_LINE_BYTES = 1_048_576


def _timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value)
        return result.astimezone(UTC) if result.tzinfo else None
    except (ValueError, OverflowError):
        return None


def audit_stream(directory: Path, name: str, *, now: datetime) -> dict:
    """Scan the active log and numeric backups without retaining row contents.

    Dates describe observed records only: gaps, external archives, and host
    retention configuration cannot be inferred from them.
    """
    result = {
        "files": 0, "bytes": 0, "rows": 0, "invalid_rows": 0,
        "oversized_rows": 0, "future_rows": 0, "unreadable_files": 0,
        "oldest_at": None, "newest_at": None, "oldest_age_days": None,
        "observed_span_days": None,
        "records_older_than_target": 0 if TARGETS[name] else None,
        "policy_target_days": TARGETS[name], "policy_verified": False,
    }
    oldest = newest = None
    # Fixed basenames and numeric rotations exclude .env, SQLite, and other files.
    try:
        paths = sorted(directory.iterdir())
    except OSError:
        return {**result, "source_status": "UNAVAILABLE"}
    for path in paths:
        suffix = path.name.removeprefix(name + ".")
        if path.name != name and not (path.name.startswith(name + ".") and suffix.isdigit()):
            continue
        # Do not follow a link into unrelated data.
        if path.is_symlink() or not path.is_file():
            result["unreadable_files"] += 1
            continue
        result["files"] += 1
        try:
            with path.open("rb") as stream:
                result["bytes"] += path.stat().st_size
                while line := stream.readline(MAX_LINE_BYTES + 1):
                    if len(line) > MAX_LINE_BYTES:
                        result["oversized_rows"] += 1
                        while line and not line.endswith(b"\n"):
                            line = stream.readline(MAX_LINE_BYTES + 1)
                        continue
                    try:
                        row = json.loads(line)
                    except (ValueError, UnicodeDecodeError, RecursionError):
                        result["invalid_rows"] += 1
                        continue
                    at = _timestamp(row.get("at")) if isinstance(row, dict) else None
                    if at is None:
                        result["invalid_rows"] += 1
                        continue
                    result["rows"] += 1
                    if at > now:
                        result["future_rows"] += 1
                        continue
                    oldest = min(oldest, at) if oldest else at
                    newest = max(newest, at) if newest else at
                    target_days = TARGETS[name]
                    if target_days and (now - at).total_seconds() > target_days * 86400:
                        result["records_older_than_target"] += 1
        except OSError:
            result["unreadable_files"] += 1
    if oldest:
        result.update(
            oldest_at=oldest.isoformat(), newest_at=newest.isoformat(),
            oldest_age_days=round((now - oldest).total_seconds() / 86400, 3),
            observed_span_days=round((newest - oldest).total_seconds() / 86400, 3),
        )
    result["source_status"] = (
        "PARTIAL" if result["unreadable_files"] else "AVAILABLE" if result["files"] else "MISSING"
    )
    return result


def audit(directory: Path, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must include a timezone")
    return {
        "schema_version": 1, "checked_at": now.astimezone(UTC).isoformat(),
        "read_only": True, "host_configuration_verified": False,
        "streams": {name: audit_stream(directory, name, now=now) for name in TARGETS},
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-dir", required=True, type=Path,
                        help="Actual runtime log directory; no .env is loaded")
    args = parser.parse_args(argv)
    report = audit(args.log_dir)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    # A successful inventory is not a policy compliance verdict.
    return int(any(stream["source_status"] != "AVAILABLE" or any(
        stream[key] for key in ("invalid_rows", "oversized_rows", "future_rows")
    ) for stream in report["streams"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
