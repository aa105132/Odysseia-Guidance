import os
import sys
import types

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# 兼容测试环境：若未安装 psycopg2，注入一个最小桩模块。
if "psycopg2" not in sys.modules:
    fake_psycopg2 = types.ModuleType("psycopg2")
    fake_psycopg2_extras = types.ModuleType("psycopg2.extras")
    fake_psycopg2_extensions = types.ModuleType("psycopg2.extensions")

    class _DummyConnection:
        pass

    fake_psycopg2_extras.DictCursor = object
    fake_psycopg2_extensions.connection = _DummyConnection
    fake_psycopg2.extras = fake_psycopg2_extras
    fake_psycopg2.extensions = fake_psycopg2_extensions
    sys.modules["psycopg2"] = fake_psycopg2
    sys.modules["psycopg2.extras"] = fake_psycopg2_extras
    sys.modules["psycopg2.extensions"] = fake_psycopg2_extensions

from src.dashboard.api import (
    _normalize_thread_ids,
    _parse_thread_ids_text,
    _safe_int,
    ThreadAutoSpeakerConfigUpdate,
)
from src.chat.features.chat_settings.services.chat_settings_service import (
    _parse_id_set_from_text,
)


def test_normalize_thread_ids_deduplicate_and_filter_invalid_values():
    source_ids = ["123", "456", "123", "0", "-1", None, "abc"]
    assert _normalize_thread_ids(source_ids) == [123, 456]


def test_parse_thread_ids_text_supports_multiple_delimiters():
    text = "123, 456\n789 456"
    assert _parse_thread_ids_text(text) == [123, 456, 789]


def test_safe_int_returns_fallback_when_invalid():
    assert _safe_int("42", 1) == 42
    assert _safe_int("oops", 7) == 7
    assert _safe_int(None, 9) == 9


def test_parse_id_set_from_text_handles_mixed_tokens():
    text = "111, abc 222\n333"
    assert _parse_id_set_from_text(text) == {111, 222, 333}
