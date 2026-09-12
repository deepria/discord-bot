import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest

from hina_bot.usage import UsageLogger


def response(input_tokens, output_tokens, *, cached=0, reasoning=0):
    return NS(status='completed', usage=NS(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=NS(cached_tokens=cached),
        output_tokens_details=NS(reasoning_tokens=reasoning)))


@pytest.mark.asyncio
async def test_usage_success_and_error_do_not_log_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    first_response = response(100, 20, cached=50, reasoning=5)
    client = NS(responses=NS(create=AsyncMock(return_value=first_response)))
    assert await logger.request(client, 'answer', model='test', input='secret') is first_response
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


@pytest.mark.asyncio
async def test_discord_exchange_aggregates_answer_and_summary_calls(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(side_effect=[
        response(1000, 100, cached=400, reasoning=20),
        response(300, 50, cached=100),
        response(200, 40),
    ])))

    with logger.exchange('guild'):
        await logger.request(client, 'answer', model='chat-model', input='secret user message')
        await logger.request(client, 'summarize', model='memory-model', input='secret memory')
        await logger.request(client, 'summarize_shared', model='memory-model', input='secret shared')
    logger.close()

    detail_rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(detail_rows) == 3

    exchange_path = tmp_path / 'discord-usage.jsonl'
    text = exchange_path.read_text()
    assert 'secret' not in text
    row = json.loads(text)
    assert row['scope'] == 'guild'
    assert row['status'] == 'completed'
    assert row['calls'] == 3
    assert row['failed_calls'] == 0
    assert row['input_tokens'] == 1500
    assert row['output_tokens'] == 190
    assert row['total_tokens'] == 1690
    assert row['cached_tokens'] == 500
    assert row['reasoning_tokens'] == 20
    assert row['usage_complete'] is True
    assert row['models'] == ['chat-model', 'memory-model']
    assert row['operations']['answer']['total_tokens'] == 1100
    assert row['operations']['summarize']['total_tokens'] == 350
    assert row['operations']['summarize_shared']['total_tokens'] == 240


@pytest.mark.asyncio
async def test_discord_exchange_records_failed_api_call_without_content(tmp_path):
    path = tmp_path / 'usage.jsonl'
    logger = UsageLogger(str(path))
    client = NS(responses=NS(create=AsyncMock(side_effect=RuntimeError('secret failure'))))

    with pytest.raises(RuntimeError), logger.exchange('dm'):
        await logger.request(client, 'answer', model='test', input='secret')
    logger.close()

    row = json.loads((tmp_path / 'discord-usage.jsonl').read_text())
    assert row['scope'] == 'dm'
    assert row['status'] == 'error'
    assert row['error_type'] == 'RuntimeError'
    assert row['calls'] == 1
    assert row['failed_calls'] == 1
    assert row['usage_complete'] is False
    assert 'secret' not in json.dumps(row)
