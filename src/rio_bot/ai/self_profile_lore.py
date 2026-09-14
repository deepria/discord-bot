"""Small authoritative self-profile facts that must not depend on public web lookup."""

_BIRTHDAY_REFERENCE = {
    "reference": "local_profile.rio.birthday",
    "kind": "world_fact",
    "content": "츠카츠키 리오의 생일은 6월 6일이다.",
    "awareness": "self",
    "time": "프로필 상시 설정",
}


def fallback_references(query: str) -> list[dict]:
    """Return stable profile facts missing from the main lexical lore index.

    This is intentionally tiny. The normal curated lore index remains the primary source; this
    fallback only prevents a stable self fact from silently becoming a web-search dependency.
    """
    if "생일" in query:
        return [dict(_BIRTHDAY_REFERENCE)]
    return []
