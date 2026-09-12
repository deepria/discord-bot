from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

KNOWLEDGE_LEVELS = {
    "self", "direct_experience", "reported", "public_knowledge", "inference",
    "audience_only", "unknown",
}
MAX_ITEMS = 100
MAX_CONTENT_CHARS = 1800
MAX_VALUES = 20
MAX_VALUE_CHARS = 60
_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_STOPWORDS = {"히나", "히나야", "소라사키", "블루", "아카이브", "뭐야", "알려줘", "어떻게"}


def _split_values(value: str) -> list[str]:
    rows = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not rows or len(rows) > MAX_VALUES:
        raise ValueError(f"쉼표로 구분한 1~{MAX_VALUES}개 값을 입력해 주세요.")
    if any(len(row) > MAX_VALUE_CHARS for row in rows):
        raise ValueError(f"각 키워드/대상은 {MAX_VALUE_CHARS}자 이하여야 합니다.")
    return rows


def _terms(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN.findall(text)
            if token.casefold() not in _STOPWORDS}


class RuntimeKnowledgeRegistry:
    """Admin-managed facts or interpretations, loaded on every search for live tuning."""

    def __init__(self, path: str, *, kind: str):
        if kind not in {"world_fact", "interpretation"}:
            raise ValueError("runtime knowledge kind must be world_fact or interpretation")
        self.path = Path(path) if path else None
        self.kind = kind

    @staticmethod
    def validate_id(identifier: str) -> str:
        identifier = identifier.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{1,63}", identifier):
            raise ValueError("ID는 영문 소문자·숫자·점·밑줄·하이픈 2~64자로 입력해 주세요.")
        return identifier

    @staticmethod
    def validate_content(content: str) -> str:
        content = content.strip()
        if not 1 <= len(content) <= MAX_CONTENT_CHARS:
            raise ValueError(f"본문은 1~{MAX_CONTENT_CHARS}자로 입력해 주세요.")
        return content

    @staticmethod
    def validate_timeline(timeline: str) -> str:
        timeline = timeline.strip() or "시점 미지정"
        if len(timeline) > 120:
            raise ValueError("timeline은 120자 이하여야 합니다.")
        return timeline

    @staticmethod
    def validate_awareness(awareness: str) -> str:
        if awareness not in KNOWLEDGE_LEVELS:
            raise ValueError("지원하지 않는 awareness 값입니다.")
        return awareness

    def _validate_row(self, row: dict) -> dict:
        required = {"id", "content", "keywords", "subjects", "awareness", "timeline", "enabled"}
        if not isinstance(row, dict) or not required <= row.keys():
            raise ValueError("runtime knowledge 파일 형식이 잘못되었습니다.")
        self.validate_id(str(row["id"]))
        self.validate_content(str(row["content"]))
        self.validate_awareness(str(row["awareness"]))
        self.validate_timeline(str(row["timeline"]))
        created_at = row.get("created_at")
        if created_at is not None:
            if not isinstance(created_at, str):
                raise TypeError("runtime knowledge의 created_at 값이 잘못되었습니다.")
            try:
                datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("runtime knowledge의 created_at 값이 잘못되었습니다.") from exc
        for key in ("keywords", "subjects"):
            values = row[key]
            if (not isinstance(values, list) or not values or len(values) > MAX_VALUES
                    or any(not isinstance(value, str) or not value.strip()
                           or len(value) > MAX_VALUE_CHARS for value in values)):
                raise ValueError(f"runtime knowledge의 {key} 형식이 잘못되었습니다.")
        if not isinstance(row["enabled"], bool):
            raise TypeError("runtime knowledge의 enabled 값이 잘못되었습니다.")
        return row

    def _read(self) -> list[dict]:
        if self.path is None or not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"runtime knowledge JSON을 읽을 수 없습니다: {exc}") from exc
        if not isinstance(data, list):
            raise TypeError("runtime knowledge 파일은 JSON 배열이어야 합니다.")
        rows = [self._validate_row(row) for row in data]
        ids = [row["id"] for row in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("runtime knowledge ID가 중복되어 있습니다.")
        return rows

    def _write(self, rows: list[dict]) -> None:
        if self.path is None:
            raise ValueError("runtime knowledge 저장 경로가 설정되지 않았습니다.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(self.path)

    def list(self) -> list[dict]:
        return self._read()

    def get(self, identifier: str) -> dict:
        identifier = self.validate_id(identifier)
        for row in self._read():
            if row["id"] == identifier:
                return row
        raise ValueError("등록되지 않은 ID입니다.")

    def add(self, identifier: str, content: str, keywords: str, subjects: str,
            awareness: str, timeline: str = "") -> None:
        identifier = self.validate_id(identifier)
        content = self.validate_content(content)
        awareness = self.validate_awareness(awareness)
        timeline = self.validate_timeline(timeline)
        rows = self._read()
        if any(row["id"] == identifier for row in rows):
            raise ValueError("이미 존재하는 ID입니다.")
        if len(rows) >= MAX_ITEMS:
            raise ValueError(f"항목은 최대 {MAX_ITEMS}개까지 저장할 수 있습니다.")
        rows.append({
            "id": identifier,
            "content": content,
            "keywords": _split_values(keywords),
            "subjects": _split_values(subjects),
            "awareness": awareness,
            "timeline": timeline,
            "enabled": True,
            "created_at": datetime.now(UTC).isoformat(),
        })
        self._write(rows)

    def edit(self, identifier: str, *, content: str | None = None,
             keywords: str | None = None, subjects: str | None = None,
             awareness: str | None = None, timeline: str | None = None) -> None:
        identifier = self.validate_id(identifier)
        if all(value is None for value in (content, keywords, subjects, awareness, timeline)):
            raise ValueError("수정할 값을 하나 이상 입력해 주세요.")
        rows = self._read()
        for row in rows:
            if row["id"] != identifier:
                continue
            if content is not None:
                row["content"] = self.validate_content(content)
            if keywords is not None:
                row["keywords"] = _split_values(keywords)
            if subjects is not None:
                row["subjects"] = _split_values(subjects)
            if awareness is not None:
                row["awareness"] = self.validate_awareness(awareness)
            if timeline is not None:
                row["timeline"] = self.validate_timeline(timeline)
            self._write(rows)
            return
        raise ValueError("등록되지 않은 ID입니다.")

    def set_enabled(self, identifier: str, enabled: bool) -> None:
        identifier = self.validate_id(identifier)
        rows = self._read()
        for row in rows:
            if row["id"] == identifier:
                row["enabled"] = enabled
                self._write(rows)
                return
        raise ValueError("등록되지 않은 ID입니다.")

    def remove(self, identifier: str) -> None:
        identifier = self.validate_id(identifier)
        rows = self._read()
        filtered = [row for row in rows if row["id"] != identifier]
        if len(filtered) == len(rows):
            raise ValueError("등록되지 않은 ID입니다.")
        self._write(filtered)

    def search(self, query: str, *, limit: int = 2, chars: int = 1800) -> list[dict]:
        if limit <= 0 or chars <= 0:
            return []
        folded = query.casefold()
        terms = _terms(query)
        ranked = []
        for order, row in enumerate(self._read()):
            if not row["enabled"]:
                continue
            score = 0
            for value in row["subjects"]:
                folded_value = value.casefold()
                if folded_value not in _STOPWORDS and folded_value in folded:
                    score += 8 + min(len(folded_value), 8)
            for value in row["keywords"]:
                folded_value = value.casefold()
                if folded_value in folded:
                    score += 5 + min(len(folded_value), 8)
            score += 2 * len(terms & _terms(row["content"]))
            if score:
                ranked.append((score, -order, row))

        result, used = [], 0
        prefix = "runtime_context" if self.kind == "interpretation" else "runtime_lore"
        for _, _, row in sorted(ranked, reverse=True):
            item = {
                "reference": f"{prefix}.{row['id']}",
                "kind": self.kind,
                "content": row["content"],
                "awareness": row["awareness"],
                "time": row["timeline"],
            }
            if self.kind == "interpretation":
                item["certainty"] = "plausible_interpretation_not_established_fact"
            size = len(json.dumps(item, ensure_ascii=False))
            if used + size > chars:
                continue
            result.append(item)
            used += size
            if len(result) >= limit:
                break
        return result
