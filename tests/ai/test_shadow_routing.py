from rio_bot.ai.freshness import FreshnessMode
from rio_bot.ai.information_plan import InformationPlan
from rio_bot.ai.information_routing import InformationRoute
from rio_bot.ai.routing_plan import RoutingPlan
from rio_bot.ai.rp_output_policy import ProvenanceMode
from rio_bot.ai.shadow_routing import build_shadow_web_plan, shadow_telemetry
from rio_bot.core.config import Settings


def _information(**overrides):
    values = {
        "routing": RoutingPlan("리오야 안녕", "안녕"),
        "route": InformationRoute.GENERAL,
        "references": (),
        "freshness": FreshnessMode.STATIC,
        "fact_question": False,
        "search_mode": "none",
        "provenance": ProvenanceMode.SILENT,
    }
    values.update(overrides)
    return InformationPlan(**values)


def test_shadow_model_proposal_runs_while_actual_routing_stays_fixed():
    settings = Settings(
        "key", "token", model="fixed", model_routing_mode="fixed",
        model_routing_shadow=True, model_routing_smart_threshold=1.0,
        fast_model="fast", smart_model="smart",
    )

    telemetry = shadow_telemetry(
        settings,
        content="이 사건의 문제점을 자세히 분석해줘",
        information=_information(search_mode="required"),
    )

    assert telemetry["shadow_model_tier"] == "smart"
    assert telemetry["shadow_model_policy"] == "hybrid-v4-shadow"
    assert "model" not in telemetry


def test_shadow_web_routing_uses_only_metadata_and_does_not_copy_content():
    proposal = build_shadow_web_plan(_information(freshness=FreshnessMode.REQUIRED))

    assert proposal.mode == "required"
    assert proposal.telemetry() == {
        "shadow_web_mode": "required",
        "shadow_web_reasons": ["freshness_required"],
        "shadow_web_fallback_reason": None,
    }


def test_disabled_shadow_flags_emit_no_telemetry():
    assert shadow_telemetry(Settings("key", "token"), content="안녕", information=_information()) == {}
