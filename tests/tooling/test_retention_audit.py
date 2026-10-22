import json
from datetime import UTC, datetime

import pytest

from rio_bot.tooling.retention_audit import MAX_LINE_BYTES, TARGETS, audit, main

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def test_inventory_scans_rotations_without_exposing_or_changing_rows(tmp_path):
    secret = "private-message-and-key"
    files = {
        "events.jsonl": '{"at":"2026-10-22T00:00:00Z","content":"' + secret + '"}\n',
        "events.jsonl.1": '{"at":"2026-09-01T00:00:00+00:00"}\n',
        "events.jsonl.2": '{"at":"2026-10-23T00:00:00Z"}\n',
        "events.jsonl.private": secret,
        ".env": secret,
    }
    for name, text in files.items():
        (tmp_path / name).write_text(text)
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    report = audit(tmp_path, now=NOW)
    stream = report["streams"]["events.jsonl"]
    assert stream["files"] == 3
    assert stream["rows"] == 3
    assert stream["future_rows"] == 1
    assert stream["oldest_age_days"] == 51
    assert stream["observed_span_days"] == 51
    assert stream["records_older_than_target"] == 1
    assert stream["policy_verified"] is False
    assert report["host_configuration_verified"] is False
    assert secret not in json.dumps(report)
    assert before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}


def test_invalid_oversized_and_unzoned_rows_do_not_hide_later_records(tmp_path):
    (tmp_path / "usage.jsonl").write_bytes(
        b'not-json\n[]\n{"at":"2026-10-01"}\n'
        + b"x" * (MAX_LINE_BYTES * 2) + b'\n'
        + b'{"at":"2026-10-21T09:00:00+09:00"}\n'
    )
    stream = audit(tmp_path, now=NOW)["streams"]["usage.jsonl"]
    assert stream["invalid_rows"] == 3
    assert stream["oversized_rows"] == 1
    assert stream["rows"] == 1
    assert stream["oldest_age_days"] == 1
    assert stream["policy_target_days"] is None
    assert stream["records_older_than_target"] is None


def test_missing_and_symlink_sources_remain_unverified(tmp_path):
    missing = audit(tmp_path / "absent", now=NOW)
    assert all(x["source_status"] == "UNAVAILABLE" for x in missing["streams"].values())
    target = tmp_path / "private"
    target.write_text("secret")
    (tmp_path / "events.jsonl").symlink_to(target)
    streams = audit(tmp_path, now=NOW)["streams"]
    assert streams["events.jsonl"]["source_status"] == "PARTIAL"
    assert streams["usage.jsonl"]["source_status"] == "MISSING"
    assert streams["events.jsonl"]["rows"] == 0


def test_cli_exit_code_distinguishes_inventory_from_compliance(tmp_path, capsys):
    assert main(["--log-dir", str(tmp_path)]) == 1
    capsys.readouterr()
    for name in TARGETS:
        (tmp_path / name).write_text('{"at":"2020-01-01T00:00:00Z"}\n')
    assert main(["--log-dir", str(tmp_path)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert all(not row["policy_verified"] for row in report["streams"].values())


def test_naive_reference_time_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="timezone"):
        audit(tmp_path, now=NOW.replace(tzinfo=None))


def test_unreadable_file_reports_partial_without_exception_contents(tmp_path, monkeypatch):
    path = tmp_path / "events.jsonl"
    path.write_text("private")

    def fail_open(*args, **kwargs):
        raise PermissionError("private-error-detail")

    monkeypatch.setattr(type(path), "open", fail_open)
    report = audit(tmp_path, now=NOW)
    assert report["streams"]["events.jsonl"]["source_status"] == "PARTIAL"
    assert report["streams"]["events.jsonl"]["unreadable_files"] == 1
    assert "private" not in json.dumps(report)
