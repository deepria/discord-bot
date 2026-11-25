"""Explicit per-turn routing plan shared by the production LLM pipeline."""

from dataclasses import dataclass

from .contextual_routing import (
    build_query,
    can_inherit_local_anchor,
    find_anchor,
    find_local_anchor,
    is_followup,
    is_local_followup,
    local_area_answer,
)
from .freshness import is_local_recommendation, local_area


@dataclass(frozen=True)
class RoutingPlan:
    """Separate the literal user turn from text used only for routing decisions."""

    visible_content: str
    routing_query: str
    anchor: str = ""

    @property
    def expanded(self) -> bool:
        return self.routing_query != self.visible_content


def build_routing_plan(
    store,
    scope,
    content: str,
    channel_context: list[dict] | None = None,
    *,
    use_memory: bool = True,
) -> RoutingPlan:
    rows = channel_context or []
    if local_area_answer(content):
        anchor = find_local_anchor(store, scope, rows, use_memory=use_memory)
        if is_local_recommendation(anchor) and not local_area(anchor):
            return RoutingPlan(content, build_query(content, anchor), anchor)
    if is_local_followup(content):
        anchor = find_local_anchor(store, scope, rows, use_memory=use_memory)
        query = (
            build_query(content, anchor)
            if not local_area(content) and can_inherit_local_anchor(content, anchor)
            else content
        )
        return RoutingPlan(content, query, anchor)
    anchor = (
        find_anchor(store, scope, rows, use_memory=use_memory)
        if is_followup(content)
        else ""
    )
    return RoutingPlan(
        visible_content=content,
        routing_query=build_query(content, anchor),
        anchor=anchor,
    )


__all__ = ["RoutingPlan", "build_routing_plan"]
