import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.knowledge_commands import ContextCommands, LoreCommands
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
        )

    def tearDown(self):
        self.directory.cleanup()

    async def test_commands_use_bot_admin_allowlist(self):
        for group in (LoreCommands(self.client), ContextCommands(self.client)):
            denied = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
            self.assertFalse(await group.interaction_check(denied))
            allowed = NS(user=NS(id=101), response=NS(send_message=AsyncMock()))
            self.assertTrue(await group.interaction_check(allowed))
            self.assertTrue(group.allowed_contexts.guild)
            self.assertTrue(group.allowed_contexts.dm_channel)
            self.assertTrue(group.allowed_installs.guild)

    def test_command_sets(self):
        expected = {"add", "list", "show", "edit", "enable", "disable", "remove"}
        self.assertEqual({command.name for command in LoreCommands(self.client).commands}, expected)
        self.assertEqual({command.name for command in ContextCommands(self.client).commands}, expected)


if __name__ == "__main__":
    unittest.main()
