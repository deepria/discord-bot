"""Conservative checks on the user's authored evidence, independent of extractor confidence."""

import re

from .message_provenance import split_inline_quotes

_DURABLE = re.compile(r"(?:앞으로|앞으론|이제부터|이후로|다음부터|계속|매번|항상|평소)")
_BOUNDARY = re.compile(
    r"(?:꺼내지|언급하지|분석하지|말하지|하지|부르지|묻지|추천하지)\s*마|"
    r"(?:불러|말해|해|써|유지해)\s*(?:줘|주세요)"
)
_PASSING_MOOD = re.compile(
    r"^(?:오늘(?:은|만)?|지금(?:은)?|방금)[,\s]+.{0,50}"
    r"(?:기분|짜증|화가|우울|피곤|서운|신나|지쳤|슬퍼)"
)
_REPORTED = re.compile(
    r"(?:라고|라며|다고|다는)\s*(?:말했|했대|하더라|들었|써있|적혀)|"
    r"^(?:친구|동료|다른\s*사람|그\s*사람)(?:가|이|는|은)[,\s]"
)


def evidence_rejection(content: str) -> str | None:
    dialogue, narration = split_inline_quotes(content)
    if narration or not dialogue or dialogue.lstrip().startswith(('>', '"', "'", '“', '‘', '`')):
        return "quoted_or_narrated_source"
    if _REPORTED.search(dialogue):
        return "reported_speech_source"
    if _PASSING_MOOD.search(dialogue) and not _DURABLE.search(dialogue):
        return "temporary_mood_source"
    return None


def explicit_boundary(content: str) -> bool:
    return bool(_DURABLE.search(content) and _BOUNDARY.search(content))
