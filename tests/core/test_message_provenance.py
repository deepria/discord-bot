from rio_bot.core.message_provenance import split_inline_quotes
from rio_bot.core.recent import RecentMessages
from rio_bot.core.routing import Scope


def test_inline_quotes_are_not_direct_speaker_text():
    direct, quoted = split_inline_quotes(
        "> 다른 사용자의 주장\n> 두 번째 줄\n리오야 이 말이 사실이야?"
    )

    assert direct == "리오야 이 말이 사실이야?"
    assert quoted == "다른 사용자의 주장\n두 번째 줄"


def test_triple_angle_quote_consumes_the_remaining_message():
    direct, quoted = split_inline_quotes("설명할게\n>>> 인용한 원문\n계속된 인용")

    assert direct == "설명할게"
    assert quoted == "인용한 원문\n계속된 인용"


def test_greater_than_without_markdown_spacing_is_preserved():
    direct, quoted = split_inline_quotes("리오야 5>3 이지?")

    assert direct == "리오야 5>3 이지?"
    assert quoted == ""


def test_recent_context_never_attributes_an_inline_quote_to_the_message_author():
    recent = RecentMessages()
    scope = Scope(1, 10, 100)

    recent.add(scope, 1, "사용자", "> 제3자의 비밀\n내 의견은 아니야")

    assert [row["content"] for row in recent.context(scope, 2)] == ["내 의견은 아니야"]
