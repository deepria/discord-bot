import argparse
from collections import Counter
from copy import deepcopy
from pathlib import Path

from . import lore_pipeline
from .lore import (
    CONFIDENCE_LEVELS,
    FACT_TYPES,
    REFERENCE_ONLY_FACT_TYPES,
    LoreValidationError,
    fact_type,
    read_jsonl,
    validate_record,
    write_jsonl,
)

_PRIMARY_SOURCE_TYPES = {"official_game", "official_site", "official_video", "official_profile"}
_SECONDARY_SOURCE_TYPES = {"official_secondary", "game_data_mirror", "official_data_mirror"}
_RUNTIME_OMIT = {"source_id", "evidence", "uncertainty", "kr_release_evidence"}


def _default_bulk_confidence(row: dict) -> str:
    evidence_type = fact_type(row)
    if row["lane"] == "community_meme" or evidence_type in {"inference", "unknown"}:
        return "crosschecked"
    source_type = str(row.get("source", {}).get("type", ""))
    if source_type in _PRIMARY_SOURCE_TYPES:
        return "verified"
    if source_type in _SECONDARY_SOURCE_TYPES:
        return "official_secondary"
    return "crosschecked"


def _matches_scope(row: dict, args) -> bool:
    source = row.get("source", {})
    if args.source_type is not None and source.get("type") != args.source_type:
        return False
    if args.id_prefix is not None and not row.get("id", "").startswith(args.id_prefix):
        return False
    if args.title is not None and source.get("title") != args.title:
        return False
    return args.fact_type is None or fact_type(row) in args.fact_type


def _has_web_conflict(row: dict) -> bool:
    verification = row.get("verification")
    return isinstance(verification, dict) and verification.get("status") == "conflict"


def _prepare_acceptance(
    row: dict, *, confidence: str, confirm_kr_release: bool
) -> tuple[dict, dict]:
    if row["status"] != "candidate":
        raise LoreValidationError(f"{row['id']}: candidate 상태만 일괄 승인할 수 있습니다.")
    if row["lane"] == "canon" and fact_type(row) in REFERENCE_ONLY_FACT_TYPES:
        raise LoreValidationError(f"{row['id']}: reference-only canon 항목은 승인할 수 없습니다.")
    if row["lane"] == "canon" and not confirm_kr_release:
        raise LoreValidationError(
            "canon 일괄 승인은 --confirm-kr-release로 한국 서버 출시를 확인해야 합니다."
        )

    accepted = deepcopy(row)
    accepted["status"] = "accepted"
    accepted["confidence"] = confidence
    if accepted["lane"] == "canon":
        accepted["kr_release"] = "confirmed"
    validate_record(accepted)

    runtime = {key: value for key, value in accepted.items() if key not in _RUNTIME_OMIT}
    validate_record(runtime, accepted=True)
    return accepted, runtime


def _print_bulk_plan(*, scoped: list[dict], prepared: list[tuple[dict, dict]],
                     suppressed: list[dict], conflicts: list[dict], duplicates: list[dict],
                     dry_run: bool) -> None:
    counts = Counter((fact_type(accepted), accepted["confidence"]) for accepted, _ in prepared)
    print(f"scope matched: {len(scoped)}")
    print(f"approve: {len(prepared)}")
    for (evidence_type, confidence), count in sorted(counts.items()):
        print(f"  {evidence_type}: {count} ({confidence})")
    print(f"excluded suppressed/non-candidate: {len(suppressed)}")
    print(f"excluded web conflicts: {len(conflicts)}")
    print(f"excluded runtime duplicates: {len(duplicates)}")
    if dry_run:
        print("실제 변경 없음 (--dry-run)")


def approve_all(args) -> None:
    if not any((args.source_type, args.id_prefix, args.title)):
        raise SystemExit("approve-all은 --source-type, --id-prefix, --title 중 하나 이상이 필요합니다.")

    queue = read_jsonl(lore_pipeline.QUEUE_PATH)
    runtime = read_jsonl(lore_pipeline.RUNTIME_PATH)
    scoped = [row for row in queue if _matches_scope(row, args)]
    candidates = [row for row in scoped if row.get("status") == "candidate"]
    suppressed = [row for row in scoped if row.get("status") != "candidate"]

    runtime_ids = {row["id"] for row in runtime}
    duplicates = [row for row in candidates if row["id"] in runtime_ids]
    duplicate_ids = {row["id"] for row in duplicates}
    conflicts = [row for row in candidates if _has_web_conflict(row)]
    conflict_ids = {row["id"] for row in conflicts}
    targets = [
        row for row in candidates
        if row["id"] not in duplicate_ids and row["id"] not in conflict_ids
    ]

    prepared = []
    for row in targets:
        confidence = args.confidence or _default_bulk_confidence(row)
        prepared.append(_prepare_acceptance(
            row,
            confidence=confidence,
            confirm_kr_release=args.confirm_kr_release,
        ))

    final_runtime = runtime + [clean for _, clean in prepared]
    ids = [row["id"] for row in final_runtime]
    if len(ids) != len(set(ids)):
        raise LoreValidationError("bulk approval 결과에 duplicate runtime ids가 있습니다.")
    for row in final_runtime:
        validate_record(row, accepted=True)

    _print_bulk_plan(
        scoped=scoped,
        prepared=prepared,
        suppressed=suppressed,
        conflicts=conflicts,
        duplicates=duplicates,
        dry_run=args.dry_run,
    )
    if args.dry_run:
        return
    if not prepared:
        print("승인할 새 candidate가 없습니다.")
        return

    accepted_by_id = {accepted["id"]: accepted for accepted, _ in prepared}
    final_queue = [accepted_by_id.get(row["id"], row) for row in queue]
    for row in final_queue:
        validate_record(row)

    try:
        write_jsonl(lore_pipeline.RUNTIME_PATH, final_runtime)
        write_jsonl(lore_pipeline.QUEUE_PATH, final_queue)
    except Exception:
        # The two JSONL files cannot be replaced as one filesystem transaction.
        # Restore both snapshots if the second write fails.
        write_jsonl(lore_pipeline.RUNTIME_PATH, runtime)
        write_jsonl(lore_pipeline.QUEUE_PATH, queue)
        raise
    print(f"accepted: {len(prepared)} candidate(s)")


def parser() -> argparse.ArgumentParser:
    root = lore_pipeline.parser()
    subparsers = next(
        action for action in root._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    command = subparsers.add_parser("approve-all", help="검수 후보를 필터링해 일괄 승인합니다.")
    command.add_argument("--source-type")
    command.add_argument("--id-prefix")
    command.add_argument("--title")
    command.add_argument("--fact-type", action="append", choices=sorted(FACT_TYPES))
    command.add_argument("--confirm-kr-release", action="store_true")
    command.add_argument(
        "--confidence",
        choices=sorted(CONFIDENCE_LEVELS - {"candidate"}),
        help="지정하면 모든 승인 대상에 같은 confidence를 사용합니다.",
    )
    mode = command.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="승인 계획만 출력하고 파일은 바꾸지 않습니다.")
    mode.add_argument("--yes", action="store_true", help="사전 검사를 통과한 후보를 실제로 승인합니다.")
    command.set_defaults(run=approve_all)
    return root


def main() -> None:
    try:
        from dotenv import load_dotenv
    except ModuleNotFoundError:
        pass
    else:
        load_dotenv(Path.cwd() / ".env.local", override=False)
        load_dotenv(Path.cwd() / ".env", override=False)
    args = parser().parse_args()
    try:
        args.run(args)
    except (LoreValidationError, OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
