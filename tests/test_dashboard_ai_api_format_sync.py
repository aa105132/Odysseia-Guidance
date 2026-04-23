from pathlib import Path


def test_dashboard_sync_forms_restores_ai_api_format():
    text = Path("src/dashboard/static/index.html").read_text(encoding="utf-8")

    assert "this.aiForm.api_format = this.config.ai.api_format || 'gemini';" in text
