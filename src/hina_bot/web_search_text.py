import re

_DOMAIN_CITATION = re.compile(r"\s*\((?:www\.)?(?:[\w-]+\.)+[a-z]{2,}(?:/[^\s)]*)?\)", re.IGNORECASE)


def _field(value, name, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _remove_annotation_spans(text: str, annotations) -> str:
    spans = []
    for annotation in annotations or []:
        if _field(annotation, "type") != "url_citation":
            continue
        start = _field(annotation, "start_index")
        end = _field(annotation, "end_index")
        if isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= len(text):
            spans.append((start, end))
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + text[end:]
    return text


def response_text(response, *, hide_citations: bool = False) -> str:
    fallback = (_field(response, "output_text", "") or "").strip()
    if not hide_citations:
        return fallback

    pieces = []
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for part in _field(item, "content", []) or []:
            if _field(part, "type") != "output_text":
                continue
            text = _field(part, "text", "") or ""
            text = _remove_annotation_spans(text, _field(part, "annotations", []))
            pieces.append(text)

    text = "".join(pieces).strip() if pieces else fallback
    text = _DOMAIN_CITATION.sub("", text)
    text = re.sub(r"[ \t]+([,.!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
