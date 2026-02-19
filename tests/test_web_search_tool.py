import asyncio
from unittest.mock import AsyncMock

from src.chat.features.tools.functions import web_search as web_search_tool


def test_parse_query_flags_deep_and_batch():
    query, use_tavily, batch_queries = web_search_tool._parse_query_flags(
        '[DEEP][BATCH]\nquery-1\nquery-2\nquery-3'
    )

    assert query == 'query-1'
    assert use_tavily is True
    assert batch_queries == 'query-2\nquery-3'


def test_web_search_default_only_grok(monkeypatch):
    monkeypatch.setattr(
        web_search_tool._config,
        'is_grok_configured',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        web_search_tool._config,
        'is_tavily_configured',
        AsyncMock(return_value=True),
    )

    grok_mock = AsyncMock(return_value={'content': 'ok', 'sources': []})
    tavily_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(web_search_tool, '_grok_search', grok_mock)
    monkeypatch.setattr(web_search_tool, '_tavily_search', tavily_mock)

    result = asyncio.run(web_search_tool.web_search('what is python'))

    assert '网络搜索结果' in result
    assert grok_mock.await_count == 1
    assert tavily_mock.await_count == 0


def test_web_search_deep_enables_tavily(monkeypatch):
    monkeypatch.setattr(
        web_search_tool._config,
        'is_grok_configured',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        web_search_tool._config,
        'is_tavily_configured',
        AsyncMock(return_value=True),
    )

    grok_mock = AsyncMock(return_value={'content': 'ok', 'sources': []})
    tavily_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(web_search_tool, '_grok_search', grok_mock)
    monkeypatch.setattr(web_search_tool, '_tavily_search', tavily_mock)

    asyncio.run(web_search_tool.web_search('[DEEP] latest ai news'))

    assert grok_mock.await_count == 1
    assert tavily_mock.await_count == 1


def test_web_search_batch_runs_multiple_queries(monkeypatch):
    monkeypatch.setattr(
        web_search_tool._config,
        'is_grok_configured',
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        web_search_tool._config,
        'is_tavily_configured',
        AsyncMock(return_value=False),
    )

    grok_mock = AsyncMock(return_value={'content': 'ok', 'sources': []})
    monkeypatch.setattr(web_search_tool, '_grok_search', grok_mock)

    result = asyncio.run(web_search_tool.web_search('[BATCH]\nq1\nq2\nq3'))

    assert '网络批量搜索结果' in result
    assert grok_mock.await_count == 3
