"""Deterministic per-turn model and generation-budget routing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .freshness import FreshnessMode
from .information_plan import InformationPlan

_COMPLEX_TASK_REQUEST = re.compile(
    r"(?:(?:분석|비교|검토|평가|설계|구현|리팩터링?|디버깅|증명|유도)"
    r"(?:해|하(?:고|기|는|면|여|자|죠|세요|십시오)|해\s*(?:줘|주세요|줄래))|"
    r"(?:고쳐|수정해|개선해|해결해|최적화해)|"
    r"(?:원인|문제점|개선(?:할\s*)?(?:점|부분)|병목|취약점|오류|버그)"
    r".{0,24}(?:찾아|찾아봐|분석해|검토해|고쳐|수정해|개선해|해결해)|"
    r"(?:analy[sz]e|compare|review|design|implement|refactor|debug|fix|improve)"
    r"(?:\s+(?:this|it|the|my|our|please)|\b))",
    re.IGNORECASE,
)
_NEGATED_COMPLEX_TASK = re.compile(
    r"(?:분석|비교|검토|평가|설계|구현|리팩터링?|디버깅|증명|유도|"
    r"수정|개선|해결|최적화).{0,8}(?:하지\s*말|하지는\s*말|말고|빼고)",
    re.IGNORECASE,
)
_LONG_ANSWER_REQUEST = re.compile(
    r"(?:(?:자세히|구체적으로|깊이\s*있게|차근차근|단계별(?:로)?|빠짐없이)"
    r".{0,20}(?:설명|알려|정리|써|작성|답해)|"
    r"(?:긴\s*(?:답변|글)|보고서|튜토리얼|가이드).{0,16}(?:써|작성|만들|정리)|"
    r"전체(?:적으로)?\s*.{0,12}정리)",
    re.IGNORECASE,
)
_NEGATED_LONG_ANSWER = re.compile(
    r"(?:자세히|구체적으로|깊이\s*있게|차근차근|단계별(?:로)?|빠짐없이|"
    r"긴\s*(?:답변|글)|보고서|튜토리얼|가이드).{0,10}"
    r"(?:말하지\s*말|설명하지\s*말|하지\s*말|말고|빼고)",
    re.IGNORECASE,
)
_LISTED_REQUIREMENT = re.compile(r"(?:^|\n)\s*(?:[-*]|\d+[.)])\s+", re.MULTILINE)


class ModelTier(StrEnum):
    FIXED = "fixed"
    FAST = "fast"
    SMART = "smart"


@dataclass(frozen=True)
class ModelPlan:
    tier: ModelTier
    model: str
    max_output_tokens: int
    score: float
    smart_threshold: float
    reasons: tuple[str, ...]
    components: tuple[tuple[str, float], ...]
    policy: str

    def telemetry(self) -> dict:
        return {
            "model_tier": self.tier.value,
            "model_route_score": self.score,
            "model_route_threshold": self.smart_threshold,
            "model_route_reasons": list(self.reasons),
            "model_route_components": dict(self.components),
            "model_route_policy": self.policy,
            "requested_max_output_tokens": self.max_output_tokens,
        }


def _ramp(value: int, *, start: int, full: int, maximum: float) -> float:
    if value <= start:
        return 0.0
    if value >= full:
        return maximum
    return maximum * (value - start) / (full - start)


def _affirmative_match(text: str, pattern: re.Pattern, negated: re.Pattern) -> bool:
    return bool(pattern.search(negated.sub("", text)))


def _visual_count(rows: list[dict] | tuple[dict, ...] | None) -> int:
    count = 0
    for row in rows or ():
        count += int(bool(row.get("visual_inputs")))
        count += len(row.get("visual_inputs") or ())
    return count


def build_model_plan(
    settings,
    *,
    content: str,
    information: InformationPlan,
    channel_context: list[dict] | None = None,
    public_context: list[dict] | None = None,
    history_turns: int = 0,
    semantic_route: dict | None = None,
) -> ModelPlan:
    mode = str(getattr(settings, "model_routing_mode", "fixed")).strip().lower()
    if mode != "adaptive":
        return ModelPlan(
            tier=ModelTier.FIXED,
            model=settings.model,
            max_output_tokens=settings.output_tokens,
            score=0.0,
            smart_threshold=float(getattr(settings, "model_routing_smart_threshold", 2.0)),
            reasons=("fixed",),
            components=(),
            policy="fixed",
        )

    components: list[tuple[str, float]] = []
    reasons: list[str] = []

    text_len = len(content or "")
    length_score = round(_ramp(text_len, start=450, full=2600, maximum=1.4), 3)
    if length_score:
        components.append(("input_length", length_score))
        reasons.append("long_input")

    context_count = len(channel_context or ()) + len(public_context or ()) + history_turns
    context_score = round(min(1.0, context_count * 0.18), 3)
    if context_score:
        components.append(("context", context_score))
        reasons.append("context")

    if _affirmative_match(content, _COMPLEX_TASK_REQUEST, _NEGATED_COMPLEX_TASK):
        components.append(("complex_request", 1.2))
        reasons.append("complex_request")
    if _affirmative_match(content, _LONG_ANSWER_REQUEST, _NEGATED_LONG_ANSWER):
        components.append(("long_answer_request", 0.8))
        reasons.append("long_answer_request")

    listed = len(_LISTED_REQUIREMENT.findall(content or ""))
    if listed:
        components.append(("listed_requirements", min(0.9, listed * 0.25)))
        reasons.append("listed_requirements")

    if information.search_mode in {"auto", "required"}:
        components.append(("web_search", 0.8 if information.search_mode == "required" else 0.45))
        reasons.append("web_search")
    if information.fact_question:
        components.append(("world_fact", 0.35))
        reasons.append("world_fact")
    if len(information.references) >= 3:
        components.append(("lore_references", min(0.8, len(information.references) * 0.12)))
        reasons.append("lore_references")
    if information.freshness == FreshnessMode.REQUIRED:
        components.append(("freshness_required", 0.45))
        reasons.append("freshness_required")

    visuals = _visual_count(channel_context)
    if visuals:
        components.append(("visual_context", min(1.0, 0.35 + visuals * 0.15)))
        reasons.append("visual_context")

    semantic_mode = str(getattr(settings, "semantic_routing_mode", "off")).strip().lower()
    if semantic_mode == "active" and semantic_route:
        semantic_tier = str(semantic_route.get("tier", "")).strip().lower()
        confidence = semantic_route.get("confidence", 0.0)
        confidence = confidence if isinstance(confidence, (int, float)) else 0.0
        if semantic_tier == "smart" and confidence >= 0.5:
            components.append(("semantic_classifier", round(min(1.2, confidence * 1.2), 3)))
            reasons.append("semantic_classifier")
        elif semantic_tier == "fast" and confidence >= 0.75:
            components.append(("semantic_classifier_fast", -0.35))
            reasons.append("semantic_classifier_fast")

    score = round(sum(value for _, value in components), 3)
    threshold = float(getattr(settings, "model_routing_smart_threshold", 2.0))
    tier = ModelTier.SMART if score >= threshold else ModelTier.FAST
    model = settings.smart_model if tier == ModelTier.SMART else settings.fast_model
    max_output_tokens = (
        settings.smart_output_tokens if tier == ModelTier.SMART else settings.fast_output_tokens
    )

    return ModelPlan(
        tier=tier,
        model=model,
        max_output_tokens=max_output_tokens,
        score=score,
        smart_threshold=threshold,
        reasons=tuple(dict.fromkeys(reasons)) or ("simple",),
        components=tuple(components),
        policy="adaptive-v1",
    )


def build_shadow_model_plan(
    settings,
    *,
    content: str,
    information: InformationPlan,
    channel_context: list[dict] | None = None,
    public_context: list[dict] | None = None,
    history_turns: int = 0,
) -> ModelPlan:
    """Calculate the adaptive proposal even when production routing is fixed.

    This uses the established, bounded feature set but has no caller-visible side
    effects.  In particular it must not be used to select the actual request model.
    """
    class ShadowSettings:
        def __init__(self, source):
            self.source = source

        @property
        def model_routing_mode(self):
            return "adaptive"

        def __getattr__(self, name):
            return getattr(self.source, name)

    proposed = build_model_plan(
        ShadowSettings(settings),
        content=content,
        information=information,
        channel_context=channel_context,
        public_context=public_context,
        history_turns=history_turns,
    )
    return ModelPlan(
        tier=proposed.tier,
        model=proposed.model,
        max_output_tokens=proposed.max_output_tokens,
        score=proposed.score,
        smart_threshold=proposed.smart_threshold,
        reasons=proposed.reasons,
        components=proposed.components,
        policy="hybrid-v4-shadow",
    )


def build_memory_model_plan(settings, *, pending_count: int, payload_chars: int) -> ModelPlan:
    threshold = float(getattr(settings, "memory_routing_smart_threshold", 2.0))
    same_provider = (
        str(getattr(settings, "memory_provider", "") or getattr(settings, "provider", ""))
        == str(getattr(settings, "provider", ""))
    )
    adaptive = str(getattr(settings, "model_routing_mode", "fixed")).strip().lower() == "adaptive"
    if not adaptive or not same_provider:
        return ModelPlan(
            tier=ModelTier.FIXED,
            model=settings.memory_model,
            max_output_tokens=int(getattr(settings, "memory_output_tokens", 900)),
            score=0.0,
            smart_threshold=threshold,
            reasons=("memory_fixed",),
            components=(),
            policy="memory-fixed",
        )

    components: list[tuple[str, float]] = []
    if pending_count:
        components.append(("pending_turns", round(min(1.3, pending_count * 0.16), 3)))
    if payload_chars > 2500:
        components.append(("payload_chars", round(_ramp(payload_chars, start=2500, full=9000, maximum=1.4), 3)))
    score = round(sum(value for _, value in components), 3)
    tier = ModelTier.SMART if score >= threshold else ModelTier.FAST
    return ModelPlan(
        tier=tier,
        model=settings.smart_model if tier == ModelTier.SMART else settings.fast_model,
        max_output_tokens=int(getattr(settings, "memory_output_tokens", 900)),
        score=score,
        smart_threshold=threshold,
        reasons=tuple(name for name, value in components if value) or ("memory_simple",),
        components=tuple(components),
        policy="memory-adaptive-v1",
    )


__all__ = [
    "ModelPlan", "ModelTier", "build_memory_model_plan", "build_model_plan",
    "build_shadow_model_plan",
]
