"""Render only venue claims with associated provider citations."""

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from .web_search_text import _field

LOCAL_PLACE_POLICY = """[실제 매장 추천]
검색 도구를 사용해 확인하세요. local_recommendation_request의 현재 화자 요청과
지역만 검색 조건으로 사용하세요.
요청한 위치와 메뉴 조건을 충족하는 매장을 찾고, 매장별로 상호와 구체적인 주소를
한 줄에 '매장 | 상호(필요시 지점 포함) | 도로명과 번지' 형식으로 쓰세요. 각 줄 전체에
해당 상호와 주소를 함께 뒷받침하는 개별 매장 페이지의 실제 검색 인용을 붙이세요.
일반 지역 목록, 지역 소개 페이지, 검색 결과 목록은 근거로 사용하지 마세요.
확인된 추가 정보는 바로 다음 줄에 '메뉴 | 상호 | 값', '평점 | 상호 | 값',
'영업 | 상호 | 값' 형식으로 쓰고 각각에 그 값을 뒷받침하는 인용을 붙이세요.
한 줄에 한 항목만 쓰고, 마크다운 표나 별도의 링크 목록은 만들지 마세요.
검증할 수 있는 매장이 없으면 빈 목록을 반환하세요.
"""

_RECORD = re.compile(r"^\s*(?:[-*]\s+)?(매장|메뉴|평점|영업)\s*\|")
_LINK_TAIL = re.compile(r"(?:\s*\(?\[[^\n\]]+\]\(https?://[^\s)]+\)\)?)+\s*$")
_ADDRESS = re.compile(
    r"[가-힣0-9]+(?:로|길|번길)\s*\d+(?:-\d+)?(?=$|\s|,)|"
    r"\d+\s*(?:번지|번\s*출구)|"
    r"\b\d+\s+[^|]+\b(?:street|road|avenue|lane|boulevard|st\.?|rd\.?)\b",
    re.IGNORECASE,
)
_UNSAFE_TEXT = re.compile(r"[\[\]<>@`*_\\]|https?://", re.IGNORECASE)
_UNSAFE_URL = re.compile(r"[\s<>\\]|[\x00-\x1f\x7f]")


def _key(text: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if char.isalnum())


def _source_url(annotation, name: str) -> str:
    url = _field(annotation, "url", "")
    if not isinstance(url, str) or len(url) > 350 or _UNSAFE_URL.search(url):
        return ""
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
        return ""
    # A source must identify this venue in its title or in a dedicated page path.
    name_key = _key(name)
    title = _key(str(_field(annotation, "title", "") or ""))
    path_names = {_key(unquote(part)) for part in parsed.path.split("/")}
    if len(name_key) < 2 or (name_key not in title and name_key not in path_names):
        return ""
    return url


def _record_url(annotations, name, start, end, line_end, has_link):
    for annotation in annotations:
        if _field(annotation, "type") != "url_citation":
            continue
        first, last = _field(annotation, "start_index"), _field(annotation, "end_index")
        if type(first) is not int or type(last) is not int:
            continue
        covers_fields = start <= first <= start + 1 and end <= last <= line_end
        cites_line = has_link and end <= first < last <= line_end
        if covers_fields or cites_line:
            url = _source_url(annotation, name)
            if url:
                return url
    return ""


@dataclass
class Place:
    name: str
    address: str
    url: str
    details: dict[str, tuple[str, str]] = field(default_factory=dict)

    def render(self) -> str:
        lines = [f"- **{self.name}** · {self.address} · <{self.url}>"]
        lines.extend(f"  - {label} {value} · <{url}>"
                     for label, (value, url) in self.details.items())
        return "\n".join(lines)


def verified_places(response) -> tuple[str, int, bool]:
    """Return Discord text, visible venue count, and whether any records were omitted."""
    places: dict[str, Place] = {}
    used_urls: set[str] = set()
    omitted = False
    for item in _field(response, "output", []) or []:
        if _field(item, "type") != "message":
            continue
        for part in _field(item, "content", []) or []:
            if _field(part, "type") != "output_text":
                continue
            raw = _field(part, "_rio_grounded_text", _field(part, "text", "")) or ""
            annotations = _field(part, "_rio_grounded_annotations", _field(part, "annotations", []))
            annotations = annotations or []
            offset = 0
            for line in raw.splitlines(keepends=True):
                start = offset
                offset += len(line)
                line = line.rstrip("\r\n")
                record = _RECORD.match(line)
                if not record:
                    continue
                link = _LINK_TAIL.search(line)
                body = line[:link.start()] if link else line
                values = body.split("|")
                if len(values) != 3:
                    omitted = True
                    continue
                kind = record[1]
                name, value = (part.strip() for part in values[1:])
                if not name or not value or len(name) > 80 or len(value) > 120 or (
                    _UNSAFE_TEXT.search(name) or _UNSAFE_TEXT.search(value)
                ):
                    omitted = True
                    continue
                # Provider spans may start at the label, the leading space, or the entity name.
                name_start = start + body.index("|") + 1
                name_start += len(values[1]) - len(values[1].lstrip())
                ends_at = start + len(body.rstrip())
                url = _record_url(annotations, name, start + len(line) - len(line.lstrip()),
                                  ends_at, start + len(line), bool(link))
                if not url:
                    url = _record_url(annotations, name, name_start, ends_at,
                                      start + len(line), False)
                key = _key(name)
                if not url:
                    omitted = True
                elif kind == "매장":
                    if key not in places and url not in used_urls and _ADDRESS.search(value):
                        places[key] = Place(name, value, url)
                        used_urls.add(url)
                    else:
                        omitted = True
                elif key in places:
                    places[key].details.setdefault(kind, (value, url))
                else:
                    omitted = True

    rendered = []
    for place in places.values():
        row = place.render()
        if len(rendered) >= 4 or sum(map(len, rendered)) + len(row) + len(rendered) > 1650:
            omitted = True
            continue
        rendered.append(row)
    return "\n".join(rendered), len(rendered), omitted
