import re

SOURCE_REQUEST_QUERY = re.compile(
    r"(?:출처|근거\s*(?:링크|주소)?|링크|소스|어디서\s*(?:봤|찾았|알았|확인))",
    re.IGNORECASE,
)


def hide_web_citations(content: str) -> bool:
    """Hide retrieval provenance unless the user explicitly asks to see it."""
    return not bool(SOURCE_REQUEST_QUERY.search(content))
