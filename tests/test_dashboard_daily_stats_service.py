# -*- coding: utf-8 -*-

import asyncio
import os
import sys
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

_MISSING = object()
_ORIGINAL_MODULES = {}


def _remember_module(module_name: str):
    if module_name not in _ORIGINAL_MODULES:
        _ORIGINAL_MODULES[module_name] = sys.modules.get(module_name, _MISSING)


class ProgrammingError(Exception):
    def __init__(self, statement, params, orig):
        super().__init__(f"{statement} | {orig}")
        self.statement = statement
        self.params = params
        self.orig = orig


class _FakeSelect:
    def __init__(self, target):
        self.target = target

    def filter(self, *_args, **_kwargs):
        return self


_remember_module("sqlalchemy.exc")
_remember_module("sqlalchemy.ext.asyncio")
_remember_module("sqlalchemy.future")
_remember_module("src.database.models")
sys.modules["sqlalchemy.exc"] = SimpleNamespace(ProgrammingError=ProgrammingError)
sys.modules["sqlalchemy.ext.asyncio"] = SimpleNamespace(AsyncSession=object)
sys.modules["sqlalchemy.future"] = SimpleNamespace(
    select=lambda target: _FakeSelect(target)
)


class _FakeDashboardDailyStats:
    __table__ = object()
    date = object()


sys.modules["src.database.models"] = SimpleNamespace(
    Base=SimpleNamespace(metadata=SimpleNamespace(create_all=lambda *args, **kwargs: None)),
    DashboardDailyStats=_FakeDashboardDailyStats,
)

from src.database.services.dashboard_daily_stats_service import DashboardDailyStatsService


for module_name, original_module in _ORIGINAL_MODULES.items():
    if original_module is _MISSING:
        sys.modules.pop(module_name, None)
    else:
        sys.modules[module_name] = original_module


def test_get_daily_stats_auto_creates_table_when_missing(monkeypatch):
    record = object()
    fake_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: record)
    )
    missing_table_error = ProgrammingError(
        "SELECT * FROM dashboard_daily_stats",
        {},
        Exception('relation "dashboard_daily_stats" does not exist'),
    )
    session = SimpleNamespace(
        execute=AsyncMock(side_effect=[missing_table_error, fake_result]),
        rollback=AsyncMock(),
        bind=object(),
    )
    ensure_table_mock = AsyncMock()
    monkeypatch.setattr(
        DashboardDailyStatsService,
        "_ensure_dashboard_daily_stats_table",
        ensure_table_mock,
    )
    DashboardDailyStatsService._table_ready_checked = False
    DashboardDailyStatsService._table_ready_lock = None

    result = asyncio.run(
        DashboardDailyStatsService.get_daily_stats(session, date(2026, 4, 8))
    )

    assert result is record
    session.rollback.assert_awaited_once()
    assert ensure_table_mock.await_count == 2
    assert session.execute.await_count == 2


def test_get_daily_stats_proactively_ensures_table_before_query(monkeypatch):
    record = object()
    fake_result = SimpleNamespace(
        scalars=lambda: SimpleNamespace(first=lambda: record)
    )
    session = SimpleNamespace(
        execute=AsyncMock(return_value=fake_result),
        rollback=AsyncMock(),
        bind=object(),
    )
    ensure_table_mock = AsyncMock()
    monkeypatch.setattr(
        DashboardDailyStatsService,
        "_ensure_dashboard_daily_stats_table",
        ensure_table_mock,
    )
    DashboardDailyStatsService._table_ready_checked = False
    DashboardDailyStatsService._table_ready_lock = None

    result = asyncio.run(
        DashboardDailyStatsService.get_daily_stats(session, date(2026, 4, 13))
    )

    assert result is record
    ensure_table_mock.assert_awaited_once_with(session)
    session.execute.assert_awaited_once()
