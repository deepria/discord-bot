import unittest

from rio_bot.output_safety import DISCORD_MENTION, neutralize_mentions


class OutputSafetyTests(unittest.TestCase):
    def test_mass_and_role_mentions_are_neutralized_but_user_mentions_survive(self):
        unsafe = "@everyone @here <@123> <@!456> <@&789>"
        result = neutralize_mentions(unsafe)
        self.assertIsNone(DISCORD_MENTION.search(result))
        self.assertEqual(result, "＠everyone ＠here <@123> <@!456> <＠&789>")

    def test_normal_text_and_custom_emoji_are_unchanged(self):
        text = "안녕 @user <:rio_happy:1234> 이메일 user@example.com"
        self.assertEqual(neutralize_mentions(text), text)

    def test_case_variants_are_neutralized(self):
        self.assertEqual(neutralize_mentions("@Everyone @HERE"), "＠Everyone ＠HERE")
