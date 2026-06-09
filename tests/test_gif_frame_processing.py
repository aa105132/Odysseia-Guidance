# -*- coding: utf-8 -*-

import io
import os
import sys
import types

from PIL import Image

sys.path.insert(0, os.path.abspath("."))

from src.chat.services.prompt_service import PromptService
from src.chat.services.message_processor import MessageProcessor
from src.chat.utils.image_utils import (
    extract_image_frames_for_ai,
    extract_video_frames_for_ai,
    extract_video_tail_frame_for_ai,
)


def _install_fake_cv2(monkeypatch, frame_count: int = 8, fps: float = 4.0):
    class _FakeVideoCapture:
        def __init__(self, _path):
            self.current = 0

        def isOpened(self):
            return True

        def get(self, prop):
            if prop == fake_cv2.CAP_PROP_FRAME_COUNT:
                return frame_count
            if prop == fake_cv2.CAP_PROP_FPS:
                return fps
            return 0

        def set(self, prop, value):
            if prop == fake_cv2.CAP_PROP_POS_FRAMES:
                self.current = int(value)
            return True

        def read(self):
            frame = Image.new(
                "RGB",
                (32, 32),
                (
                    self.current * 30 % 255,
                    self.current * 20 % 255,
                    self.current * 10 % 255,
                ),
            )
            return True, frame

        def release(self):
            return None

    fake_cv2 = types.SimpleNamespace(
        CAP_PROP_FRAME_COUNT=1,
        CAP_PROP_FPS=2,
        CAP_PROP_POS_FRAMES=3,
        COLOR_BGR2RGB=4,
        VideoCapture=_FakeVideoCapture,
        cvtColor=lambda frame, _code: frame,
    )
    monkeypatch.setitem(sys.modules, "cv2", fake_cv2)


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


def test_extract_video_frames_for_ai_samples_video(monkeypatch):
    _install_fake_cv2(monkeypatch, frame_count=8, fps=4.0)

    frames, meta = extract_video_frames_for_ai(
        video_bytes=b"fake-video",
        mime_type="video/mp4",
        max_video_frames=4,
    )

    assert meta["is_video"] is True
    assert meta["total_frames"] == 8
    assert meta["sampled_frames"] == 4
    assert meta["duration_seconds"] == 2.0
    assert len(frames) == 4
    assert meta["frame_indices"][0] == 0
    assert meta["frame_indices"][-1] == 7


def test_extract_video_tail_frame_falls_back_to_ffmpeg(monkeypatch):
    from src.chat.utils import image_utils

    def _raise_opencv_missing(*_args, **_kwargs):
        raise RuntimeError("缺少 opencv-python-headless，无法抽取视频帧。")

    def _fake_run(command, stdout=None, stderr=None, timeout=None, check=False):
        output_path = command[-1]
        frame = Image.new("RGB", (32, 32), (10, 20, 30))
        frame.save(output_path, format="PNG")

        class _Result:
            returncode = 0
            stderr = b""

        return _Result()

    monkeypatch.setattr(image_utils, "extract_video_frames_for_ai", _raise_opencv_missing)
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
    monkeypatch.setattr("subprocess.run", _fake_run)

    frame, meta = extract_video_tail_frame_for_ai(
        video_bytes=b"fake-video",
        mime_type="video/mp4",
    )

    assert frame.size == (32, 32)
    assert frame.mode == "RGB"
    assert meta["tail_frame_extractor"] == "ffmpeg"
    assert meta["sampled_frames"] == 1


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


def test_build_chat_prompt_auto_injects_video_storyboard_and_notice(monkeypatch):
    _install_fake_cv2(monkeypatch, frame_count=8, fps=4.0)
    prompt_service = PromptService()

    conversation = prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="请描述这个视频",
        replied_message=None,
        images=[
            {
                "data": b"fake-video",
                "mime_type": "video/mp4",
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

    assert any("用户发送了一个视频" in text for text in text_parts)
    assert any("视频时间序列拼图" in text for text in text_parts)
    assert any("视频尾帧" in text for text in text_parts)
    assert len(image_parts) == 2


def test_message_processor_extracts_video_attachment():
    class _FakeAttachment:
        content_type = "video/mp4"
        filename = "demo.mp4"
        size = 10

        async def read(self):
            return b"fake-video"

    processor = MessageProcessor()
    result = __import__("asyncio").run(
        processor._extract_images_from_attachments([_FakeAttachment()])
    )

    assert result[0]["mime_type"] == "video/mp4"
    assert result[0]["data"] == b"fake-video"
    assert result[0]["filename"] == "demo.mp4"


def test_message_processor_extracts_sticker_image(monkeypatch):
    from src.chat.features.tools.utils import discord_image_utils

    class _FakeSticker:
        id = 123456
        name = "测试贴纸"

    class _FakeMessage:
        stickers = [_FakeSticker()]

    async def _fake_fetch_sticker_image(sticker):
        return {
            "mime_type": "image/png",
            "data": b"fake-sticker",
            "filename": f"sticker_{sticker.id}.png",
        }

    monkeypatch.setattr(
        discord_image_utils,
        "fetch_sticker_image",
        _fake_fetch_sticker_image,
    )

    processor = MessageProcessor()
    result = __import__("asyncio").run(
        processor._extract_sticker_images_from_message(
            _FakeMessage(),
            source="sticker",
        )
    )

    assert result[0]["mime_type"] == "image/png"
    assert result[0]["data"] == b"fake-sticker"
    assert result[0]["source"] == "sticker"
    assert result[0]["sticker_name"] == "测试贴纸"


def test_fetch_lottie_sticker_tries_static_preview(monkeypatch):
    import importlib.util
    from pathlib import Path

    # 组合运行时，其他测试会把 discord_image_utils 注入为 MagicMock。
    # 这里按文件路径加载真实模块，专门验证贴纸下载逻辑本身。
    module_path = (
        Path(__file__).resolve().parents[1]
        / "src/chat/features/tools/utils/discord_image_utils.py"
    )
    spec = importlib.util.spec_from_file_location(
        "_real_discord_image_utils_for_test",
        module_path,
    )
    discord_image_utils = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(discord_image_utils)

    requested_urls = []

    class _FakeFormat:
        value = 3

    class _FakeSticker:
        id = 654321
        name = "Lottie贴纸"
        format = _FakeFormat()
        url = "https://cdn.discordapp.com/stickers/654321.json"

    class _FakeResponse:
        status = 200
        headers = {"Content-Type": "image/png"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def read(self):
            return b"static-preview"

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def get(self, url, timeout=None):
            requested_urls.append(url)
            return _FakeResponse()

    monkeypatch.setattr(
        discord_image_utils.aiohttp,
        "ClientSession",
        lambda: _FakeSession(),
    )

    result = __import__("asyncio").run(
        discord_image_utils.fetch_sticker_image(_FakeSticker())
    )

    assert result["mime_type"] == "image/png"
    assert result["data"] == b"static-preview"
    assert requested_urls[0].endswith("/654321.png?size=512")


def test_build_chat_prompt_auto_injects_embed_video_storyboard(monkeypatch):
    _install_fake_cv2(monkeypatch, frame_count=6, fps=3.0)
    prompt_service = PromptService()

    conversation = prompt_service.build_chat_prompt(
        user_name="测试用户",
        message="看看这个生成视频",
        replied_message=None,
        images=[
            {
                "data": b"fake-embed-video",
                "mime_type": "video/mp4",
                "source": "embed",
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

    assert any("用户发送了一个视频" in text for text in text_parts)
    assert any("视频时间序列拼图" in text for text in text_parts)
    assert any("视频尾帧" in text for text in text_parts)
    assert len(image_parts) == 2
