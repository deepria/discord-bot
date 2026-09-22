"""Detect when a user is correcting a factual claim rather than continuing roleplay."""

import re

_FACTUAL_CHALLENGE = re.compile(
    r"(?:틀렸(?:어|잖|는데|다)?|잘못(?:됐|된|했|한)|오류|사실이\s*아니|"
    r"근거\s*없|지어냈|환각|없는\s*(?:설정|사실|기억)|말이\s*안\s*돼)",
    re.IGNORECASE,
)

FACTUAL_CHALLENGE_POLICY = """[사실 오류 지적]
사용자가 이전 답변의 사실 오류·근거 부족·지어낸 설정을 지적하고 있습니다. 불확실한 기억이나
근거 없는 사건·설정·대사로 방어하거나 변명하지 마세요. 제공된 근거를 다시 확인해 짧게 인정·정정하고,
확인할 수 없으면 모른다고 분명히 말하세요. 리오의 말투는 유지하되 사용자가 틀렸다는 식으로 회피하거나
새로운 과거 사건을 만들어 정당화하지 마세요.
"""


def is_factual_challenge(content: str) -> bool:
    return bool(_FACTUAL_CHALLENGE.search(content or ""))


__all__ = ["FACTUAL_CHALLENGE_POLICY", "is_factual_challenge"]
