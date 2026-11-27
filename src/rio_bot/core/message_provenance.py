"""Separate Discord dialogue from roleplay narration without losing scene order."""

import re

_SINGLE_LINE_QUOTE = re.compile(r"^ {0,3}>[ \t]+(.*)$")
_BLOCK_QUOTE = re.compile(r"^ {0,3}>>>[ \t]?(.*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")

# Accept complete, directly observable descriptions. Matching only the final verb would
# also accept private clauses such as "계좌를 조사하기로 다짐하며 웃었다".
_SCENE_SENTENCES = re.compile(r"(?<=[.!?。！？])\s+|\n")
_EMBEDDED_TEXT = re.compile(r"""["'“”‘’「」『』`()[\]<>]""")
_ACTOR = r"(?:[가-힣A-Za-z][가-힣A-Za-z0-9_-]{0,23}(?:은|는|이|가)\s+)?"
_MANNER = (
    r"(?:(?:조용히|작게|크게|살짝|가볍게|천천히|빠르게|갑자기|활짝|희미하게|"
    r"빙긋|피식|싱긋|씩|말없이|가만히|잠시|한숨을 쉬며)\s+)*"
)
_GESTURE = (
    r"(?:고개|머리|손|손가락|팔|어깨|눈|눈썹|눈살|미간|이마|얼굴|입|입술|입꼬리|주먹|"
    r"발|발걸음|몸|시선|안경|문|창문|커튼|책|컵|잔|휴대폰|폰|가방|서류|의자|우산|"
    r"책상|한숨|눈물|미소)(?:을|를)\s+"
)
_DIRECTION = (
    r"(?:(?:앞|뒤|옆|밖|안|창밖|바닥|천장|문|문밖|복도|출입구|리오|선생)"
    r"(?:\s+쪽(?:으로|에)|으로|로|을|를|에)?\s+)?"
)
_LOOK = r"(?:바라보며|내려다보며|올려다보며)\s+"
_VISIBLE_ACTION = (
    r"웃(?:는다|었다)|미소(?:를)? (?:짓는다|지었다)|울(?:었다|고 있다)|운다|"
    r"끄덕(?:였다|인다|거렸다|거린다)|흔들(?:었다|고 있다)|흔든다|"
    r"찡그(?:렸다|린다)|찌푸(?:렸다|린다)|치켜(?:떴다|뜬다)|깜빡(?:였다|인다)|"
    r"붉어졌다|붉어진다|떨(?:렸다|린다)|올라(?:갔다|간다)|"
    r"감는다|뜬다|올린다|내린다|편다|접는다|돌린다|숙인다|기울인다|"
    r"가리킨다|닦는다|쥔다|든다|놓는다|내민다|꺼낸다|펼친다|"
    r"감았다|떴다|올렸다|내렸다|폈다|접었다|돌렸다|숙였다|기울였다|가리켰다|"
    r"쥐었다|들었다|들어 올렸다|들어 올린다|놓았다|내밀었다|꺼냈다|펼쳤다|"
    r"열(?:었다|렸다|린다)|연다|닫(?:았다|는다|혔다|힌다)|"
    r"바라(?:봤다|본다)|내려다(?:봤다|본다)|올려다(?:봤다|본다)|"
    r"돌아(?:봤다|본다|섰다|선다)|본다|보았다|"
    r"쉬(?:었다|고 있다)|쉰다|"
    r"걷는다|걸(?:었다|어간다|어갔다|어온다|어왔다)|"
    r"다가(?:왔다|온다|갔다|간다)|물러(?:났다|난다)|"
    r"일어(?:났다|난다|섰다|선다)|앉(?:았다|는다)|"
    r"나(?:갔다|간다)|들어(?:왔다|온다|갔다|간다)|"
    r"들(?:렸다|린다)|울려 퍼(?:졌다|진다)|깜박(?:였다|인다)"
)
_OBSERVABLE = re.compile(
    rf"{_ACTOR}{_MANNER}{_DIRECTION}(?:{_LOOK}{_MANNER})?(?:{_GESTURE}{_MANNER})?"
    rf"(?:{_VISIBLE_ACTION})[.!?。！？…]*"
)


def message_segments(content: str) -> list[dict[str, str]]:
    """Parse quote markup as narration, except inside fenced code blocks.

    A >>> block consumes the rest of the message. Segment order is significant;
    the message author is the narrator, not necessarily the actor in the scene.
    """
    segments: list[dict[str, str]] = []
    in_block = False
    fence = ""
    for line in str(content or "").splitlines():
        kind, value = "dialogue", line
        marker = _FENCE.match(line)
        if in_block:
            kind = "narration"
        elif fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
        elif marker:
            fence = marker[1]
        else:
            block = _BLOCK_QUOTE.match(line)
            single = _SINGLE_LINE_QUOTE.match(line)
            if block:
                in_block = True
                kind, value = "narration", block[1]
            elif single:
                kind, value = "narration", single[1]
        if segments and segments[-1]["kind"] == kind:
            segments[-1]["content"] += "\n" + value
        else:
            segments.append({"kind": kind, "content": value})
    return [{**s, "content": s["content"].strip()} for s in segments if s["content"].strip()]


def split_inline_quotes(content: str) -> tuple[str, str]:
    """Compatibility helper: return dialogue and narration separately.

    Scene narration must never enter persistent memory as a user's factual statement.
    """
    segments = message_segments(content)
    return tuple("\n".join(s["content"] for s in segments if s["kind"] == kind)
                 for kind in ("dialogue", "narration"))


def observable_message_segments(segments: list[dict[str, str]]) -> list[dict[str, str]]:
    """Keep dialogue and unambiguously observable narration in their original order."""
    result = []
    for segment in segments:
        if segment["kind"] == "dialogue":
            result.append(segment)
            continue
        # Embedded text can contain sentence boundaries inside another speaker's thoughts.
        if _EMBEDDED_TEXT.search(segment["content"]):
            continue
        observable = [
            clause for part in _SCENE_SENTENCES.split(segment["content"])
            if _OBSERVABLE.fullmatch(clause := part.strip())
        ]
        if observable:
            result.append({"kind": "narration", "content": "\n".join(observable)})
    return result


def observable_discord_text(content: str, *, triggered_text: str | None = None) -> str:
    """Remove private narration before storing or truncating a Discord message."""
    segments = message_segments(content)
    if triggered_text is not None and triggered_text != content:
        # A call prefix or mention can precede the first quote marker on the same line.
        triggered_segments = message_segments(triggered_text)
        if any(segment["kind"] == "narration" for segment in triggered_segments):
            segments = triggered_segments
    if all(segment["kind"] == "dialogue" for segment in segments):
        return content
    return "\n".join(
        segment["content"] if segment["kind"] == "dialogue"
        else "\n".join(f"> {line}" for line in segment["content"].splitlines())
        for segment in observable_message_segments(segments)
    )


__all__ = [
    "message_segments", "observable_discord_text", "observable_message_segments",
    "split_inline_quotes",
]
