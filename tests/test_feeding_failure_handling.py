from pathlib import Path


def test_feeding_parse_failure_returns_before_rewards():
    text = Path("src", "chat", "features", "affection", "cogs", "feeding_cog.py").read_text(encoding="utf-8")
    start = text.index("if not match:")
    end = text.index("else:", start)
    block = text[start:end]

    assert "edit_original_response" in block
    assert "return" in block
    assert "affection_gain = 1" not in block
    assert "coin_gain = 10" not in block


def test_feeding_prompt_requires_specific_food_details():
    text = Path("src", "chat", "config", "chat_config.py").read_text(encoding="utf-8")

    assert "不要凭空说盘子空空如也" in text
    assert "具体食物名称和视觉细节" in text
    assert "不能只写“食物/料理/投喂”" in text

def test_feeding_passes_discord_attachment_url_to_vision_model():
    text = Path("src", "chat", "features", "affection", "cogs", "feeding_cog.py").read_text(encoding="utf-8")

    assert "image_url=image.url" in text

