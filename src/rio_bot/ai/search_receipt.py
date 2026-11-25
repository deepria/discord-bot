"""Search execution and evidence states for a delivered conversational answer."""

import re
from enum import StrEnum

from .web_search_text import _field, reported_web_searches

_AUDIT_QUERY = re.compile(
    r"(?:아까\s*|방금\s*|직전(?:에|\s*답변은)?\s*|그건\s*|그거\s*)?"
    r"(?:실제로\s*|진짜\s*|직접\s*)?"
    r"(?:검색(?:해\s*본|하고|해서|했|한)|찾아\s*(?:봤|보고|봐서|본)|확인(?:하고|했|한))"
    r"(?:\s*(?:어|어요|니|나|나요|(?:답|대답)(?:한|했어|했니)|말한|거야|건가|거예요))*"
    r"[\s?？.!。]*"
)


class SearchOutcome(StrEnum):
    UNKNOWN = "unknown"
    DISABLED = "disabled"
    NOT_RUN = "not_run"
    FAILED = "failed"
    RAN = "ran"
    NO_EVIDENCE = "no_evidence"
    PARTIAL = "partial"
    CONFIRMED = "confirmed"


_REPLY = {
    SearchOutcome.UNKNOWN: "직전 답변에서 실제로 검색했는지는 확인할 수 없어.",
    SearchOutcome.DISABLED: "직전에는 검색 기능이 꺼져 있어서 외부 자료를 확인하지 않았어.",
    SearchOutcome.NOT_RUN: "직전 답변에서는 검색하지 않았어.",
    SearchOutcome.FAILED: "직전에는 검색을 시도했지만 확인을 마치지 못했어.",
    SearchOutcome.RAN: "직전 답변에서 검색한 기록은 있어. 검색 기록만으로 각 내용의 근거까지 확인할 수는 없어.",
    SearchOutcome.NO_EVIDENCE: "직전에는 검색했어. 이름과 위치를 뒷받침하는 출처가 있는 매장은 확인하지 못했어.",
    SearchOutcome.PARTIAL: "직전에는 검색했고, 이름과 위치의 출처를 확인할 수 있는 매장만 보여줬어.",
    SearchOutcome.CONFIRMED: "직전에는 검색했고, 목록에 실은 매장마다 이름과 위치의 출처를 확인했어.",
}


def is_search_audit(text: str) -> bool:
    return bool(_AUDIT_QUERY.fullmatch(text.strip()))


def search_audit_reply(outcome: str | None) -> str:
    return _REPLY.get(outcome, _REPLY[SearchOutcome.UNKNOWN])


def search_execution(response, *, requested: bool, provider: str) -> SearchOutcome:
    if _field(response, "status") != "completed":
        return SearchOutcome.FAILED
    calls = [
        item for item in _field(response, "output", []) or []
        if _field(item, "type") == "web_search_call"
    ]
    if any(
        _field(item, "status", "completed") == "completed"
        and _field(_field(item, "action"), "type", "search") == "search"
        for item in calls
    ):
        return SearchOutcome.RAN
    if any(_field(item, "status") in {"failed", "incomplete", "in_progress"} for item in calls):
        return SearchOutcome.FAILED
    reported = reported_web_searches(response)
    if reported is not None:
        return SearchOutcome.RAN if reported else SearchOutcome.NOT_RUN
    # The existing OpenRouter plugin can return text without an execution item.
    if requested and provider == "openrouter":
        return SearchOutcome.UNKNOWN
    return SearchOutcome.NOT_RUN
