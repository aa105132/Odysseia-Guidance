"""兼容真实网关的单个代码块包装，其他格式错误仍回退。"""

import importlib
import json

import httpx
import pytest

llm = importlib.import_module('src.chat.features.games.blackjack-web.game_llm')


@pytest.mark.asyncio
@pytest.mark.parametrize('content,accepted', [
    ('{"action":"stand"}', True),
    ('```json\n{"action":"stand"}\n```', True),
    ('  ```JSON\r\n{"action":"stand"}\r\n```  ', True),
    ('```\n{"action":"stand"}\n```', True),
    ('建议如下\n```json\n{"action":"stand"}\n```', False),
    ('```json\n{"action":"stand"}\n```\n解释', False),
    ('```json\n{"action":"stand","action":"hit"}\n```', False),
    ('```json\n{"action":"stand","reason":"多余字段"}\n```', False),
    ('```json\n{"action":"stand"}\n```\n```json\n{"action":"hit"}\n```', False),
    ('```json\n[{"action":"stand"}]\n```', False),
])
async def test_fenced_action_still_passes_strict_validation(monkeypatch, content, accepted):
    monkeypatch.setenv('GAME_LLM_ENABLED', 'true')
    monkeypatch.setenv('GAME_LLM_BASE_URL', 'https://model.test/v1')
    monkeypatch.setenv('GAME_LLM_API_KEY', 'synthetic-only')
    def response(request):
        payload = json.loads(request.content)
        assert 'max_tokens' not in payload
        return httpx.Response(200, json={'choices': [{'message': {'content': content}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(response), **kw))
    result = await llm.GameLLMClient().choose_action({'game_type': 'blackjack', 'state': {'legal_actions': ['stand', 'hit']}})
    assert result == ({'action': 'stand'} if accepted else None)
