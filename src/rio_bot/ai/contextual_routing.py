"""Helpers for short follow-up routing without changing the visible user message."""

import re

from ..core.message_provenance import split_inline_quotes
from .freshness import is_local_recommendation, local_area
from .rp_output_policy import SOURCE_REQUEST_QUERY
from .search_receipt import is_search_audit

_FOLLOWUP = re.compile(
    r"^\s*(?:그럼|그러면|그렇다면|그래서|근데|그런데|"
    r"걔|얘|쟤|그거|그건|그게|그걸|그때|거기|그쪽|"
    r"오늘은|내일은|모레는|왜|언제|어디|누구|뭐|무엇|어떻게|얼마)",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"(?:[?？]|뭐|무엇|왜|언제|어디|누구|어떻게|얼마|어때|어떻|있어|없어|해|야|니)\s*$",
    re.IGNORECASE,
)
_PREFIX = re.compile(r"^\s*(?:그럼|그러면|그렇다면|그래서|근데|그런데)\s*", re.IGNORECASE)
_TOPIC_PARTICLE = re.compile(
    r"^(오늘|내일|모레|날씨|기온|온도|강수|습도|예보|거기)"
    r"(?:은|는|도)(?=\s|[?？!.~]|$)"
)
_LOCAL_LIST_FOLLOWUP = re.compile(
    r"^(?:(?:가게|매장|식당)\s*(?:이름|상호)(?:도|은|는|만)?|"
    r"(?:리스트|목록)(?:로|도)?)"
    r"(?:\s*(?:보여|정리해|알려|해)\s*줘)?[\s?？.!。]*$"
)
_LOCAL_DEICTIC_FOLLOWUP = re.compile(
    r"^(?:(?:그럼|그러면|그래서)[,\s]+)?(?:거기|그쪽|그\s*근처)"
)
_AREA_ANSWER_END = re.compile(r"(?:으로|에서|\s*근처)?[\s.!。]*$")


def is_local_list_followup(text: str) -> bool:
    return bool(_LOCAL_LIST_FOLLOWUP.fullmatch(_PREFIX.sub("", text.strip())))


def is_local_followup(text: str) -> bool:
    return is_local_list_followup(text) or (
        bool(_LOCAL_DEICTIC_FOLLOWUP.search(text.strip())) and is_local_recommendation(text)
    )


def local_area_answer(text: str) -> str:
    candidate = _AREA_ANSWER_END.sub("", text.strip())
    return candidate if candidate and local_area(candidate) == candidate else ""


def find_local_anchor(store, scope, rows, *, use_memory: bool) -> str:
    """Use the latest substantive direct turn by this speaker in this conversation."""
    saved = store.history(scope) if use_memory else ()
    turns = {int(row["message_id"]): row["content"] for row in saved
             if row["message_id"].isascii() and row["message_id"].isdecimal()}
    for row in rows:
        message_id = str(row.get("message_id") or "")
        author_id = str(row.get("author_user_id") or row.get("user_id") or "")
        if (
            message_id.isascii() and message_id.isdecimal()
            and row.get("role") == "user"
            and author_id == str(scope.user_id)
            and str(row.get("channel_id")) == str(scope.channel_id)
            and row.get("context_kind") == "speaker_thread"
            and row.get("direct_trigger") is True
            and not row.get("context_truncated")
        ):
            authored, _ = split_inline_quotes(row.get("content") or "")
            turns.setdefault(int(message_id), authored)
    supplied_area = ""
    for _, text in sorted(turns.items(), reverse=True):
        text = text.strip()[:800]
        if not text or is_search_audit(text):
            continue
        named_area = local_area_answer(text)
        if named_area:
            if not supplied_area:
                supplied_area = named_area
            continue
        if supplied_area and is_local_recommendation(text) and not local_area(text):
            return f"{supplied_area}\n{text}"
        if not is_local_followup(text):
            return text
    return ""


def can_inherit_local_anchor(text: str, anchor: str) -> bool:
    if not local_area(anchor):
        return False
    return not is_local_list_followup(text) or is_local_recommendation(anchor)


def is_followup(text: str) -> bool:
    value = text.strip()
    return bool(
        value
        and len(value) <= 300
        and _FOLLOWUP.search(value)
        and _QUESTION.search(value)
    )


def find_anchor(store, scope, rows: list[dict], *, use_memory: bool) -> str:
    for row in reversed(rows):
        if row.get("context_kind") == "replied_message":
            value, _ = split_inline_quotes(str(row.get("content", "")))
            if value:
                return value[:800]

    speaker = str(scope.user_id)
    for row in reversed(rows):
        if row.get("role") != "user" or row.get("context_kind") == "target_user_history":
            continue
        author = row.get("author_user_id") or row.get("user_id")
        if str(author or "") == speaker:
            value, _ = split_inline_quotes(str(row.get("content", "")))
            if value:
                return value[:800]

    if use_memory:
        turns = store.history(scope)
        if turns:
            return str(turns[-1]["content"]).strip()[:800]
    return ""


def build_query(content: str, anchor: str) -> str:
    if not anchor:
        return content
    # A prior request for a citation should not make the new turn a citation request too.
    topic = SOURCE_REQUEST_QUERY.sub(" ", anchor)
    followup = _PREFIX.sub("", content.strip())
    followup = _TOPIC_PARTICLE.sub(r"\1", followup)
    suffix = "\n" + followup
    return topic[:max(0, 1400 - len(suffix))] + suffix
