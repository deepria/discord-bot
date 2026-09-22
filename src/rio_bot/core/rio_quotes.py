"""Validate reviewed Blue Archive Rio quote metadata without storing a quote corpus in code."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

GAME = "Blue Archive"
LOCALE = "ko-KR"
SPEAKER_NAME = "리오"
RIGHTS_STATUSES = frozenset({"approved", "needs_review", "excluded"})
REVIEW_STATUSES = frozenset({"collected", "first_reviewed", "approved", "excluded"})
SCENE_STATUSES = frozenset({"collected", "excluded"})


class QuoteDatasetError(ValueError):
    """Raised when a quote record or coverage manifest violates the data contract."""


def _text(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise QuoteDatasetError(f"{field} is required")
    return result


def _optional_text(value: object) -> str | None:
    result = str(value or "").strip()
    return result or None


@dataclass(frozen=True)
class RioQuoteRecord:
    quote_id: str
    game_version: str
    story_id: str
    episode_id: str
    scene_id: str
    line_order: int
    speaker_id: str
    text_ko: str
    content_type: str
    scene: str
    emotion: str
    source_ref: str
    source_hash: str
    rights_status: str
    review_status: str
    addressee: str | None = None
    superseded_by: str | None = None
    notes: str | None = None

    @property
    def location(self) -> tuple[str, str, str, str, int]:
        return (self.game_version, self.story_id, self.episode_id, self.scene_id, self.line_order)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> RioQuoteRecord:
        if row.get("game") != GAME:
            raise QuoteDatasetError(f"game must be {GAME!r}")
        if row.get("locale") != LOCALE:
            raise QuoteDatasetError(f"locale must be {LOCALE!r}")
        if row.get("speaker_name_ko") != SPEAKER_NAME:
            raise QuoteDatasetError(f"speaker_name_ko must be {SPEAKER_NAME!r}")
        try:
            line_order = int(row.get("line_order"))
        except (TypeError, ValueError) as exc:
            raise QuoteDatasetError("line_order must be a positive integer") from exc
        if line_order <= 0:
            raise QuoteDatasetError("line_order must be a positive integer")
        rights_status = _text(row.get("rights_status"), "rights_status")
        review_status = _text(row.get("review_status"), "review_status")
        if rights_status not in RIGHTS_STATUSES:
            raise QuoteDatasetError("invalid rights_status")
        if review_status not in REVIEW_STATUSES:
            raise QuoteDatasetError("invalid review_status")
        source_ref = _text(row.get("source_ref"), "source_ref")
        source_hash = _text(row.get("source_hash"), "source_hash")
        if review_status == "approved" and rights_status != "approved":
            raise QuoteDatasetError("approved quotes require approved rights")
        return cls(
            quote_id=_text(row.get("quote_id"), "quote_id"),
            game_version=_text(row.get("game_version"), "game_version"),
            story_id=_text(row.get("story_id"), "story_id"),
            episode_id=_text(row.get("episode_id"), "episode_id"),
            scene_id=_text(row.get("scene_id"), "scene_id"),
            line_order=line_order,
            speaker_id=_text(row.get("speaker_id"), "speaker_id"),
            text_ko=_text(row.get("text_ko"), "text_ko"),
            content_type=_text(row.get("content_type"), "content_type"),
            scene=_text(row.get("scene"), "scene"),
            emotion=_text(row.get("emotion"), "emotion"),
            source_ref=source_ref,
            source_hash=source_hash,
            rights_status=rights_status,
            review_status=review_status,
            addressee=_optional_text(row.get("addressee")),
            superseded_by=_optional_text(row.get("superseded_by")),
            notes=_optional_text(row.get("notes")),
        )


@dataclass(frozen=True)
class CoverageScene:
    game_version: str
    story_id: str
    episode_id: str
    scene_id: str
    status: str
    expected_rio_lines: int
    exclusion_reason: str | None = None

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.game_version, self.story_id, self.episode_id, self.scene_id)

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> CoverageScene:
        status = _text(row.get("status"), "status")
        if status not in SCENE_STATUSES:
            raise QuoteDatasetError("invalid coverage scene status")
        try:
            expected_lines = int(row.get("expected_rio_lines"))
        except (TypeError, ValueError) as exc:
            raise QuoteDatasetError("expected_rio_lines must be a non-negative integer") from exc
        if expected_lines < 0:
            raise QuoteDatasetError("expected_rio_lines must be a non-negative integer")
        reason = _optional_text(row.get("exclusion_reason"))
        if status == "excluded" and reason is None:
            raise QuoteDatasetError("excluded scenes require exclusion_reason")
        if status == "collected" and reason is not None:
            raise QuoteDatasetError("collected scenes must not have exclusion_reason")
        return cls(
            game_version=_text(row.get("game_version"), "game_version"),
            story_id=_text(row.get("story_id"), "story_id"),
            episode_id=_text(row.get("episode_id"), "episode_id"),
            scene_id=_text(row.get("scene_id"), "scene_id"),
            status=status,
            expected_rio_lines=expected_lines,
            exclusion_reason=reason,
        )


def validate_quote_records(rows: Iterable[Mapping[str, Any]]) -> list[RioQuoteRecord]:
    records = [RioQuoteRecord.from_mapping(row) for row in rows]
    quote_ids = [record.quote_id for record in records]
    locations = [record.location for record in records]
    if len(quote_ids) != len(set(quote_ids)):
        raise QuoteDatasetError("duplicate quote_id")
    if len(locations) != len(set(locations)):
        raise QuoteDatasetError("duplicate quote scene location")
    return records


def coverage_complete(
    records: Iterable[RioQuoteRecord], scenes: Iterable[CoverageScene], game_version: str
) -> bool:
    """Return true only when every manifest scene is accounted for at this exact version."""
    version_scenes = [scene for scene in scenes if scene.game_version == game_version]
    if not version_scenes or len({scene.key for scene in version_scenes}) != len(version_scenes):
        return False
    collected = [scene for scene in version_scenes if scene.status == "collected"]
    manifest_keys = {scene.key for scene in version_scenes}
    record_keys = {
        (record.game_version, record.story_id, record.episode_id, record.scene_id)
        for record in records
        if record.game_version == game_version
    }
    if not record_keys <= manifest_keys:
        return False
    counts = Counter(
        (record.game_version, record.story_id, record.episode_id, record.scene_id)
        for record in records
        if record.game_version == game_version and record.review_status == "approved"
    )
    return all(counts[scene.key] == scene.expected_rio_lines for scene in collected)


def approved_quotes(records: Iterable[RioQuoteRecord]) -> list[RioQuoteRecord]:
    """Return the only records eligible for a future RAG index."""
    return [
        record for record in records
        if record.rights_status == "approved" and record.review_status == "approved"
    ]


__all__ = [
    "GAME",
    "LOCALE",
    "CoverageScene",
    "QuoteDatasetError",
    "RioQuoteRecord",
    "approved_quotes",
    "coverage_complete",
    "validate_quote_records",
]
