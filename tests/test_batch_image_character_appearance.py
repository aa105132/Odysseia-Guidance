# -*- coding: utf-8 -*-

from pathlib import Path


def test_generate_images_batch_docstring_requires_each_prompt_to_repeat_appearance():
    source = Path("src/chat/features/tools/functions/generate_image.py").read_text(encoding="utf-8")

    assert "每一条 prompt 都必须完整重复人物外貌锚点" in source
    assert "不能前几条写完整、后几条只写角色名" in source
    assert "后续图片不会继承前一条 prompt 的人物设定" in source
    assert "同上" in source
    assert "保持上一张设定" in source
