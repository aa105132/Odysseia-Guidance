import asyncio
import importlib
import os
import sys
import types


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


class FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.closed = False
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False


class FakeConnection:
    def __init__(self, row=None):
        self.row = row
        self.closed = False
        self.commit_called = False
        self.rollback_called = False
        self.cursors = []

    def cursor(self, cursor_factory=None):
        cursor = FakeCursor(self.row)
        self.cursors.append(cursor)
        return cursor

    def commit(self):
        self.commit_called = True

    def rollback(self):
        self.rollback_called = True

    def close(self):
        self.closed = True


def _install_psycopg2_stub(monkeypatch):
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2_extras = types.ModuleType("psycopg2.extras")

    class FakePsycopg2Error(Exception):
        pass

    fake_psycopg2.Error = FakePsycopg2Error
    fake_psycopg2.connect = lambda **kwargs: FakeConnection()
    fake_psycopg2.extras = fake_psycopg2_extras
    fake_psycopg2_extras.DictCursor = object
    fake_psycopg2_extras.RealDictCursor = object

    monkeypatch.setitem(sys.modules, "psycopg2", fake_psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", fake_psycopg2_extras)


def _load_incremental_rag_module(monkeypatch):
    _install_psycopg2_stub(monkeypatch)
    module_name = "src.chat.features.world_book.services.incremental_rag_service"
    sys.modules.pop(module_name, None)
    return importlib.import_module(module_name)


def _load_world_book_module(monkeypatch):
    _install_psycopg2_stub(monkeypatch)

    fake_gemini_module = types.ModuleType("src.chat.services.gemini_service")

    class FakeGeminiService:
        def is_available(self):
            return True

    fake_gemini_module.GeminiService = FakeGeminiService
    fake_gemini_module.gemini_service = FakeGeminiService()
    monkeypatch.setitem(
        sys.modules, "src.chat.services.gemini_service", fake_gemini_module
    )

    incremental_module_name = (
        "src.chat.features.world_book.services.incremental_rag_service"
    )
    world_book_module_name = "src.chat.features.world_book.services.world_book_service"

    sys.modules.pop(incremental_module_name, None)
    sys.modules.pop(world_book_module_name, None)

    return importlib.import_module(world_book_module_name)


def test_get_parade_connection_returns_new_connection_each_call(monkeypatch):
    module = _load_incremental_rag_module(monkeypatch)
    created_connections = []

    def fake_connect(**kwargs):
        conn = FakeConnection()
        created_connections.append(conn)
        return conn

    monkeypatch.setattr(module.psycopg2, "connect", fake_connect)

    service = module.IncrementalRAGService()
    first_conn = service._get_parade_connection()
    second_conn = service._get_parade_connection()

    assert first_conn is not second_conn
    assert len(created_connections) == 2


def test_get_community_member_data_closes_connection_after_read(monkeypatch):
    module = _load_incremental_rag_module(monkeypatch)
    service = module.IncrementalRAGService()
    fake_conn = FakeConnection(
        row={
            "id": "member-1",
            "external_id": "ext-1",
            "discord_id": "123",
            "title": "测试成员",
            "full_text": "{}",
            "source_metadata": None,
        }
    )

    monkeypatch.setattr(service, "_get_parade_connection", lambda: fake_conn)

    result = service._get_community_member_data("member-1")

    assert result["id"] == "member-1"
    assert fake_conn.closed is True
    assert fake_conn.cursors[0].closed is True


def test_get_profile_by_discord_id_closes_connection_after_read(monkeypatch):
    module = _load_world_book_module(monkeypatch)
    fake_conn = FakeConnection(
        row={
            "discord_id": "123",
            "title": "测试头衔",
            "personal_summary": "测试简介",
            "source_metadata": {},
        }
    )

    monkeypatch.setattr(
        module.incremental_rag_service, "_get_parade_connection", lambda: fake_conn
    )

    result = asyncio.run(module.world_book_service.get_profile_by_discord_id(123))

    assert result["discord_id"] == "123"
    assert fake_conn.closed is True
    assert fake_conn.cursors[0].closed is True
