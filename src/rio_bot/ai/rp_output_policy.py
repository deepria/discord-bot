import re
from enum import StrEnum

SOURCE_REQUEST_QUERY = re.compile(
    r"(?:출처|근거\s*(?:링크|주소)?|링크|소스|어디서\s*(?:봤|찾았|알았|확인))",
    re.IGNORECASE,
)


class ProvenanceMode(StrEnum):
    SILENT = "silent"
    NATURAL_LOOKUP = "natural_lookup"
    EXPLICIT_SOURCE = "explicit_source"


def provenance_mode(content: str, *, web_search: bool) -> ProvenanceMode:
    """Choose how retrieval provenance may appear in the RP response."""
    if SOURCE_REQUEST_QUERY.search(content):
        return ProvenanceMode.EXPLICIT_SOURCE
    if web_search:
        return ProvenanceMode.NATURAL_LOOKUP
    return ProvenanceMode.SILENT


def provenance_instruction(mode: ProvenanceMode) -> str:
    if mode == ProvenanceMode.EXPLICIT_SOURCE:
        return (
            "[출처 표시]\n"
            "사용자가 출처나 근거를 직접 물었습니다. 답변 자체는 리오의 말투와 1인칭을 유지하고, "
            "필요한 범위에서만 실제 확인한 출처나 링크를 짧게 덧붙일 수 있습니다. 검색 과정이나 "
            "도구 사용법을 설명하지 마세요."
        )
    if mode == ProvenanceMode.NATURAL_LOOKUP:
        return (
            "[자연스러운 외부 확인]\n"
            "이번 답변에서는 외부 확인을 했습니다. 질문 내용이 리오가 원래 직접 경험했거나 평소 "
            "알 법한 정보라면 확인 과정을 말하지 말고 바로 답하세요. 반대로 원래 알기 어려운 최신·"
            "외부 정보라면 필요할 때 한 번만 '잠깐 확인해봤는데', '자료를 좀 확인해보니까'처럼 "
            "세계 안에서 자연스럽게 조회한 듯 표현할 수 있습니다. 웹, 검색엔진, RAG, AI, 사이트명, "
            "링크, 인용 표시는 사용자가 출처를 요구하지 않은 한 드러내지 마세요."
        )
    return (
        "[출처 비노출]\n"
        "참고자료·기억·RAG 같은 내부 정보 획득 과정은 말하지 말고, 답변에 필요한 내용만 자연스럽게 "
        "사용하세요."
    )


def hide_web_citations(mode: ProvenanceMode) -> bool:
    """Only expose web citations when the user explicitly asks for sources."""
    return mode != ProvenanceMode.EXPLICIT_SOURCE
