# -*- coding: utf-8 -*-

from pathlib import Path


def test_global_prompt_requires_self_contained_character_appearance_for_batch_images():
    prompt_source = Path("src/chat/config/prompts.py").read_text(encoding="utf-8")

    assert "同一人物/角色批量生图时，每一条 prompt 都必须自包含完整外貌设定" in prompt_source
    assert "禁止" in prompt_source
    assert "后几条只写" in prompt_source
    assert "通用外貌锚点包括但不限于" in prompt_source
    assert "不允许只靠名字代替" in prompt_source
    assert "月月只是同一人物规则的特例" in prompt_source
    assert "每一条 prompt 还必须重复月月固定外貌锚点" in prompt_source
    assert "生成 `prompts=[...]` 前必须逐条自检" in prompt_source
    assert "edit_images_batch(edit_prompts=[...])" in prompt_source
    assert "success_message` 只写一次" in prompt_source
