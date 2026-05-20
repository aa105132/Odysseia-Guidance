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
