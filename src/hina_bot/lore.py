import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

LANES = {"canon", "community_meme"}
KNOWLEDGE_LEVELS = {
    "self", "direct_experience", "reported", "public_knowledge", "inference",
    "audience_only", "unknown",
}
CONFIDENCE_LEVELS = {"verified", "official_secondary", "crosschecked", "candidate"}
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {"히나", "히나야", "소라사키", "블루", "아카이브", "뭐야", "알려줘", "어떻게"}


class LoreValidationError(ValueError):
    pass


def validate_record(record: dict, *, accepted: bool = False) -> dict:
    required = {"id", "lane", "summary", "keywords", "subjects", "knowledge", "confidence",
                "source", "status", "kr_release", "timeline"}
    missing = required - record.keys()
    if missing:
        raise LoreValidationError(f"{record.get('id', '<unknown>')}: missing {sorted(missing)}")
    if not isinstance(record["id"], str) or not re.fullmatch(r"[a-z0-9_.-]{3,100}", record["id"]):
        raise LoreValidationError("id는 영문 소문자·숫자·._- 형식이어야 합니다.")
    if record["lane"] not in LANES:
        raise LoreValidationError(f"invalid lane: {record['lane']}")
    if record["knowledge"] not in KNOWLEDGE_LEVELS:
        raise LoreValidationError(f"invalid knowledge: {record['knowledge']}")
    if record["confidence"] not in CONFIDENCE_LEVELS:
        raise LoreValidationError(f"invalid confidence: {record['confidence']}")
    if record["status"] not in {"candidate", "accepted", "rejected"}:
        raise LoreValidationError(f"invalid status: {record['status']}")
    if record["kr_release"] not in {"confirmed", "pending", "not_applicable"}:
        raise LoreValidationError(f"invalid kr_release: {record['kr_release']}")
    if record["lane"] == "canon" and accepted and record["kr_release"] != "confirmed":
        raise LoreValidationError(f"{record['id']}: Korean release must be confirmed")
    if record["lane"] == "community_meme" and record["kr_release"] != "not_applicable":
        raise LoreValidationError(f"{record['id']}: meme release status must be not_applicable")
    if accepted and (record["status"] != "accepted" or record["confidence"] == "candidate"):
        raise LoreValidationError(f"{record['id']}: runtime records must be reviewed and accepted")
    if not isinstance(record["summary"], str) or not 1 <= len(record["summary"].strip()) <= 600:
        raise LoreValidationError(f"{record['id']}: summary must be 1~600 chars")
    if not isinstance(record["timeline"], str) or not 1 <= len(record["timeline"].strip()) <= 120:
        raise LoreValidationError(f"{record['id']}: timeline must be 1~120 chars")
    for key in ("keywords", "subjects"):
        values = record[key]
        if not isinstance(values, list) or not values or len(values) > 30:
            raise LoreValidationError(f"{record['id']}: {key} must be a non-empty list")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 60 for value in values):
            raise LoreValidationError(f"{record['id']}: invalid {key}")
    source = record["source"]
    if not isinstance(source, dict) or not {"type", "title", "locator"} <= source.keys():
        raise LoreValidationError(f"{record['id']}: incomplete source")
    if record["lane"] == "community_meme" and not record.get("reaction"):
        raise LoreValidationError(f"{record['id']}: community meme needs a reaction guide")
    return record


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LoreValidationError(f"{path}:{number}: {exc}") from exc
    return rows


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                            for row in rows), encoding="utf-8")


@dataclass
class LoreIndex:
    records: list[dict]

    @classmethod
    def load(cls, path: str = "") -> "LoreIndex":
        target = Path(path) if path else files("hina_bot").joinpath("data/lore.jsonl")
        records = [validate_record(row, accepted=True) for row in read_jsonl(Path(target))]
        ids = [row["id"] for row in records]
        if len(ids) != len(set(ids)):
            raise LoreValidationError("runtime lore contains duplicate ids")
        return cls(records)

    @staticmethod
    def _terms(text: str) -> set[str]:
        return {token.casefold() for token in _TOKEN.findall(text) if token.casefold() not in _STOPWORDS}

    def search(self, query: str, *, limit: int = 6, chars: int = 3200,
               include_community: bool = True) -> list[dict]:
        if limit <= 0 or chars <= 0:
            return []
        folded = query.casefold()
        terms = self._terms(query)
        ranked = []
        for order, record in enumerate(self.records):
            if record["lane"] == "community_meme" and not include_community:
                continue
            score = 0
            for value in record["subjects"]:
                value = value.casefold()
                if value not in _STOPWORDS and value in folded:
                    score += 8 + min(len(value), 8)
            for value in record["keywords"]:
                value = value.casefold()
                if value in folded:
                    score += 5 + min(len(value), 8)
            score += 2 * len(terms & self._terms(record["summary"]))
            if score:
                ranked.append((score, -order, record))
        result, used = [], 0
        for _, _, record in sorted(ranked, reverse=True):
            item = {
                "id": record["id"], "lane": record["lane"], "summary": record["summary"],
                "knowledge": record["knowledge"], "confidence": record["confidence"],
                "timeline": record["timeline"],
                "canon_note": ("공식 설정 후보가 아니라 커뮤니티 밈 기반의 선택적 연출"
                               if record["lane"] == "community_meme" else "한국 서버 채택 설정"),
            }
            if record["lane"] == "community_meme":
                item["reaction"] = record["reaction"]
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > chars:
                continue
            result.append(item)
            used += size
            if len(result) >= limit:
                break
        return result
