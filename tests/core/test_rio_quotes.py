import pytest

from rio_bot.core.rio_quotes import (
    CoverageScene,
    QuoteDatasetError,
    approved_quotes,
    coverage_complete,
    validate_quote_records,
)


def quote(**overrides):
    row = {
        "quote_id": "rio-1",
        "game": "Blue Archive",
        "locale": "ko-KR",
        "game_version": "2026.09",
        "story_id": "story-1",
        "episode_id": "episode-1",
        "scene_id": "scene-1",
        "line_order": 1,
        "speaker_id": "rio",
        "speaker_name_ko": "리오",
        "text_ko": "합성 테스트 대사",
        "content_type": "main_story",
        "scene": "회의",
        "emotion": "차분함",
        "source_ref": "client:story-1/episode-1/scene-1",
        "source_hash": "test-hash",
        "rights_status": "approved",
        "review_status": "approved",
    }
    row.update(overrides)
    return row


def scene(**overrides):
    row = {
        "game_version": "2026.09",
        "story_id": "story-1",
        "episode_id": "episode-1",
        "scene_id": "scene-1",
        "status": "collected",
        "expected_rio_lines": 1,
    }
    row.update(overrides)
    return row


def test_approved_records_are_rag_eligible_and_manifest_can_be_complete():
    records = validate_quote_records([quote()])
    manifest = [CoverageScene.from_mapping(scene())]

    assert approved_quotes(records) == records
    assert coverage_complete(records, manifest, "2026.09")


def test_approved_record_requires_approved_rights_and_source_metadata():
    with pytest.raises(QuoteDatasetError, match="approved rights"):
        validate_quote_records([quote(rights_status="needs_review")])
    with pytest.raises(QuoteDatasetError, match="source_hash"):
        validate_quote_records([quote(source_hash="")])


def test_duplicate_scene_line_is_rejected_and_incomplete_manifest_is_false():
    with pytest.raises(QuoteDatasetError, match="duplicate quote scene location"):
        validate_quote_records([quote(), quote(quote_id="rio-2")])

    records = validate_quote_records([quote()])
    manifest = [CoverageScene.from_mapping(scene(expected_rio_lines=2))]
    assert not coverage_complete(records, manifest, "2026.09")


def test_excluded_scene_requires_a_reason():
    with pytest.raises(QuoteDatasetError, match="exclusion_reason"):
        CoverageScene.from_mapping(scene(status="excluded"))
