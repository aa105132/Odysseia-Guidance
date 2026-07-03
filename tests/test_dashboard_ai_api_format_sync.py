from pathlib import Path


def test_dashboard_ai_view_exposes_api_format_options():
    text = Path("dashboard-ui", "src", "views", "AIView.vue").read_text(encoding="utf-8")

    assert "form.api_format ?? 'gemini'" in text
    assert "{ value: 'gemini', label: 'Gemini' }" in text
    assert "{ value: 'interactions', label: 'Interactions' }" in text
    assert "{ value: 'openai', label: 'OpenAI' }" in text


def test_dashboard_all_config_returns_persisted_ai_api_format():
    text = Path("src", "dashboard", "api.py").read_text(encoding="utf-8")
    start = text.index('@app.get("/api/config/all")')
    end = text.index('@app.get("/api/config/ai")')
    block = text[start:end]

    assert 'db_ai_api_format = await chat_db_manager.get_global_setting("ai_api_format")' in block
    assert '"api_format": ai_api_format' in block


def test_dashboard_ai_config_accepts_interactions_api_format():
    text = Path("src", "dashboard", "api.py").read_text(encoding="utf-8")
    start = text.index('if config.api_format is not None:')
    block = text[start : start + 400]

    assert '"interactions"' in block
    assert "API 格式必须是 'gemini'、'openai' 或 'interactions'" in block
