# -*- coding: utf-8 -*-

import asyncio
import importlib
import os
import sys
import types


sys.path.insert(0, os.path.abspath("."))


if "discord" not in sys.modules:
    discord_module = types.ModuleType("discord")

    class _DummyEmbed:
        def __init__(self, *args, **kwargs):
            self.fields = []

        def set_author(self, *args, **kwargs):
            return None

        def add_field(self, *args, **kwargs):
            self.fields.append((args, kwargs))
            return None

        def set_footer(self, *args, **kwargs):
            return None

    class _DummyFile:
        def __init__(self, fp=None, filename=None, spoiler=False, **kwargs):
            self.fp = fp
            self.filename = filename
            self.spoiler = spoiler
            self.kwargs = kwargs

    class _DummyMessage:
        pass

    discord_module.Embed = _DummyEmbed
    discord_module.File = _DummyFile
    discord_module.Message = _DummyMessage
    discord_module.SelectOption = type("SelectOption", (), {})
    discord_module.abc = types.SimpleNamespace(User=type("User", (), {}))
    sys.modules["discord"] = discord_module

dummy_regenerate_view_module = types.ModuleType(
    "src.chat.features.tools.ui.regenerate_view"
)


class _DummyRegenerateView:
    def __init__(self, *args, **kwargs):
        pass


dummy_regenerate_view_module.RegenerateView = _DummyRegenerateView
sys.modules["src.chat.features.tools.ui.regenerate_view"] = dummy_regenerate_view_module


generate_image_tool = importlib.import_module(
    "src.chat.features.tools.functions.generate_image"
)
edit_image_tool = importlib.import_module(
    "src.chat.features.tools.functions.edit_image"
)
imagen_service_module = importlib.import_module(
    "src.chat.features.image_generation.services.gemini_imagen_service"
)
app_config = importlib.import_module("src.chat.config.chat_config")


class DummySentMessage:
    id = 123
    guild = None
    channel = types.SimpleNamespace(id=456)


class DummyChannel:
    def __init__(self):
        self.sent_payloads = []

    async def send(self, **kwargs):
        self.sent_payloads.append(kwargs)
        return DummySentMessage()


def _extract_first_spoiler_flag(channel: DummyChannel) -> bool:
    assert channel.sent_payloads, "预期至少发送一条消息"
    payload = channel.sent_payloads[0]
    if "file" in payload:
        return payload["file"].spoiler
    if "files" in payload:
        assert payload["files"], "预期 files 非空"
        return payload["files"][0].spoiler
    raise AssertionError("消息中未携带 file/files")


def test_generate_image_sfw_should_not_use_spoiler(monkeypatch):
    channel = DummyChannel()

    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "IMAGE_GENERATION_COST", 0)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "DEFAULT_NUMBER_OF_IMAGES", 1)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "MAX_IMAGES_PER_REQUEST", 4)
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "is_available",
        lambda: True,
    )

    async def fake_generate_single_image(**kwargs):
        return b"fake-image"

    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "generate_single_image",
        fake_generate_single_image,
    )
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "_get_model_for_resolution",
        lambda **kwargs: "imagen-sfw-test",
    )

    result = asyncio.run(
        generate_image_tool.generate_image(
            prompt="一只可爱的小猫，柔和光线，细节丰富",
            content_rating="sfw",
            preview_message=None,
            success_message=None,
            number_of_images=1,
            channel=channel,
        )
    )

    assert result["success"] is True
    assert _extract_first_spoiler_flag(channel) is False


def test_edit_image_sfw_should_not_use_spoiler(monkeypatch):
    channel = DummyChannel()

    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "IMAGE_EDIT_COST", 0)
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "is_available",
        lambda: True,
    )

    async def fake_edit_image(**kwargs):
        return b"edited-image"

    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "edit_image",
        fake_edit_image,
    )
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "_get_model_for_resolution",
        lambda **kwargs: "imagen-edit-sfw-test",
    )

    result = asyncio.run(
        edit_image_tool.edit_image(
            edit_prompt="把图片改成更明亮的白天场景",
            content_rating="sfw",
            preview_message=None,
            success_message=None,
            channel=channel,
            prepared_reference_images=[{"data": b"ref-image", "mime_type": "image/png"}],
        )
    )

    assert result["success"] is True
    assert _extract_first_spoiler_flag(channel) is False


def test_edit_image_does_not_fallback_to_message_image_when_avatar_missing(monkeypatch):
    class _FakeAttachment:
        content_type = "image/png"
        filename = "unrelated.png"

        async def read(self):
            raise AssertionError("头像失败时不应读取无关消息图片")

    fake_message = types.SimpleNamespace(
        id=1,
        guild=None,
        content="",
        stickers=[],
        attachments=[_FakeAttachment()],
        reference=None,
    )

    async def fake_fetch_avatar_image(*args, **kwargs):
        return None

    discord_image_utils_module = importlib.import_module(
        "src.chat.features.tools.utils.discord_image_utils"
    )
    monkeypatch.setattr(
        discord_image_utils_module,
        "fetch_avatar_image",
        fake_fetch_avatar_image,
    )

    result = asyncio.run(
        edit_image_tool.edit_image(
            edit_prompt="按指定用户头像改成特摄风格",
            avatar_user_id="1172726720378446080",
            preview_message=None,
            success_message=None,
            message=fake_message,
        )
    )

    assert result["edit_failed"] is True
    assert result["reason"] == "avatar_image_not_found"


def test_generate_image_nsfw_should_keep_spoiler(monkeypatch):
    channel = DummyChannel()

    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "IMAGE_GENERATION_COST", 0)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "DEFAULT_NUMBER_OF_IMAGES", 1)
    monkeypatch.setitem(app_config.GEMINI_IMAGEN_CONFIG, "MAX_IMAGES_PER_REQUEST", 4)
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "is_available",
        lambda: True,
    )

    async def fake_generate_single_image(**kwargs):
        return b"fake-image"

    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "generate_single_image",
        fake_generate_single_image,
    )
    monkeypatch.setattr(
        imagen_service_module.gemini_imagen_service,
        "_get_model_for_resolution",
        lambda **kwargs: "imagen-nsfw-test",
    )

    result = asyncio.run(
        generate_image_tool.generate_image(
            prompt="暧昧氛围的人像，柔和灯光，细节丰富",
            content_rating="nsfw",
            preview_message=None,
            success_message=None,
            number_of_images=1,
            channel=channel,
        )
    )

    assert result["success"] is True
    assert _extract_first_spoiler_flag(channel) is True
