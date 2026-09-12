import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.usage import UsageLogger


@pytest.mark.asyncio
async def test_usage_success_and_error_do_not_log_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    response = NS(status='completed', usage=NS(
        input_tokens=100, output_tokens=20, total_tokens=120,
        input_tokens_details=NS(cached_tokens=50),
        output_tokens_details=NS(reasoning_tokens=5)))
    client = NS(responses=NS(create=AsyncMock(return_value=response)))
    assert await logger.request(client, 'answer', model='test', input='secret') is response
    client.responses.create.side_effect = ValueError('secret exception')
    with pytest.raises(ValueError):
        await logger.request(client, 'summarize', model='test', input='secret')
    logger.close()
    text = path.read_text()
    assert 'secret' not in text
    first, second = map(json.loads, text.splitlines())
    assert first['total_tokens'] == 120
    assert first['cached_tokens'] == 50
    assert second['error_type'] == 'ValueError'
    assert 'total_tokens' not in second


@pytest.mark.asyncio
async def test_missing_usage_is_unknown(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(return_value=NS(status='incomplete'))))
    await logger.request(client, 'answer', model='test')
    logger.close()
    row = json.loads(path.read_text())
    assert row['input_tokens'] is None
    assert row['status'] == 'incomplete'
