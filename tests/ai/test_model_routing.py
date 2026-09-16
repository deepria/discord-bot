from rio_bot.ai.freshness import FreshnessMode
from rio_bot.ai.information_plan import InformationPlan
from rio_bot.ai.information_routing import InformationRoute
from rio_bot.ai.model_routing import ModelTier, build_model_plan
from rio_bot.ai.routing_plan import RoutingPlan
from rio_bot.ai.rp_output_policy import ProvenanceMode
from rio_bot.core.config import Settings


def _information(**overrides):
    values = {
        "routing": RoutingPlan("리오야 안녕", "안녕"),
        "route": InformationRoute.GENERAL,
        "references": (),
        "freshness": FreshnessMode.NONE,
        "fact_question": False,
        "search_mode": "none",
        "provenance": ProvenanceMode.SILENT,
    }
    values.update(overrides)
    return InformationPlan(**values)


def test_fixed_model_routing_preserves_existing_settings():
    settings = Settings("key", "token", model="fixed-model", output_tokens=777)

    plan = build_model_plan(settings, content="짧은 인사", information=_information())

    assert plan.tier == ModelTier.FIXED
    assert plan.model == "fixed-model"
    assert plan.max_output_tokens == 777


def test_adaptive_model_routing_uses_smart_for_complex_contextual_turn():
    settings = Settings(
        "key",
        "token",
        model="base-model",
        model_routing_mode="adaptive",
        model_routing_smart_threshold=1.5,
        fast_model="fast-model",
        smart_model="smart-model",
        fast_output_tokens=1000,
        smart_output_tokens=4000,
    )
    information = _information(
        references=({"kind": "world_fact"}, {"kind": "world_fact"}, {"kind": "world_fact"}),
        fact_question=True,
        search_mode="required",
    )

    plan = build_model_plan(
        settings,
        content="리오 관점에서 이 사건의 문제점을 자세히 분석해줘",
        information=information,
        channel_context=[{"content": "리오야 이전 말"}],
    )

    assert plan.tier == ModelTier.SMART
    assert plan.model == "smart-model"
    assert plan.max_output_tokens == 4000
    assert "complex_request" in plan.reasons


def test_adaptive_model_routing_uses_fast_for_simple_turn():
    settings = Settings(
        "key",
        "token",
        model="base-model",
        model_routing_mode="adaptive",
        fast_model="fast-model",
        smart_model="smart-model",
    )

    plan = build_model_plan(settings, content="안녕", information=_information())

    assert plan.tier == ModelTier.FAST
    assert plan.model == "fast-model"
