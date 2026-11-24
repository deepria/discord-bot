import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rio_bot.ai.auto_reply import clearly_addressed_to_other


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ('{"decision":"other","evidence":"민수야"}', True),
        ('{"decision":"other","evidence":"상상한 구절"}', False),
        ('{"decision":"reply","evidence":""}', False),
        ("[]", False),
        ("incomplete JSON", False),
        (TimeoutError(), False),
    ],
)
async def test_auto_reply_requires_a_grounded_other_addressee_decision(response, expected):
    request = AsyncMock()
    if isinstance(response, Exception):
        request.side_effect = response
    else:
        request.return_value = SimpleNamespace(status="completed", output_text=response)
    usage = SimpleNamespace(request=request)
    current = {"speaker_id": "100", "content": "민수야 다섯 시에 보자"}
    recent = [
        {
            "author_user_id": "200", "name": "민수", "role": "user",
            "content": f"{i}\n> 역할극 나레이션",
        }
        for i in range(8)
    ]

    result = await clearly_addressed_to_other(
        usage, object(), "fast", current=current, recent=recent,
    )

    assert result is expected
    kwargs = request.call_args.kwargs
    assert kwargs["store"] is False
    sent = json.loads(kwargs["input"])
    assert [row["content"] for row in sent["recent"]] == ["2", "3", "4", "5", "6", "7"]
