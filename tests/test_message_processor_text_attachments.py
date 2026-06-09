# -*- coding: utf-8 -*-

import asyncio
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

if "discord" not in sys.modules:
    discord_module = types.ModuleType("discord")
    discord_module.Thread = type("Thread", (), {})
    discord_module.Message = type("Message", (), {})
    discord_module.Client = type("Client", (), {})
    discord_module.ClientUser = type("ClientUser", (), {})
    discord_module.Attachment = type("Attachment", (), {})
    discord_module.Embed = type("Embed", (), {})
    discord_module.NotFound = type("NotFound", (Exception,), {})
    discord_module.Forbidden = type("Forbidden", (Exception,), {})
    sys.modules["discord"] = discord_module


from src.chat.services.message_processor import MessageProcessor
import src.chat.services.message_processor as message_processor_module


class _FakeThread:
    pass


class _FakeAttachment:
    def __init__(self, filename: str, content_type: str, data: bytes, size: int | None = None):
        self.filename = filename
        self.content_type = content_type
        self._data = data
        self.size = len(data) if size is None else size

    async def read(self):
        return self._data


class _FakeChannel:
    def __init__(self, message_to_fetch=None):
        self.id = 1001
        self.name = "测试频道"
        self._message_to_fetch = message_to_fetch

    async def fetch_message(self, _message_id):
        return self._message_to_fetch


class _FakeAuthor:
    def __init__(self, user_id: int, display_name: str):
        self.id = user_id
        self.display_name = display_name


class _FakeGuild:
    def __init__(self):
        self.me = _FakeAuthor(9999, "月月")


def test_process_message_reads_current_text_attachment_full_content(monkeypatch):
    monkeypatch.setattr(
        message_processor_module.chat_db_manager,
        "is_channel_muted",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(message_processor_module.discord, "Thread", _FakeThread)

    processor = MessageProcessor()
    attachment = _FakeAttachment(
        filename="设定.txt",
        content_type="text/plain",
        data="姓名：小雪\n外貌：银发蓝眼，穿白裙。".encode("utf-8"),
    )
    message = SimpleNamespace(
        channel=_FakeChannel(),
        guild=_FakeGuild(),
        attachments=[attachment],
        content="帮我看看这个文件",
        embeds=[],
        reference=None,
        mentions=[],
    )

    result = asyncio.run(processor.process_message(message, bot=SimpleNamespace()))

    assert "帮我看看这个文件" in result["user_content"]
    assert "[用户上传的文本附件: 设定.txt]" in result["user_content"]
    assert "[附件全文开始]" in result["user_content"]
    assert "姓名：小雪\n外貌：银发蓝眼，穿白裙。" in result["user_content"]
    assert "[附件全文结束]" in result["user_content"]


def test_process_message_reads_replied_text_attachment_full_content(monkeypatch):
    monkeypatch.setattr(
        message_processor_module.chat_db_manager,
        "is_channel_muted",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(message_processor_module.discord, "Thread", _FakeThread)

    processor = MessageProcessor()
    replied_attachment = _FakeAttachment(
        filename="人设.md",
        content_type="text/markdown",
        data="## 角色外貌\n黑发红瞳，披风。".encode("utf-8"),
    )
    replied_message = SimpleNamespace(
        id=2222,
        author=_FakeAuthor(1234, "上传者"),
        content="这是设定文件",
        mentions=[],
        embeds=[],
        attachments=[replied_attachment],
    )
    message = SimpleNamespace(
        channel=_FakeChannel(message_to_fetch=replied_message),
        guild=_FakeGuild(),
        attachments=[],
        content="按这个文件来画",
        embeds=[],
        reference=SimpleNamespace(message_id=2222),
        mentions=[],
    )

    result = asyncio.run(processor.process_message(message, bot=SimpleNamespace()))

    assert "> [上传者]:" in result["replied_content"]
    assert "[回复消息包含文本附件: 人设.md]" in result["replied_content"]
    assert "## 角色外貌" in result["replied_content"]
    assert "黑发红瞳，披风。" in result["replied_content"]
    assert "[附件全文结束]" in result["replied_content"]


def test_extract_text_link_supports_artifact_video_url(monkeypatch):
    processor = MessageProcessor()

    async def _fake_fetch_media(session, url, proxy=None):
        assert url == "https://artifact.anycap.cloud/a/art_video123"
        return {
            "data": b"fake-video-bytes",
            "mime_type": "application/octet-stream",
            "final_url": url,
        }

    monkeypatch.setattr(processor, "_fetch_image_aio", _fake_fetch_media)

    result = asyncio.run(
        processor._extract_images_from_text_links(
            "月月看看这个视频 https://artifact.anycap.cloud/a/art_video123",
            source="attachment",
        )
    )

    assert result == [
        {
            "mime_type": "video/mp4",
            "data": b"fake-video-bytes",
            "source": "attachment",
            "filename": "art_video123",
        }
    ]


def test_extract_embed_field_video_link(monkeypatch):
    processor = MessageProcessor()

    async def _fake_fetch_media(session, url, proxy=None):
        assert url == "https://artifact.anycap.cloud/a/art_from_embed"
        return {
            "data": b"embed-video-bytes",
            "mime_type": "video/mp4",
            "final_url": url,
        }

    monkeypatch.setattr(processor, "_fetch_image_aio", _fake_fetch_media)

    embed = SimpleNamespace(
        image=None,
        thumbnail=None,
        video=None,
        title="AI 视频生成",
        description="",
        url=None,
        fields=[
            SimpleNamespace(
                name="视频链接",
                value="[点击观看](https://artifact.anycap.cloud/a/art_from_embed)",
            )
        ],
    )

    result = asyncio.run(processor._extract_images_from_embeds([embed]))

    assert result == [
        {
            "mime_type": "video/mp4",
            "data": b"embed-video-bytes",
            "source": "embed",
            "filename": "art_from_embed",
        }
    ]


def test_process_message_reads_current_json_attachment_full_content(monkeypatch):
    monkeypatch.setattr(
        message_processor_module.chat_db_manager,
        "is_channel_muted",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(message_processor_module.discord, "Thread", _FakeThread)

    processor = MessageProcessor()
    attachment = _FakeAttachment(
        filename="config.json",
        content_type="application/json",
        data='{"角色":"月月","能力":["看图","看视频"]}'.encode("utf-8"),
    )
    message = SimpleNamespace(
        channel=_FakeChannel(),
        guild=_FakeGuild(),
        attachments=[attachment],
        content="读一下这个 JSON",
        embeds=[],
        reference=None,
        mentions=[],
    )

    result = asyncio.run(processor.process_message(message, bot=SimpleNamespace()))

    assert "读一下这个 JSON" in result["user_content"]
    assert "[用户上传的文本附件: config.json]" in result["user_content"]
    assert '"角色":"月月"' in result["user_content"]
    assert '"看视频"' in result["user_content"]


def test_text_attachment_supports_json_suffix_mime_without_filename_extension():
    processor = MessageProcessor()
    attachment = _FakeAttachment(
        filename="payload",
        content_type="application/problem+json; charset=utf-8",
        data=b'{"error":"bad_request"}',
    )

    assert processor._is_supported_text_attachment(attachment) is True


def test_message_processor_extracts_mp4_attachment_with_missing_content_type():
    class _FakeVideoAttachment:
        filename = "demo.mp4"
        content_type = None
        size = 10
        url = "https://cdn.discordapp.com/attachments/1/2/demo.mp4"

        async def read(self):
            return b"mp4-bytes"

    processor = MessageProcessor()
    result = asyncio.run(
        processor._extract_images_from_attachments([_FakeVideoAttachment()])
    )

    assert result == [
        {
            "mime_type": "video/mp4",
            "data": b"mp4-bytes",
            "source": "attachment",
            "filename": "demo.mp4",
        }
    ]


def test_message_processor_downloads_video_attachment_when_read_empty(monkeypatch):
    class _FakeVideoAttachment:
        filename = "clip"
        content_type = "application/octet-stream"
        size = 10
        url = "https://cdn.discordapp.com/attachments/1/2/clip.mp4"

        async def read(self):
            return b""

    async def _fake_fetch_media(session, url, proxy=None):
        assert url == "https://cdn.discordapp.com/attachments/1/2/clip.mp4"
        return {
            "data": b"downloaded-mp4",
            "mime_type": "application/octet-stream",
            "final_url": url,
        }

    processor = MessageProcessor()
    monkeypatch.setattr(processor, "_fetch_image_aio", _fake_fetch_media)

    result = asyncio.run(
        processor._extract_images_from_attachments([_FakeVideoAttachment()])
    )

    assert result == [
        {
            "mime_type": "video/mp4",
            "data": b"downloaded-mp4",
            "source": "attachment",
            "filename": "clip",
        }
    ]
