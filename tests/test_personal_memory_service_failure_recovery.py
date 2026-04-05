import asyncio
import sys
import types
from unittest.mock import AsyncMock


class _FakeQuery:
    def where(self, *_args, **_kwargs):
        return self

    def with_for_update(self):
        return self


class _FakeResult:
    def __init__(self, profile):
        self._profile = profile

    def scalars(self):
        return self

    def first(self):
        return self._profile


class _FakeBegin:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, profile):
        self.profile = profile

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return _FakeBegin()

    async def execute(self, _stmt):
        return _FakeResult(self.profile)

    def add(self, _obj):
        return None

    async def flush(self):
        return None


def _fake_async_session_local_factory(profile):
    def _factory():
        return _FakeSession(profile)

    return _factory


fake_sqlalchemy = sys.modules.get("sqlalchemy") or types.ModuleType("sqlalchemy")
fake_sqlalchemy.update = lambda *_args, **_kwargs: _FakeQuery()
sys.modules["sqlalchemy"] = fake_sqlalchemy

fake_sqlalchemy_future = sys.modules.get("sqlalchemy.future") or types.ModuleType(
    "sqlalchemy.future"
)
fake_sqlalchemy_future.select = lambda *_args, **_kwargs: _FakeQuery()
sys.modules["sqlalchemy.future"] = fake_sqlalchemy_future

fake_database_module = types.ModuleType("src.database.database")
fake_database_module.AsyncSessionLocal = None
sys.modules.setdefault("src.database.database", fake_database_module)

fake_models_module = types.ModuleType("src.database.models")


class _FakeCommunityMemberProfile:
    discord_id = "discord_id"
    personal_summary = "personal_summary"


fake_models_module.CommunityMemberProfile = _FakeCommunityMemberProfile
sys.modules.setdefault("src.database.models", fake_models_module)

fake_chat_config_module = types.ModuleType("src.chat.config.chat_config")
fake_chat_config_module.PERSONAL_MEMORY_CONFIG = {"summary_threshold": 1}
sys.modules.setdefault("src.chat.config.chat_config", fake_chat_config_module)

from src.chat.features.personal_memory.services.personal_memory_service import (  # noqa: E402
    PersonalMemoryService,
)
import src.chat.features.personal_memory.services.personal_memory_service as memory_module  # noqa: E402


def test_update_and_conditionally_summarize_memory_restores_when_summary_failed():
    profile = types.SimpleNamespace(personal_message_count=0, history=[])
    memory_module.AsyncSessionLocal = _fake_async_session_local_factory(profile)
    memory_module.chat_config.PERSONAL_MEMORY_CONFIG = {"summary_threshold": 1}

    service = PersonalMemoryService()
    service._summarize_memory = AsyncMock(return_value=False)
    service._restore_history_after_failed_summary = AsyncMock()

    asyncio.run(
        service.update_and_conditionally_summarize_memory(
            user_id=123,
            user_name="测试用户",
            user_content="你好",
            ai_response="你好呀",
        )
    )

    service._summarize_memory.assert_awaited_once()
    service._restore_history_after_failed_summary.assert_awaited_once()

    restored_history = service._restore_history_after_failed_summary.await_args.args[1]
    assert restored_history == [
        {"role": "user", "parts": ["你好"]},
        {"role": "model", "parts": ["你好呀"]},
    ]
