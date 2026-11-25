"""Classify whether a reply depends on the real world's current state."""

from __future__ import annotations

import re
from enum import StrEnum


class FreshnessMode(StrEnum):
    STATIC = "static"
    CLOCK = "clock"
    AUTO = "auto"
    REQUIRED = "required"


_DIRECT_CLOCK_QUERY = re.compile(
    r"(?:지금|현재)?\s*(?:몇\s*시|시간이\s*몇|현재\s*시각|현재\s*시간)|"
    r"(?:오늘|내일|모레)\s*(?:이\s*)?(?:몇\s*일|날짜|무슨\s*요일|요일)",
    re.IGNORECASE,
)
_TEMPORAL_MARKER = re.compile(
    r"(?:지금|현재|오늘|내일|모레|이번\s*(?:주|달|주말|연휴|분기|학기|시즌)|"
    r"다음\s*(?:주|달|주말)|올해|이번\s*해|최신|최근|요즘|방금|곧)",
    re.IGNORECASE,
)
_LIVE_DOMAIN = re.compile(
    r"(?:날씨|기온|온도|강수|비\s*(?:와|오|올)|눈\s*(?:와|오|올)|습도|미세먼지|예보|"
    r"환율|주가|주식|코스피|코스닥|나스닥|비트코인|코인|시세|유가|기름값|가격|"
    r"뉴스|속보|발표|선거|대통령|총리|정부|"
    r"경기\s*(?:일정|결과|점수)|스코어|순위|대진|"
    r"교통|지하철|버스|열차|기차|항공|비행기|운행|지연|결항|"
    r"영업시간|운영시간|문\s*(?:열|닫)|휴무|재고|품절|예약|"
    r"공휴일|연휴|추석|설날|설\s*연휴|크리스마스|휴일|"
    r"일정|행사|공연|콘서트|상영|개봉|출시|업데이트|패치|점검|장애|서비스\s*상태|"
    r"한섭|한국\s*서버|일섭|공개(?:됐|되었|된|일정))",
    re.IGNORECASE,
)
_LOCATION_DEPENDENT = re.compile(
    r"(?:날씨|기온|온도|강수|비\s*(?:와|오|올)|눈\s*(?:와|오|올)|습도|미세먼지|예보|"
    r"교통|지하철|버스|영업시간|운영시간|문\s*(?:열|닫)|휴무)",
    re.IGNORECASE,
)
_QUESTIONISH = re.compile(
    r"(?:\?|어때|어떻|뭐|무엇|누구|언제|어디|몇|얼마|왜|어떻게|"
    r"알려|찾아|확인|남았|맞아|있어|없어|되니|돼|해\s*$|야\s*$|니\s*$)",
    re.IGNORECASE,
)
_ROLEPLAY_NOW = re.compile(
    r"^\s*(?:리오야[,!\s]*)?(?:(?:너|넌|너는)\s*)?(?:지금|오늘|요즘)?\s*"
    r"(?:뭐\s*해|뭐\s*하고|어디야|바빠|자고\s*있|기분\s*어때)",
    re.IGNORECASE,
)
_LOCATION_DEICTIC = re.compile(
    r"(?:여기|이\s*근처|근처|주변|밖에?|내\s*위치|내가\s*있는\s*(?:곳|데)|"
    r"우리\s*동네|이쪽)",
    re.IGNORECASE,
)
_LOCATION_FILLER = re.compile(
    r"(?:지금|현재|오늘|내일|모레|날씨|기온|온도|강수|습도|미세먼지|예보|"
    r"비|눈|교통|지하철|버스|영업시간|운영시간|휴무|운행|"
    r"어때|어떻|어때요|알려줘|알려\s*줘|확인해줘|확인\s*해줘|"
    r"와|와요|오니|올까|와\?|오\?|열어|열었어|열었니|닫았어|정상이야|정상|"
    r"좀|혹시|오늘은|내일은)",
    re.IGNORECASE,
)


_LOCAL_BUSINESS = re.compile(
    r"(?:맛집|식당|음식점|카페|가게|매장|상호|밥집|"
    r"(?:파는|먹을|먹기\s*좋은|갈)\s*(?:곳|데|집))"
)
_LOCAL_REQUEST = re.compile(r"(?:추천|찾아|알려|있어|없어|어디|영업시간|몇\s*(?:곳|군데)|가볼|이름)")
_MAJOR_CITY = re.compile(r"\b(?:서울|부산|대구|인천|광주|대전|울산|세종|제주)(?=에|의|\s|$)")
_LOCAL_AREA_MARKER = re.compile(r"\b[가-힣A-Za-z0-9·-]+(?:역|동|구|시|군|읍|면|거리|시장)(?=에|에서|의|\s|$)")
_LOCAL_NEARBY = re.compile(r"(?:근처|주변|인근|부근|가까운|동네|이쪽|그쪽|거기|여기)")
_LOCAL_AREA_PREFIX = re.compile(
    r"([가-힣A-Za-z0-9·-]{2,}(?:[ \t]+[가-힣A-Za-z0-9·-]+){0,2})"
    r"\s*(?:근처|주변|인근|부근)"
)
_NON_AREAS = re.compile(
    r"(?:거기|여기|저기|이쪽|그쪽|동네|직장|회사|사무실|학교|집|근처|주변|"
    r"도시|전국|식당|음식점|카페|가게|매장|맛집)$"
)


def local_area(text: str) -> str:
    for match in _LOCAL_AREA_PREFIX.finditer(text):
        candidate = match.group(1).split()[-1]
        if not _NON_AREAS.fullmatch(candidate):
            return candidate
    marked = next((match[0] for match in _LOCAL_AREA_MARKER.finditer(text)
                   if not _NON_AREAS.fullmatch(match[0])), "")
    city = _MAJOR_CITY.search(text)
    return marked or (city[0] if city else "")


def is_local_recommendation(text: str) -> bool:
    if not _LOCAL_BUSINESS.search(text):
        return False
    has_area = local_area(text) or _LOCAL_NEARBY.search(text)
    if not has_area and _LOCATION_DEPENDENT.search(text) and not re.search(r"추천|몇\s*(?:곳|군데)", text):
        return False
    # A request for menu ideas stays general unless it actually asks about venues.
    return bool(_LOCAL_REQUEST.search(text) or re.search(r"(?:맛집|식당|카페)\s*[?？.!]*$", text))


def is_live_domain(content: str) -> bool:
    return bool(_LIVE_DOMAIN.search(content)) or is_local_recommendation(content)


def is_location_dependent(content: str) -> bool:
    return bool(_LOCATION_DEPENDENT.search(content))


def needs_location_clarification(content: str) -> bool:
    """Return True only for clearly location-dependent questions with no place hint.

    This intentionally recognizes only obvious omissions. Unknown place names should remain in
    the residue and therefore be treated as an explicit location rather than rejected here.
    """
    if not is_location_dependent(content):
        return False
    residue = _LOCATION_DEICTIC.sub(" ", content)
    residue = _LOCATION_FILLER.sub(" ", residue)
    residue = re.sub(r"[\s?!.。,，~…·]+", "", residue)
    return not bool(re.search(r"[0-9A-Za-z가-힣]", residue))


def classify_freshness(content: str) -> FreshnessMode:
    """Return a conservative freshness requirement for a user message.

    Hard-coded matches cover only high-confidence cases. Ambiguous temporal questions are
    AUTO so the model may decide whether the generic web tool is actually useful.
    """
    text = content.strip()
    if not text:
        return FreshnessMode.STATIC
    if _DIRECT_CLOCK_QUERY.search(text):
        return FreshnessMode.CLOCK
    if _ROLEPLAY_NOW.search(text):
        return FreshnessMode.STATIC

    if is_local_recommendation(text):
        return FreshnessMode.REQUIRED

    questionish = bool(_QUESTIONISH.search(text))
    if questionish and is_live_domain(text):
        return FreshnessMode.REQUIRED
    if questionish and _TEMPORAL_MARKER.search(text):
        return FreshnessMode.AUTO
    return FreshnessMode.STATIC
