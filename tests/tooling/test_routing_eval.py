import json
from pathlib import Path

import pytest

from rio_bot.core.config import Settings
from rio_bot.tooling.routing_eval import evaluate, read_cases


def test_routing_shadow_fixtures_pass():
    cases = read_cases(Path("evals/routing_shadow_cases.jsonl"))
    results = [evaluate(case, Settings("eval-only", "eval-only", fast_model="fast", smart_model="smart")) for case in cases]

    assert len(results) == 7
    assert all(row["pass"] for row in results)
    assert all("content" not in row for row in results)


def test_routing_eval_rejects_invalid_expected_value(tmp_path):
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps({"id": "bad", "content": "안녕", "route": "general",
                                "freshness": "static", "search_mode": "none",
                                "expected": {"tier": "fixed", "web": "none"}}), encoding="utf-8")

    with pytest.raises(ValueError, match="expected"):
        read_cases(path)
