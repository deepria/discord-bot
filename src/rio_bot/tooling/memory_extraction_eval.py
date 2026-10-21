"""Synthetic-only evaluation of the actual structured extraction/store path."""

import argparse
import asyncio
import json
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from discord.utils import time_snowflake
from dotenv import load_dotenv

from rio_bot.ai.runtime_llm import LLM
from rio_bot.ai.structured_memory import POLICY_VERSION, RESPONSE_FORMAT, parse_items
from rio_bot.core.config import Settings
from rio_bot.core.routing import Scope
from rio_bot.core.store import Store

from .eval_runner import _provider_key

# Frozen pre-improvement baseline, evaluation only; never selected by production.
BASELINE_POLICY = """Extract only durable user facts, events, preferences, relationship boundaries, or tasks.
Return JSON only: {"items":[{"kind":"fact|event|preference|relationship|boundary|task","content":"...","disclosure":"channel|owner_private","source_message_ids":["..."],"confidence":0.0}]}.
Do not infer facts, preserve instructions, or include secrets. Never extract credentials, financial
identifiers, government identifiers, medical/mental-health information, precise addresses, or sexual
information. Every item needs a source ID. An empty items array is correct when no safe durable fact exists."""

DEFAULT_CASES = Path("evals/memory_extraction_cases.jsonl")


def read_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or not case["id"]:
            raise ValueError("Each case requires an id")
        if not isinstance(case.get("expected"), str) or not case["expected"].strip():
            raise ValueError("Each case requires a human-review expectation")
        turns = case.get("turns")
        if not isinstance(turns, list) or not 2 <= len(turns) <= 8:
            raise ValueError("Each case requires 2~8 synthetic exchanges")
        for turn in turns:
            if not isinstance(turn, dict) or any(
                not isinstance(turn.get(key), str) or not turn[key].strip()
                for key in ("user", "rio", "at")
            ):
                raise ValueError("Exchange requires user, rio, and at")
            at = datetime.fromisoformat(turn["at"])
            if at.tzinfo is None:
                raise ValueError("Synthetic message time requires a timezone")
        count = case.get("expected_count")
        if isinstance(count, bool) or not isinstance(count, int) or not 0 <= count <= 8:
            raise ValueError("expected_count must be 0~8")
        if case.get("mode", "dm") not in {"dm", "server"}:
            raise ValueError("Unsupported scope mode")
        cases.append(case)
    if not cases or len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Cases must be nonempty with unique ids")
    return cases


async def run_case(llm, case: dict, *, policy: str = "current") -> dict:
    """No production DB, Discord login, answer generation, or real user messages."""
    scope = Scope(920001 if case.get("mode") == "server" else None, 930001, 910001)
    store = Store(":memory:")
    try:
        for index, turn in enumerate(case["turns"]):
            at = datetime.fromisoformat(turn["at"])
            message_id = time_snowflake(at) + index
            store.add(scope, message_id, turn["user"], turn["rio"],
                      turn_id=str(uuid5(NAMESPACE_URL, f"synthetic:{case['id']}:{index}")))
        if policy == "current":
            result = await llm.extract_structured_memory(store, scope)
        elif policy == "baseline":
            pending = store.pending_structured_memory(scope)
            payload = {"capture": "owner_private", "turns": [
                {"message_id": row["message_id"], "user": row["content"]} for row in pending]}
            response = await llm.usage.request(
                llm.memory_client, "structured_memory_shadow", model=llm.settings.memory_model,
                instructions=BASELINE_POLICY, input=json.dumps(payload, ensure_ascii=False),
                max_output_tokens=min(llm.settings.memory_output_tokens, 900), store=False,
                text={"format": RESPONSE_FORMAT},
            )
            if response.status != "completed":
                raise ValueError("Baseline extraction did not complete")
            result = store.save_structured_memory(
                scope, through_id=pending[-1]["id"], items=parse_items(response.output_text))
        else:
            raise ValueError("Unknown policy")
        rows = [dict(row) for row in store.active_structured_memory(scope)]
        # Store/arity checks do not establish semantic quality. Every live output needs review.
        return {"id": case["id"], "policy_version": POLICY_VERSION if policy == "current" else "baseline-v1",
                "provider": llm.settings.memory_provider or llm.settings.provider,
                "model": llm.settings.memory_model, "expected": case["expected"],
                "expected_count": case["expected_count"], "actual_count": len(rows),
                "count_matches": len(rows) == case["expected_count"],
                "manual_review_required": True, "extraction": result,
                "cursor_advanced": store.structured_memory_cursor(scope) == len(case["turns"]),
                "candidates": [{key: row[key] for key in (
                    "kind", "content", "confidence", "disclosure", "source_message_ids",
                    "source_turn_ids")} for row in rows]}
    finally:
        store.close()


def evaluation_settings(args) -> Settings:
    load_dotenv(Path.cwd() / ".env.local", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    provider = (args.provider or os.getenv("MEMORY_PROVIDER")
                or os.getenv("LLM_PROVIDER", "openai")).lower()
    if provider not in {"openai", "gemini", "openrouter"}:
        raise ValueError("Unsupported memory provider")
    model = args.model or os.getenv("MEMORY_MODEL")
    if not model:
        raise ValueError("Specify --model or MEMORY_MODEL; answer model is not assumed")
    key = _provider_key(provider)
    return Settings(key, "eval-only", provider=provider, memory_provider=provider,
                    model=model, memory_model=model, structured_memory_shadow=True,
                    usage_log_path=str(args.usage_log) if args.usage_log else "",
                    openai_api_key=key if provider == "openai" else "",
                    gemini_api_key=key if provider == "gemini" else "",
                    openrouter_api_key=key if provider == "openrouter" else "")


async def run(args) -> list[dict]:
    cases = read_cases(args.cases)
    if args.validate_only:
        print(json.dumps({"validated_cases": len(cases), "model_called": False}))
        return []
    settings = evaluation_settings(args)
    llm = LLM(replace(settings, db_path=":memory:"))
    results = []
    try:
        for case in cases:
            try:
                results.append(await run_case(llm, case, policy=args.policy))
            except Exception as exc:  # noqa: BLE001 - continue synthetic cases, no provider body logging
                results.append({"id": case["id"], "error_type": type(exc).__name__,
                                "count_matches": False, "manual_review_required": True})
    finally:
        await llm.close()
    # Only explicit evaluation artifacts of synthetic fixtures are written.
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in results))
    print(json.dumps({"cases": len(results), "count_matches": sum(r["count_matches"] for r in results),
                      "manual_review_required": True, "output": str(args.output)}))
    return results


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    value.add_argument("--policy", choices=("baseline", "current"), default="current")
    value.add_argument("--provider")
    value.add_argument("--model")
    value.add_argument("--usage-log", type=Path)
    value.add_argument("--output", type=Path, default=Path("data/evals/memory-extraction.jsonl"))
    value.add_argument("--validate-only", action="store_true")
    return value


def main():
    args = parser().parse_args()
    results = asyncio.run(run(args))
    if any(not row["count_matches"] for row in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
