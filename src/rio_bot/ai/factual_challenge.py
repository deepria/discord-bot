"""Detect when a user is correcting a factual claim rather than continuing roleplay."""

import re

_FACTUAL_CHALLENGE = re.compile(
    r"(?:틀렸(?:어|잖|는데|다)?|잘못(?:됐|된|했|한)|오류|사실이\s*아니|"
    r"근거\s*없|지어냈|환각|없는\s*(?:설정|사실|기억)|말이\s*안\s*돼)",
    re.IGNORECASE,
)

_SPEAKER_ATTRIBUTION_CORRECTION = re.compile(
    r"(?:그(?:건|거)는?\s*내가\s*(?:물|말|한)|내가\s*(?:물었|말했|한)\s*(?:거|걸|건)|"
    r"(?:A|B)가\s*아니라\s*내가\s*(?:물|말|한)|화자를\s*잘못\s*(?:알|봤|짚))",
    re.IGNORECASE,
)

FACTUAL_CHALLENGE_POLICY = """[사실 오류 지적]
사용자가 이전 답변의 사실 오류·근거 부족·지어낸 설정을 지적하고 있습니다. 불확실한 기억이나
근거 없는 사건·설정·대사로 방어하거나 변명하지 마세요. 제공된 근거를 다시 확인해 짧게 인정·정정하고,
확인할 수 없으면 모른다고 분명히 말하세요. 리오의 말투는 유지하되 사용자가 틀렸다는 식으로 회피하거나
새로운 과거 사건을 만들어 정당화하지 마세요.
"""

SPEAKER_ATTRIBUTION_CORRECTION_POLICY = """[화자 귀속 정정]
사용자가 이전 응답에서 발화자를 잘못 귀속했다고 정정하고 있습니다. 먼저 오류를 분명히
인정하고 사과하세요. 이어서 잘못 연결한 귀속을 철회한 뒤, `channel_recent_messages`와
명시적 답글/인용 provenance로 확인되는 범위에서 각 화자의 발화를 다시 구분하세요. 확인할 수
없는 부분은 추측하지 마세요. 관계성 호칭이나 새 이야기를 덧붙여 오류를 덮지 마세요.
"""


def is_factual_challenge(content: str) -> bool:
    return bool(_FACTUAL_CHALLENGE.search(content or ""))


def is_speaker_attribution_correction(content: str) -> bool:
    return bool(_SPEAKER_ATTRIBUTION_CORRECTION.search(content or ""))


__all__ = [
    "FACTUAL_CHALLENGE_POLICY",
    "SPEAKER_ATTRIBUTION_CORRECTION_POLICY",
    "is_factual_challenge",
    "is_speaker_attribution_correction",
]
