import pytest

from rio_bot.ai.structured_memory import RESPONSE_FORMAT, parse_items


def test_structured_memory_response_format_requires_the_complete_item_contract():
    schema = RESPONSE_FORMAT["schema"]
    item = schema["properties"]["items"]["items"]
    assert RESPONSE_FORMAT["type"] == "json_schema"
    assert RESPONSE_FORMAT["strict"] is True
    assert schema["required"] == ["items"]
    assert item["required"] == ["kind", "content", "disclosure", "source_message_ids", "confidence"]
    assert item["properties"]["kind"]["enum"] == [
        "fact", "event", "preference", "relationship", "boundary", "task",
    ]


def test_parse_structured_memory_items():
    assert parse_items('{"items":[{"kind":"preference"}]}') == [{"kind": "preference"}]


@pytest.mark.parametrize("value", ["", "[]", "{\"items\":{}}"])
def test_parse_structured_memory_rejects_invalid_shape(value):
    with pytest.raises((TypeError, ValueError)):
        parse_items(value)
