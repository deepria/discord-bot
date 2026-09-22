"""Offline regression runner for routing-shadow proposals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rio_bot.ai.freshness import FreshnessMode
from rio_bot.ai.information_plan import InformationPlan
from rio_bot.ai.information_routing import InformationRoute
from rio_bot.ai.model_routing import build_shadow_model_plan
from rio_bot.ai.routing_plan import RoutingPlan
from rio_bot.ai.rp_output_policy import ProvenanceMode
from rio_bot.ai.shadow_routing import build_shadow_web_plan
from rio_bot.core.config import Settings

DEFAULT_CASES = Path("evals/routing_shadow_cases.jsonl")
_ROUTES = {item.value for item in InformationRoute}
_FRESHNESS = {item.value for item in FreshnessMode}
_WEB_MODES = {"none", "optional", "required"}
_TIERS = {"fast", "smart"}


def read_cases(path: Path) -> list[dict]:
    """Read non-sensitive fixture metadata and validate its contract."""
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: {exc}") from exc
        required = ("id", "content", "route", "freshness", "search_mode")
        if any(not isinstance(case.get(key), str) or not case[key].strip() for key in required):
            raise ValueError(f"{path}:{line_number}: id, content, route, freshness, search_mode가 필요합니다.")
        if case["route"] not in _ROUTES or case["freshness"] not in _FRESHNESS:
            raise ValueError(f"{path}:{line_number}: route 또는 freshness 값이 올바르지 않습니다.")
        expected = case.get("expected")
        if (case["search_mode"] not in _WEB_MODES or not isinstance(expected, dict)
                or expected.get("tier") not in _TIERS or expected.get("web") not in _WEB_MODES):
            raise ValueError(f"{path}:{line_number}: search_mode와 expected.tier/web 값이 올바르지 않습니다.")
        if not isinstance(case.get("references", []), list) or not isinstance(case.get("channel_context", []), list):
            raise TypeError(f"{path}:{line_number}: references와 channel_context는 배열이어야 합니다.")
        cases.append(case)
    ids = [case["id"] for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("routing eval case는 하나 이상이며 id가 고유해야 합니다.")
    return cases


def evaluate(case: dict, settings: Settings) -> dict:
    information = InformationPlan(
        routing=RoutingPlan(case["content"], case["content"]),
        route=InformationRoute(case["route"]), references=tuple(case.get("references", [])),
        freshness=FreshnessMode(case["freshness"]), fact_question=bool(case.get("fact_question", False)),
        search_mode=case["search_mode"], provenance=ProvenanceMode.SILENT,
    )
    model = build_shadow_model_plan(
        settings, content=case["content"], information=information,
        channel_context=case.get("channel_context", []), public_context=case.get("public_context", []),
        history_turns=int(case.get("history_turns", 0)),
    )
    web = build_shadow_web_plan(information)
    actual = {"tier": model.tier.value, "web": web.mode}
    expected = {"tier": case["expected"]["tier"], "web": case["expected"]["web"]}
    return {
        "id": case["id"], "expected": expected, "actual": actual, "pass": actual == expected,
        "model_score": model.score, "model_reasons": list(model.reasons), "web_reasons": list(web.reasons),
    }


def run(args) -> int:
    settings = Settings("eval-only", "eval-only", model_routing_smart_threshold=args.threshold,
                        fast_model="fast-eval", smart_model="smart-eval")
    rows = [evaluate(case, settings) for case in read_cases(Path(args.cases))]
    for row in rows:
        print(f"{'PASS' if row['pass'] else 'FAIL'} {row['id']}: "
              f"tier={row['actual']['tier']} web={row['actual']['web']}")
    if args.output:
        Path(args.output).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
    failures = sum(not row["pass"] for row in rows)
    print(f"cases: {len(rows)}, failures: {failures}")
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic Rio routing-shadow regression fixtures.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--threshold", type=float, default=2.0)
    parser.add_argument("--output", help="Write content-free evaluation results as JSONL.")
    args = parser.parse_args()
    if not 0.1 <= args.threshold <= 10:
        raise SystemExit("--threshold은 0.1~10 사이여야 합니다.")
    try:
        raise SystemExit(run(args))
    except (OSError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
