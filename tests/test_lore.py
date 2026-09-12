import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

from hina_bot import lore_pipeline
from hina_bot.lore import LoreIndex, LoreValidationError, validate_record
from hina_bot.lore_pipeline import _source_rows


def record(identifier="canon.test", lane="canon", **overrides):
    value = {
        "id": identifier, "lane": lane, "summary": "마코토는 만마전 의장이다.",
        "keywords": ["만마전", "의장"], "subjects": ["마코토"],
        "knowledge": "public_knowledge", "confidence": "verified", "status": "accepted",
        "kr_release": "confirmed" if lane == "canon" else "not_applicable",
        "timeline": "한국 서버 최신 검증 시점",
        "source": {"type": "official_game", "title": "스토리", "locator": "1장"},
    }
    if lane == "community_meme":
        value["reaction"] = "짧게 반응하고 본론을 돕는다."
    value.update(overrides)
    return value


class LoreValidationTests(unittest.TestCase):
    def test_runtime_rejects_candidate_and_meme_without_reaction(self):
        with self.assertRaises(LoreValidationError):
            validate_record(record(confidence="candidate"), accepted=True)
        with self.assertRaises(LoreValidationError):
            validate_record(record("meme.test", "community_meme", reaction=""), accepted=True)
        with self.assertRaises(LoreValidationError):
            validate_record(record(kr_release="pending"), accepted=True)

    def test_load_rejects_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lore.jsonl"
            row = json.dumps(record(), ensure_ascii=False)
            path.write_text(row + "\n" + row + "\n", encoding="utf-8")
            with self.assertRaises(LoreValidationError):
                LoreIndex.load(str(path))

    def test_long_sources_are_split_without_losing_text(self):
        text = "가" * 30_000 + "\n\n" + "나" * 30_000
        rows = _source_rows(text, lane="canon", title="t", url="u",
                            source_type="official", locator="l")
        self.assertGreater(len(rows), 2)
        self.assertEqual("".join(row["text"] for row in rows), text.replace("\n\n", ""))
        self.assertTrue(all(len(row["text"]) <= 24_000 for row in rows))


class LoreSearchTests(unittest.TestCase):
    def setUp(self):
        self.index = LoreIndex([
            record(),
            record("meme.head", "community_meme", summary="머리 크기는 커뮤니티 농담이다.",
                   keywords=["머리 크기", "머리 부피"], subjects=["히나"], knowledge="unknown"),
            record("canon.ako", summary="아코는 선도부 행정관이다.", keywords=["행정관", "보좌"],
                   subjects=["아코"]),
        ])

    def test_selects_relevant_canon(self):
        result = self.index.search("마코토는 만마전에서 무슨 일을 해?")
        self.assertEqual(result[0]["reference"], "canon.test")
        self.assertEqual(result[0]["kind"], "world_fact")

    def test_community_lane_is_labeled_and_can_be_disabled(self):
        result = self.index.search("히나 머리 부피를 구하자")
        self.assertEqual(result[0]["kind"], "optional_reaction")
        self.assertNotRegex(str(result[0]), "공식|커뮤니티|밈|meme")
        self.assertNotIn("optional_reaction", str(self.index.search(
            "히나 머리 부피를 구하자", include_community=False)))

    def test_generic_hina_does_not_retrieve_every_meme(self):
        self.assertEqual(self.index.search("히나야 안녕"), [])

    def test_budget_and_limit_are_enforced(self):
        self.assertEqual(len(self.index.search("마코토 아코", limit=1)), 1)
        self.assertEqual(self.index.search("마코토", chars=1), [])

    def test_packaged_corpus_contains_reviewed_profile_batch(self):
        packaged = LoreIndex.load()
        self.assertEqual(len(packaged.records), 22)
        self.assertEqual(
            sum(row["lane"] == "canon" for row in packaged.records), 18)
        self.assertEqual(
            sum(row["lane"] == "community_meme" for row in packaged.records), 4)
        result = packaged.search("세나는 응급의학부에서 무슨 일을 해?")
        self.assertEqual(result[0]["reference"], "profile.sena.role")
        self.assertEqual(result[0]["awareness"], "public_knowledge")


class LorePipelineTests(unittest.TestCase):
    def test_extract_stays_candidate_and_canon_approval_needs_kr_confirmation(self):
        parsed = {"candidates": [{
            "slug": "hina.test-fact", "summary": "히나는 시험 설정을 알고 있다.",
            "keywords": ["시험 설정"], "subjects": ["히나"], "knowledge": "self",
            "reaction": "", "evidence": "장면 일부", "uncertainty": "",
            "timeline": "테스트 장면", "kr_release_evidence": "한국 공지",
        }]}
        response = NS(status="completed", output_text=json.dumps(parsed, ensure_ascii=False))
        fake_responses = NS(create=lambda **kwargs: response)
        fake_client = NS(responses=fake_responses)
        raw = [{"source_id": "source-001", "lane": "canon", "title": "테스트",
                "url": "https://example.com", "source_type": "official_game",
                "locator": "1화", "text": "테스트 원문"}]
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            raw_path, queue_path, runtime_path = (base / "raw.jsonl", base / "queue.jsonl",
                                                   base / "runtime.jsonl")
            lore_pipeline.write_jsonl(raw_path, raw)
            with (patch.object(lore_pipeline, "RAW_PATH", raw_path),
                  patch.object(lore_pipeline, "QUEUE_PATH", queue_path),
                  patch.object(lore_pipeline, "RUNTIME_PATH", runtime_path),
                  patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}),
                  patch("openai.OpenAI", return_value=fake_client)):
                lore_pipeline.extract(NS(all=True, source_id=None, model="test-model"))
                candidate = lore_pipeline.read_jsonl(queue_path)[0]
                self.assertEqual(candidate["status"], "candidate")
                self.assertEqual(candidate["kr_release"], "pending")
                with self.assertRaises(SystemExit):
                    lore_pipeline.decide(NS(id=candidate["id"], confidence="verified",
                                            confirm_kr_release=False), "accepted")
                lore_pipeline.decide(NS(id=candidate["id"], confidence="verified",
                                        confirm_kr_release=True), "accepted")
                accepted = lore_pipeline.read_jsonl(runtime_path)[0]
                self.assertEqual(accepted["kr_release"], "confirmed")
                validate_record(accepted, accepted=True)


if __name__ == "__main__":
    unittest.main()
