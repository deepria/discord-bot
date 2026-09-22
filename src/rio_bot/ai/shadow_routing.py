"""Shadow-only routing proposals for evaluating future routing policies.

The proposal is deliberately deterministic and never changes a provider request.  It
also only returns routing metadata: callers must not attach user text, prompts, or
image data to the resulting telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass

from .freshness import FreshnessMode
from .information_routing import InformationRoute
from .model_routing import ModelPlan, build_shadow_model_plan


@dataclass(frozen=True)
class ShadowWebPlan:
    mode: str
    reasons: tuple[str, ...]
    fallback_reason: str | None = None

    def telemetry(self) -> dict:
        return {
            "shadow_web_mode": self.mode,
            "shadow_web_reasons": list(self.reasons),
            "shadow_web_fallback_reason": self.fallback_reason,
        }


def build_shadow_web_plan(information) -> ShadowWebPlan:
    """Propose a web mode without calling a classifier or a web provider."""
    route = getattr(information, "route", None)
    freshness = getattr(information, "freshness", None)
    references = getattr(information, "references", ()) or ()

    if route in {InformationRoute.MEMORY, InformationRoute.CLOCK, InformationRoute.LOCAL_LORE}:
        return ShadowWebPlan("none", ("local_or_personal_context",))
    if freshness == FreshnessMode.REQUIRED:
        return ShadowWebPlan("required", ("freshness_required",))
    if bool(getattr(information, "fact_question", False)) and not references:
        return ShadowWebPlan("required", ("unresolved_world_fact",))
    if freshness == FreshnessMode.AUTO:
        return ShadowWebPlan("optional", ("freshness_ambiguous",))
    return ShadowWebPlan("none", ("no_external_evidence_signal",))


def shadow_telemetry(
    settings,
    *,
    content: str,
    information,
    channel_context: list[dict] | None = None,
    public_context: list[dict] | None = None,
    history_turns: int = 0,
) -> dict:
    """Return enabled proposal telemetry, with no effect on the answer path."""
    telemetry: dict = {}
    if bool(getattr(settings, "model_routing_shadow", False)):
        proposal: ModelPlan = build_shadow_model_plan(
            settings,
            content=content,
            information=information,
            channel_context=channel_context,
            public_context=public_context,
            history_turns=history_turns,
        )
        telemetry.update({
            "shadow_model_tier": proposal.tier.value,
            "shadow_model_score": proposal.score,
            "shadow_model_threshold": proposal.smart_threshold,
            "shadow_model_reasons": list(proposal.reasons),
            "shadow_model_components": dict(proposal.components),
            "shadow_model_policy": proposal.policy,
        })
    if bool(getattr(settings, "semantic_web_routing_shadow", False)):
        telemetry.update(build_shadow_web_plan(information).telemetry())
    return telemetry


__all__ = ["ShadowWebPlan", "build_shadow_web_plan", "shadow_telemetry"]
