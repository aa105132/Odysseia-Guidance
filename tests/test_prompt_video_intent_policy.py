# -*- coding: utf-8 -*-

from pathlib import Path


def test_global_prompt_says_images_are_not_video_requests():
    prompt_source = Path("src/chat/config/prompts.py").read_text(encoding="utf-8")

    assert "图片、截图、附件图片、回复图片本身不是视频请求" in prompt_source
    assert "只有用户明确说" in prompt_source
    assert "才允许调用 `generate_video`" in prompt_source
    assert "动画风格" in prompt_source
    assert "自动调用 `generate_video`" in prompt_source
