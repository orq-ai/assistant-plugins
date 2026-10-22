"""Model helpers use Responses and preserve cost accounting and verdict schemas."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from evaluatorq.contracts import Usage

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import _bootstrap  # noqa: F401,E402
from lib import judge, model_backend as mb  # noqa: E402


def backend(response):
    instance = mb.OrqRouterBackend.__new__(mb.OrqRouterBackend)
    instance.model = 'synthetic/model'
    instance.max_tokens = 128
    instance._prices_loaded = True
    instance._prices = (0.1, 0.2)
    instance._client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response)))
    return instance


def test_router_completion_uses_responses_and_prices_input_output_tokens():
    instance = backend(SimpleNamespace(
        id='resp_test', status='completed', output_text='Rewritten {{query}}',
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
    ))
    result = asyncio.run(instance.complete('Keep {{query}}', system='Rewrite'))
    assert result.text == 'Rewritten {{query}}'
    assert result.cost_usd == pytest.approx(0.02)
    instance._client.responses.create.assert_awaited_once_with(
        model='synthetic/model', input='Keep {{query}}', instructions='Rewrite', max_output_tokens=128,
    )


def test_router_preflight_uses_responses():
    instance = backend(SimpleNamespace())
    asyncio.run(instance.preflight())
    instance._client.responses.create.assert_awaited_once_with(
        model='synthetic/model', input='ok', max_output_tokens=1,
    )


def test_router_incomplete_response_is_not_a_recommendation():
    instance = backend(SimpleNamespace(id='resp_test', status='incomplete', output_text='partial'))
    with pytest.raises(mb.BackendUnavailable, match='incomplete'):
        asyncio.run(instance.complete('Rewrite'))


def test_judge_uses_responses_schema_and_preserves_usage(monkeypatch):
    usage = Usage()
    execute = AsyncMock(return_value=(
        SimpleNamespace(status='completed', output_text='{"value":true,"explanation":"ok"}'), usage,
    ))
    monkeypatch.setattr(judge, 'execute_response', execute)
    fn = judge.build_judge_fn(judge.JudgeSpec('Judge {{query}}', {'query': 'Hello'}, None), client=object())
    result = asyncio.run(fn('synthetic/model'))
    assert result.value is True
    assert result.token_usage is usage
    params = execute.call_args.kwargs
    assert params['messages'] == [{'role': 'user', 'content': 'Judge Hello'}]
    assert params['extra_body']['text']['format']['type'] == 'json_schema'
    assert params['extra_body']['text']['format']['schema']['properties']['value']['type'] == 'boolean'
    assert 'response_format' not in params


def test_judge_incomplete_response_is_an_error(monkeypatch):
    monkeypatch.setattr(judge, 'execute_response', AsyncMock(return_value=(
        SimpleNamespace(id='resp_test', status='incomplete', output_text='true'), None,
    )))
    fn = judge.build_judge_fn(judge.JudgeSpec('Judge', {}, None), client=object())
    result = asyncio.run(fn('synthetic/model'))
    assert result.value is None
    assert 'incomplete' in result.error


def test_judge_sends_responses_request_through_sdk(monkeypatch):
    import json
    import httpx
    from openai import AsyncOpenAI
    from evaluatorq.common import llm_call

    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={
            'id': 'resp_synthetic', 'object': 'response', 'created_at': 0,
            'model': 'synthetic/model', 'status': 'completed',
            'output': [{'id': 'msg_test', 'type': 'message', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': '{"value":true,"explanation":"ok"}', 'annotations': []}]}],
        })

    # Isolate catalogue pricing so this only exercises the local SDK transport.
    monkeypatch.setattr(llm_call, 'price_usage', AsyncMock(return_value=None))

    async def run():
        async with AsyncOpenAI(
            api_key='synthetic', base_url='https://local.test/v3/router',
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        ) as client:
            fn = judge.build_judge_fn(judge.JudgeSpec('Judge', {}, None), client=client)
            result = await fn('synthetic/model')
            assert result.value is True, result.error

    asyncio.run(run())
    assert len(requests) == 1
    assert requests[0].url.path == '/v3/router/responses'
    body = json.loads(requests[0].content)
    assert body['input'] == [{'role': 'user', 'content': 'Judge'}]
    assert body['text']['format']['schema']['properties']['value']['type'] == 'boolean'
    assert 'messages' not in body and 'response_format' not in body
