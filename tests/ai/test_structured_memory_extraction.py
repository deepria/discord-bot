import pytest

from rio_bot.ai.structured_memory import parse_items


def test_parse_structured_memory_items():
    assert parse_items('{"items":[{"kind":"preference"}]}') == [{"kind": "preference"}]


@pytest.mark.parametrize("value", ["", "[]", "{\"items\":{}}"])
def test_parse_structured_memory_rejects_invalid_shape(value):
    with pytest.raises((TypeError, ValueError)):
        parse_items(value)
