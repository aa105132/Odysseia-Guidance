# -*- coding: utf-8 -*-

import io
import os
import sys

from PIL import Image

sys.path.insert(0, os.path.abspath("."))

from src.chat.services.prompt_service import PromptService
from src.chat.utils.image_utils import extract_image_frames_for_ai


def _build_test_gif(frame_count: int = 6, size=(32, 32)) -> bytes:
    frames = []
    for i in range(frame_count):
        frame = Image.new("RGB", size, (i * 30 % 255, i * 20 % 255, i * 10 % 255))
        frames.append(frame)

    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=80,
        loop=0,
    )
    return output.getvalue()


def _build_test_png(size=(32, 32)) -> bytes:
    image = Image.new("RGB", size, (100, 120, 140))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_extract_image_frames_for_ai_splits_gif():
    gif_bytes = _build_test_gif(frame_count=8)

    frames, meta = extract_image_frames_for_ai(
        image_bytes=gif_bytes,
        mime_type="image/gif",
        max_gif_frames=4,
    )

    assert meta["is_animated"] is True
    assert meta["total_frames"] == 8
    assert meta["sampled_frames"] == 4
    assert len(frames) == 4
    assert meta["frame_indices"][0] == 0
    assert meta["frame_indices"][-1] == 7


def test_extract_image_frames_for_ai_keeps_static_image():
    png_bytes = _build_test_png()

    frames, meta = extract_image_frames_for_ai(
        image_bytes=png_bytes,
        mime_type="image/png",
        max_gif_frames=4,
    )

    assert meta["is_animated"] is False
    assert meta["total_frames"] == 1
    assert meta["sampled_frames"] == 1
    assert len(frames) == 1


def test_create_image_context_turn_contains_gif_frames():
    prompt_service = PromptService()
    gif_bytes = _build_test_gif(frame_count=5)

    turn = prompt_service.create_image_context_turn(
        image_data=gif_bytes,
        mime_type="image/gif",
        description="测试GIF",
    )

    assert turn["role"] == "user"
    assert isinstance(turn["parts"], list)

    text_parts = [part for part in turn["parts"] if isinstance(part, str)]
    image_parts = [part for part in turn["parts"] if isinstance(part, Image.Image)]

    assert any("拼图" in text for text in text_parts)
    assert len(image_parts) == 1


def test_build_chat_prompt_auto_injects_gif_storyboard_and_notice():
    prompt_service = PromptService()
    gif_bytes = _build_test_gif(frame_count=6)

    conversation = prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="请描述这张动图",
        replied_message=None,
        images=[
            {
                "data": gif_bytes,
                "mime_type": "image/gif",
                "source": "attachment",
            }
        ],
        channel_context=[],
        world_book_entries=[],
        affection_status=None,
        guild_name="测试服务器",
        location_name="测试频道",
        user_id=123456,
    )

    user_parts = []
    for turn in conversation:
        if turn.get("role") == "user":
            user_parts.extend(turn.get("parts", []))

    text_parts = [part for part in user_parts if isinstance(part, str)]
    image_parts = [part for part in user_parts if isinstance(part, Image.Image)]

    assert any("用户发送了一张GIF动图" in text for text in text_parts)
    assert any("时间序列拼图" in text for text in text_parts)
    assert len(image_parts) == 1
