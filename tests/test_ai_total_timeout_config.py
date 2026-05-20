from pathlib import Path


def test_ai_total_timeout_is_disabled_by_default():
    text = Path("src", "chat", "config", "chat_config.py").read_text(encoding="utf-8")

    assert '"TOTAL_TIMEOUT_SECONDS": _parse_int_env("OPENAI_COMPAT_TOTAL_TIMEOUT_SECONDS", 0)' in text
    assert "设为0则不限制" in text
