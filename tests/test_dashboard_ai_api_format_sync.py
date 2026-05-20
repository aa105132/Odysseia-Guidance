from pathlib import Path


def test_dashboard_sync_forms_restores_ai_api_format():
    text = Path("src", "dashboard", "static", "index.html").read_text(encoding="utf-8")

    assert "this.aiForm.api_format = this.config.ai.api_format || 'gemini';" in text


def test_dashboard_all_config_returns_persisted_ai_api_format():
    text = Path("src", "dashboard", "api.py").read_text(encoding="utf-8")
    start = text.index('@app.get("/api/config/all")')
    end = text.index('@app.get("/api/config/ai")')
    block = text[start:end]

    assert 'db_ai_api_format = await chat_db_manager.get_global_setting("ai_api_format")' in block
    assert '"api_format": ai_api_format' in block
