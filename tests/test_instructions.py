import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

from hina_bot.instruction_commands import InstructionCommands
from hina_bot.instructions import InstructionRegistry


class InstructionRegistryTests(unittest.TestCase):
    def test_crud_and_active_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "instructions.json"
            registry = InstructionRegistry(str(path))

            self.assertEqual(registry.list(), [])
            self.assertEqual(registry.active_text(), "")

            registry.add("meta_guard", "메타 질문에도 세계 안의 히나로 답하세요.")
            registry.add("restraint", "말줄임표를 반복하지 마세요.")
            rows = registry.list()
            self.assertEqual([row["id"] for row in rows], ["meta_guard", "restraint"])
            self.assertTrue(all(row["enabled"] for row in rows))
            self.assertTrue(all(row.get("created_at") for row in rows))
            self.assertIn("meta_guard", registry.active_text())
            self.assertIn("restraint", registry.active_text())

            registry.set_enabled("meta_guard", False)
            active = registry.active_text()
            self.assertNotIn("meta_guard", active)
            self.assertIn("restraint", active)

            registry.edit("restraint", "한숨과 말줄임표를 습관적으로 반복하지 마세요.")
            self.assertIn("한숨과 말줄임표", registry.active_text())

            registry.remove("meta_guard")
            self.assertEqual([row["id"] for row in registry.list()], ["restraint"])

    def test_validation_and_disabled_registry(self):
        registry = InstructionRegistry("")
        self.assertEqual(registry.list(), [])
        self.assertEqual(registry.active_text(), "")
        with self.assertRaises(ValueError):
            registry.add("valid-id", "저장할 수 없어야 합니다.")

        with tempfile.TemporaryDirectory() as directory:
            registry = InstructionRegistry(str(Path(directory) / "instructions.json"))
            for identifier in ("a", "한글", "bad id", "UPPER CASE"):
                with self.assertRaises(ValueError):
                    registry.add(identifier, "내용")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "가" * 1201)
            registry.add("valid-id", "내용")
            with self.assertRaises(ValueError):
                registry.add("valid-id", "중복")
            with self.assertRaises(ValueError):
                registry.remove("missing-id")


class InstructionCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_access_control_uses_bot_admin_allowlist(self):
        client = NS(settings=NS(instruction_path=""), emoji_admin_ids={100, 101})
        group = InstructionCommands(client)
        interaction = NS(user=NS(id=200), response=NS(send_message=AsyncMock()))
        self.assertFalse(await group.interaction_check(interaction))
        for admin_id in (100, 101):
            interaction.user.id = admin_id
            self.assertTrue(await group.interaction_check(interaction))

    def test_available_in_guilds_and_private_contexts(self):
        client = NS(settings=NS(instruction_path=""), emoji_admin_ids={100})
        group = InstructionCommands(client)
        self.assertTrue(group.allowed_contexts.guild)
        self.assertTrue(group.allowed_contexts.dm_channel)
        self.assertTrue(group.allowed_contexts.private_channel)
        self.assertTrue(group.allowed_installs.guild)
        self.assertTrue(group.allowed_installs.user)


if __name__ == "__main__":
    unittest.main()
