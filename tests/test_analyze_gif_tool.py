# -*- coding: utf-8 -*-

import io
import os
import sys
import asyncio

from PIL import Image

sys.path.insert(0, os.path.abspath("."))

from src.chat.features.tools.functions.analyze_gif import analyze_gif


def _build_test_gif(frame_count: int = 6, size=(40, 40)) -> bytes:
    frames = []
    for i in range(frame_count):
        frames.append(Image.new("RGB", size, (i * 25 % 255, i * 40 % 255, i * 55 % 255)))

    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=60,
        loop=0,
    )
    return output.getvalue()


def _build_test_png(size=(40, 40)) -> bytes:
    image = Image.new("RGB", size, (120, 140, 160))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class _FakeAttachment:
    def __init__(self, data: bytes, filename: str, content_type: str):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self) -> bytes:
        return self._data


class _FakeMessage:
    def __init__(self, attachments):
        self.attachments = attachments
        self.content = ""
        self.embeds = []
        self.reference = None


def test_analyze_gif_extracts_storyboard_from_attachment():
    gif_bytes = _build_test_gif(frame_count=8)
    message = _FakeMessage(
        attachments=[
            _FakeAttachment(
                data=gif_bytes,
                filename="demo.gif",
                content_type="image/gif",
            )
        ]
    )

    result = asyncio.run(analyze_gif(message=message, max_frames=4))

    assert "image_data" in result
    assert result["image_data"]["mime_type"] == "image/png"
    assert len(result["image_data"]["data"]) > 0

    frame_info = result.get("frame_info", {})
    assert frame_info.get("sampled_frames") == 4
    assert frame_info.get("total_frames") == 8


def test_analyze_gif_rejects_static_image():
    png_bytes = _build_test_png()
    message = _FakeMessage(
        attachments=[
            _FakeAttachment(
                data=png_bytes,
                filename="static.png",
                content_type="image/png",
            )
        ]
    )

    result = asyncio.run(analyze_gif(message=message, max_frames=4))

    assert result.get("error") is True
    assert "没有找到可分析的 GIF" in result.get("hint", "")
