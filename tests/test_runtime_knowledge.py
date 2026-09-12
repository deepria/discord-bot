import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.knowledge_commands import KnowledgeCommands
from hina_bot.knowledge_ingest import KnowledgeIngestor
from hina_bot.runtime_knowledge import RuntimeKnowledgeRegistry


class RuntimeKnowledgeRegistryTests(unittest.TestCase):
    def test_fact_crud_and_search(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = RuntimeKnowledgeRegistry(
                str(Path(directory) / "lore.json"), kind="world_fact")
            registry.add(
                "hina.quote", "히나는 사건 뒤 선생과 대화했다.",
                "에덴조약,선생,대화", "히나,선생", "self", "에덴조약 이후")

            item = registry.search("에덴조약 뒤 선생과 무슨 대화를 했어?")[0]
            self.assertEqual(item["kind"], "world_fact")
            self.assertEqual(item["awareness"], "self")
            self.assertTrue(item["reference"].startswith("runtime_lore."))

            registry.edit("hina.quote", content="히나는 회복한 선생과 대화했다.")
            self.assertIn("회복한", registry.get("hina.quote")["content"])
            registry.set_enabled("hina.quote", False)
            self.assertEqual(registry.search("에덴조약 선생"), [])
            registry.remove("hina.quote")
            self.assertEqual(registry.list(), [])

    def test_context_is_marked_as_non_fact_interpretation(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = RuntimeKnowledgeRegistry(
                str(Path(directory) / "contexts.json"), kind="interpretation")
            registry.add(
                "hina.hoshino", "히나가 호시노를 비교 대상으로 든 이유에 대한 해석이다.",
                "호시노처럼,호시노,비교", "히나,호시노", "inference", "에덴조약 이후")
            item = registry.search("호시노처럼 될 수 없다는 말이 무슨 뜻이야?")[0]
            self.assertEqual(item["kind"], "interpretation")
            self.assertEqual(item["certainty"], "plausible_interpretation_not_established_fact")
            self.assertTrue(item["reference"].startswith("runtime_context."))

    def test_validation(self):
        registry = RuntimeKnowledgeRegistry("", kind="world_fact")
        with self.assertRaises(ValueError):
            registry.add("x", "내용", "키워드", "대상", "self")
        with tempfile.TemporaryDirectory() as directory:
            registry = RuntimeKnowledgeRegistry(str(Path(directory) / "lore.json"), kind="world_fact")
            with self.assertRaises(ValueError):
                registry.add("valid.id", "내용", "", "히나", "self")
            with self.assertRaises(ValueError):
                registry.add("valid.id", "내용", "키워드", "히나", "bad-awareness")


class KnowledgeIngestorTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_splits_fact_interpretation_and_hold(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = RuntimeKnowledgeRegistry(str(root / "facts.json"), kind="world_fact")
            contexts = RuntimeKnowledgeRegistry(str(root / "contexts.json"), kind="interpretation")
            payload = {
                "items": [
                    {
                        "id": "eden.hina.hoshino-quote",
                        "kind": "world_fact",
                        "content": "히나는 에덴조약 사태 이후 자신이 호시노처럼 될 수 없다고 말했다.",
                        "keywords": ["호시노처럼", "에덴조약"],
                        "subjects": ["히나", "호시노"],
                        "awareness": "self",
                        "timeline": "에덴조약 사태 이후",
                        "decision": "apply",
                        "reason": "입력에 명시된 대사 사실",
                    },
                    {
                        "id": "hina.hoshino-comparison",
                        "kind": "interpretation",
                        "content": "히나는 상실 뒤에도 후배들을 이끄는 호시노를 자신과 대비했을 수 있다.",
                        "keywords": ["호시노", "비교", "유메"],
                        "subjects": ["히나", "호시노", "유메"],
                        "awareness": "inference",
                        "timeline": "에덴조약 사태 이후",
                        "decision": "apply",
                        "reason": "입력 자체가 추측으로 제시한 동기 해석",
                    },
                    {
                        "id": "hina.unknown-detail",
                        "kind": "world_fact",
                        "content": "서로 모순되는 세부 정보다.",
                        "keywords": ["세부 정보"],
                        "subjects": ["히나"],
                        "awareness": "unknown",
                        "timeline": "시점 미지정",
                        "decision": "hold",
                        "reason": "입력 내부에서 충돌함",
                    },
                ]
            }
            llm = NS(
                runtime_lore=facts,
                story_context=contexts,
                lore=NS(records=[]),
                usage=NS(request=AsyncMock(return_value=NS(
                    status="completed", output_text=json.dumps(payload, ensure_ascii=False)))),
                client=object(),
                settings=NS(model="test-model"),
            )
            result = await KnowledgeIngestor(llm).ingest("에덴조약과 호시노에 대한 조사 메모")

            self.assertEqual(len(result["applied"]), 2)
            self.assertEqual(len(result["held"]), 1)
            self.assertEqual(len(facts.list()), 1)
            self.assertEqual(len(contexts.list()), 1)
            self.assertEqual(contexts.list()[0]["awareness"], "inference")

    async def test_ingest_skips_near_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            facts = RuntimeKnowledgeRegistry(str(root / "facts.json"), kind="world_fact")
            facts.add(
                "existing.quote", "히나는 에덴조약 이후 선생과 대화하며 호시노처럼 될 수 없다고 말했다.",
                "호시노처럼,에덴조약", "히나,호시노,선생", "self", "에덴조약 이후")
            payload = {"items": [{
                "id": "new.quote",
                "kind": "world_fact",
                "content": "히나는 에덴조약 이후 선생과 대화하며 호시노처럼 될 수 없다고 말했다.",
                "keywords": ["호시노처럼", "에덴조약"],
                "subjects": ["히나", "호시노", "선생"],
                "awareness": "self",
                "timeline": "에덴조약 이후",
                "decision": "apply",
                "reason": "명시된 대사",
            }]}
            llm = NS(
                runtime_lore=facts,
                story_context=RuntimeKnowledgeRegistry(
                    str(root / "contexts.json"), kind="interpretation"),
                lore=NS(records=[]),
                usage=NS(request=AsyncMock(return_value=NS(
                    status="completed", output_text=json.dumps(payload, ensure_ascii=False)))),
                client=object(),
                settings=NS(model="test-model"),
            )
            result = await KnowledgeIngestor(llm).ingest("중복 조사 메모")
            self.assertEqual(result["applied"], [])
            self.assertEqual(result["skipped"][0]["duplicate_of"], "existing.quote")
            self.assertEqual(len(facts.list()), 1)


class KnowledgeCommandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.client = NS(
            settings=NS(
                runtime_lore_path=str(root / "lore.json"),
                context_path=str(root / "contexts.json"),
            ),
            emoji_admin_ids={100, 101},
            llm=NS(),
        )

    def tearDown(self):
        self.directory.cleanup()

    async def test_commands_use_bot_admin_allowlist(self):
        group = KnowledgeCommands(self.client)
        denied = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
        self.assertFalse(await group.interaction_check(denied))
        allowed = NS(user=NS(id=101), response=NS(send_message=AsyncMock()))
        self.assertTrue(await group.interaction_check(allowed))
        self.assertTrue(group.allowed_contexts.guild)
        self.assertTrue(group.allowed_contexts.dm_channel)
        self.assertTrue(group.allowed_installs.guild)

    def test_command_set(self):
        expected = {"ingest", "list", "show", "enable", "disable", "remove"}
        self.assertEqual({command.name for command in KnowledgeCommands(self.client).commands}, expected)


if __name__ == "__main__":
    unittest.main()
