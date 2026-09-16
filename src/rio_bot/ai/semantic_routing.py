"""Optional lightweight semantic routing classifier."""

from __future__ import annotations

import json
import logging

log = logging.getLogger("rio")

CLASSIFIER_POLICY = """사용자 요청을 fast 또는 smart 중 하나로 분류하세요.
fast: 짧은 잡담, 단순 확인, 간단한 역할극 반응.
smart: 복잡한 분석, 최신/세계관 사실 검증, 긴 맥락 통합, 이미지/여러 자료 해석, 실수 위험이 큰 질문.
JSON만 출력하세요: {"tier":"fast|smart","confidence":0.0~1.0,"reasons":["짧은 한국어 이유"]}"""


def _safe_parse(text: str) -> dict | None:
    try:
        data = json.loads(text.strip())
    except (TypeError, ValueError):
        return None
    tier = str(data.get("tier", "")).strip().lower()
    confidence = data.get("confidence", 0.0)
    reasons = data.get("reasons", [])
    if tier not in {"fast", "smart"} or not isinstance(confidence, (int, float)):
        return None
    if not isinstance(reasons, list):
        reasons = []
    return {
        "tier": tier,
        "confidence": max(0.0, min(1.0, float(confidence))),
        "reasons": [str(reason)[:80] for reason in reasons[:4]],
    }


async def classify_semantic_route(usage, client, settings, content: str, information) -> dict | None:
    mode = str(getattr(settings, "semantic_routing_mode", "off")).strip().lower()
    if mode not in {"shadow", "active"}:
        return None
    if str(getattr(settings, "model_routing_mode", "fixed")).strip().lower() != "adaptive":
        return None
    payload = {
        "content": (content or "")[:5000],
        "route": getattr(getattr(information, "route", None), "value", str(getattr(information, "route", ""))),
        "freshness": getattr(getattr(information, "freshness", None), "value", str(getattr(information, "freshness", ""))),
        "fact_question": bool(getattr(information, "fact_question", False)),
        "search_mode": str(getattr(information, "search_mode", "")),
        "reference_count": len(getattr(information, "references", ()) or ()),
    }
    try:
        response = await usage.request(
            client,
            "route_classify",
            model=getattr(settings, "semantic_routing_model", "") or getattr(settings, "fast_model", settings.model),
            instructions=CLASSIFIER_POLICY,
            input=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            max_output_tokens=180,
            store=False,
        )
    except Exception as exc:  # noqa: BLE001 - classifier must never break the answer path.
        log.warning("Semantic route classifier skipped (%s)", type(exc).__name__)
        return None
    return _safe_parse(getattr(response, "output_text", ""))


__all__ = ["CLASSIFIER_POLICY", "classify_semantic_route"]
