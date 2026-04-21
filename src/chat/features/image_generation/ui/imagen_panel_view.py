# -*- coding: utf-8 -*-

"""
Imagen / Grok 图片生成交互面板
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import discord
from discord.ext import commands

from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
from src.chat.features.tools.functions.edit_image import edit_image as edit_image_tool
from src.chat.features.tools.functions.generate_image import (
    generate_image as generate_image_tool,
)
from src.chat.features.tools.utils.discord_image_utils import fetch_avatar_image
from ..services.gemini_imagen_service import gemini_imagen_service

log = logging.getLogger(__name__)

VALID_ASPECT_RATIOS = {"1:1", "3:4", "4:3", "9:16", "16:9"}
VALID_RESOLUTIONS = {"default", "2k", "4k"}
VALID_CONTENT_RATINGS = {"sfw", "nsfw"}
VALID_RESPONSE_FORMATS = {"url", "b64_json", "base64"}
VALID_IMAGE_API_MODES = {"auto", "images_api", "chat_completions"}
MAX_REFERENCE_IMAGES = 3


def _is_image_attachment(attachment: discord.Attachment) -> bool:
    content_type = str(getattr(attachment, "content_type", "") or "").lower()
    filename = str(getattr(attachment, "filename", "") or "").lower()
    if content_type.startswith("image/"):
        return True
    return filename.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".avif"))


def _parse_bool_text(raw_value: str) -> Optional[bool]:
    text = str(raw_value or "").strip().lower()
    if not text or text in {"none", "null", "auto", "default"}:
        return None
    if text in {"true", "1", "yes", "y", "on"}:
        return True
    if text in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError("`stream` 只支持 true / false / none。")


def _normalize_choice(
    raw_value: str,
    valid_values: set[str],
    field_name: str,
    allow_empty: bool = False,
) -> Optional[str]:
    text = str(raw_value or "").strip().lower()
    if not text:
        return None if allow_empty else ""
    if text not in valid_values:
        valid_text = " / ".join(sorted(valid_values))
        raise ValueError(f"`{field_name}` 只支持：{valid_text}")
    return text


def _parse_extra_options(raw_text: str) -> Dict[str, str]:
    result: Dict[str, str] = {}
    text = str(raw_text or "").strip()
    if not text:
        return result

    for part in text.replace("\n", ";").split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" in item:
            key, value = item.split("=", 1)
        elif ":" in item:
            key, value = item.split(":", 1)
        else:
            raise ValueError("扩展参数请使用 `key=value` 或 `key:value` 格式。")
        result[str(key).strip().lower()] = str(value).strip()
    return result


class ImagenPromptModal(discord.ui.Modal, title="月月画图提示词"):
    def __init__(self, panel_view: "ImagenGenerationPanelView"):
        super().__init__()
        self.panel_view = panel_view

        self.prompt_input = discord.ui.TextInput(
            label="提示词",
            placeholder="输入你想生成或修改的画面内容",
            required=False,
            max_length=2000,
            style=discord.TextStyle.paragraph,
            default=panel_view.prompt,
        )
        self.negative_prompt_input = discord.ui.TextInput(
            label="负面提示词",
            placeholder="可留空，仅文生图会使用",
            required=False,
            max_length=1000,
            style=discord.TextStyle.paragraph,
            default=panel_view.negative_prompt,
        )
        self.add_item(self.prompt_input)
        self.add_item(self.negative_prompt_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.panel_view.prompt = str(self.prompt_input.value or "").strip()
        self.panel_view.negative_prompt = str(self.negative_prompt_input.value or "").strip()
        await interaction.response.send_message("提示词已更新。", ephemeral=True)
        await self.panel_view.refresh_panel_message()


class ImagenBasicParamsModal(discord.ui.Modal, title="基础参数设置"):
    def __init__(self, panel_view: "ImagenGenerationPanelView"):
        super().__init__()
        self.panel_view = panel_view

        self.aspect_ratio_input = discord.ui.TextInput(
            label="宽高比",
            placeholder="1:1 / 3:4 / 4:3 / 9:16 / 16:9",
            required=True,
            max_length=10,
            style=discord.TextStyle.short,
            default=panel_view.aspect_ratio,
        )
        self.count_input = discord.ui.TextInput(
            label="数量",
            placeholder="1 - 20",
            required=True,
            max_length=3,
            style=discord.TextStyle.short,
            default=str(panel_view.count),
        )
        self.resolution_input = discord.ui.TextInput(
            label="分辨率",
            placeholder="default / 2k / 4k",
            required=True,
            max_length=16,
            style=discord.TextStyle.short,
            default=panel_view.resolution,
        )
        self.content_rating_input = discord.ui.TextInput(
            label="内容分级",
            placeholder="sfw / nsfw",
            required=True,
            max_length=8,
            style=discord.TextStyle.short,
            default=panel_view.content_rating,
        )

        self.add_item(self.aspect_ratio_input)
        self.add_item(self.count_input)
        self.add_item(self.resolution_input)
        self.add_item(self.content_rating_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        aspect_ratio = str(self.aspect_ratio_input.value or "").strip()
        if aspect_ratio not in VALID_ASPECT_RATIOS:
            raise ValueError("宽高比只支持 1:1 / 3:4 / 4:3 / 9:16 / 16:9。")

        try:
            count = int(str(self.count_input.value or "1").strip())
        except ValueError as error:
            raise ValueError("数量必须是整数。") from error
        count = min(max(count, 1), 20)

        resolution = _normalize_choice(
            self.resolution_input.value,
            VALID_RESOLUTIONS,
            "resolution",
        )
        content_rating = _normalize_choice(
            self.content_rating_input.value,
            VALID_CONTENT_RATINGS,
            "content_rating",
        )

        self.panel_view.aspect_ratio = aspect_ratio
        self.panel_view.count = count
        self.panel_view.resolution = resolution or "default"
        self.panel_view.content_rating = content_rating or "sfw"

        await interaction.response.send_message("基础参数已更新。", ephemeral=True)
        await self.panel_view.refresh_panel_message()

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        await interaction.response.send_message(str(error), ephemeral=True)


class ImagenOpenAIParamsModal(discord.ui.Modal, title="OpenAI 图片接口参数"):
    def __init__(self, panel_view: "ImagenGenerationPanelView"):
        super().__init__()
        self.panel_view = panel_view

        self.model_name_input = discord.ui.TextInput(
            label="模型覆盖",
            placeholder="例如 grok-imagine-1.0",
            required=False,
            max_length=120,
            style=discord.TextStyle.short,
            default=panel_view.model_name_override or "",
        )
        self.image_size_input = discord.ui.TextInput(
            label="图片尺寸",
            placeholder="例如 1024x1024 / 1792x1024",
            required=False,
            max_length=30,
            style=discord.TextStyle.short,
            default=panel_view.openai_image_size or "",
        )
        self.response_format_input = discord.ui.TextInput(
            label="返回格式",
            placeholder="url / b64_json / base64，留空走默认",
            required=False,
            max_length=20,
            style=discord.TextStyle.short,
            default=panel_view.openai_response_format or "",
        )
        self.switches_input = discord.ui.TextInput(
            label="流式与路由",
            placeholder="stream=true; api_mode=images_api",
            required=False,
            max_length=120,
            style=discord.TextStyle.short,
            default=self._build_switches_default(),
        )
        self.extras_input = discord.ui.TextInput(
            label="质量与风格",
            placeholder="quality=high; style=anime",
            required=False,
            max_length=200,
            style=discord.TextStyle.short,
            default=self._build_extras_default(),
        )

        self.add_item(self.model_name_input)
        self.add_item(self.image_size_input)
        self.add_item(self.response_format_input)
        self.add_item(self.switches_input)
        self.add_item(self.extras_input)

    def _build_switches_default(self) -> str:
        parts: List[str] = []
        if self.panel_view.openai_stream is not None:
            parts.append(f"stream={'true' if self.panel_view.openai_stream else 'false'}")
        if self.panel_view.openai_image_api_mode:
            parts.append(f"api_mode={self.panel_view.openai_image_api_mode}")
        return "; ".join(parts)

    def _build_extras_default(self) -> str:
        parts: List[str] = []
        if self.panel_view.openai_quality:
            parts.append(f"quality={self.panel_view.openai_quality}")
        if self.panel_view.openai_style:
            parts.append(f"style={self.panel_view.openai_style}")
        return "; ".join(parts)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        response_format = _normalize_choice(
            self.response_format_input.value,
            VALID_RESPONSE_FORMATS,
            "response_format",
            allow_empty=True,
        )

        switches = _parse_extra_options(self.switches_input.value)
        extras = _parse_extra_options(self.extras_input.value)

        openai_stream = _parse_bool_text(switches.get("stream", ""))
        raw_api_mode = switches.get("api_mode", switches.get("mode", ""))
        openai_image_api_mode = _normalize_choice(
            raw_api_mode,
            VALID_IMAGE_API_MODES,
            "api_mode",
            allow_empty=True,
        )

        self.panel_view.model_name_override = str(self.model_name_input.value or "").strip() or None
        self.panel_view.openai_image_size = str(self.image_size_input.value or "").strip() or None
        self.panel_view.openai_response_format = response_format
        self.panel_view.openai_stream = openai_stream
        self.panel_view.openai_image_api_mode = openai_image_api_mode
        self.panel_view.openai_quality = str(extras.get("quality", "")).strip() or None
        self.panel_view.openai_style = str(extras.get("style", "")).strip() or None

        await interaction.response.send_message("OpenAI 图片接口参数已更新。", ephemeral=True)
        await self.panel_view.refresh_panel_message()

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
    ) -> None:
        await interaction.response.send_message(str(error), ephemeral=True)


class ImagenGenerationPanelView(discord.ui.View):
    def __init__(
        self,
        *,
        bot: commands.Bot,
        user_id: int,
        prompt: str = "",
        negative_prompt: str = "",
        mode: str = "image",
        aspect_ratio: Optional[str] = None,
        count: int = 1,
        resolution: str = "default",
        content_rating: str = "sfw",
        model_name_override: Optional[str] = None,
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
        timeout: float = 600,
    ) -> None:
        super().__init__(timeout=timeout)
        self.bot = bot
        self.user_id = user_id
        self.prompt = str(prompt or "").strip()
        self.negative_prompt = str(negative_prompt or "").strip()
        self.mode = "image_edit" if mode == "image_edit" else "image"
        self.aspect_ratio = aspect_ratio or GEMINI_IMAGEN_CONFIG.get("DEFAULT_ASPECT_RATIO", "1:1")
        self.count = min(max(int(count or 1), 1), 20)
        self.resolution = resolution if resolution in VALID_RESOLUTIONS else "default"
        self.content_rating = content_rating if content_rating in VALID_CONTENT_RATINGS else "sfw"
        self.model_name_override = str(model_name_override or "").strip() or None
        self.openai_image_size = str(openai_image_size or "").strip() or None
        self.openai_response_format = (
            openai_response_format if openai_response_format in VALID_RESPONSE_FORMATS else None
        )
        self.openai_stream = openai_stream
        self.openai_quality = str(openai_quality or "").strip() or None
        self.openai_style = str(openai_style or "").strip() or None
        self.openai_image_api_mode = (
            openai_image_api_mode
            if openai_image_api_mode in VALID_IMAGE_API_MODES
            else None
        )
        self.reference_images: List[Dict[str, Any]] = []
        self.panel_message: Optional[discord.Message] = None
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.toggle_mode_button.label = "切到文生图" if self.mode == "image_edit" else "切到图生图"
        self.start_generation_button.label = "开始图生图" if self.mode == "image_edit" else "开始画图"
        self.clear_reference_button.disabled = not self.reference_images
        self.clear_reference_button.label = (
            f"清空参考图 ({len(self.reference_images)})"
            if self.reference_images
            else "清空参考图"
        )
        self.upload_reference_button.label = (
            f"上传参考图 ({len(self.reference_images)})"
            if self.reference_images
            else "上传参考图"
        )

    def _current_route_hint(self) -> str:
        model_name = self.model_name_override or GEMINI_IMAGEN_CONFIG.get("MODEL_NAME", "")
        if not model_name:
            return self.openai_image_api_mode or "auto"
        try:
            return gemini_imagen_service._resolve_openai_image_api_mode(
                model_name=model_name,
                mode_override=self.openai_image_api_mode,
            )
        except Exception:
            return self.openai_image_api_mode or "auto"

    def build_embed(self) -> discord.Embed:
        self._refresh_buttons()
        title = "Grok / Imagen 绘图面板"
        embed = discord.Embed(title=title, color=0x2B2D31)
        embed.description = (
            "在这里先配置参数，再点击“开始生成”。\n"
            "当模型像 `grok-imagine-*` 时，`auto` 会优先走 `/v1/images/*`；"
            "`gpt-image-*` 默认走 `chat/completions`，需要固定 `/v1/images/*` 时请显式切换模式。"
        )
        embed.add_field(
            name="当前模式",
            value="图生图" if self.mode == "image_edit" else "文生图",
            inline=True,
        )
        embed.add_field(
            name="参考图",
            value=str(len(self.reference_images)),
            inline=True,
        )
        embed.add_field(
            name="预估消耗",
            value=str(
                (GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 1) if self.mode == "image_edit" else GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1))
                * self.count
            ),
            inline=True,
        )
        embed.add_field(
            name="提示词",
            value=f"```\n{(self.prompt or '（未填写）')[:1016]}\n```",
            inline=False,
        )
        embed.add_field(
            name="负面提示词",
            value=f"```\n{(self.negative_prompt or '（未填写）')[:1016]}\n```",
            inline=False,
        )
        embed.add_field(
            name="基础参数",
            value=(
                f"宽高比：`{self.aspect_ratio}`\n"
                f"数量：`{self.count}`\n"
                f"分辨率：`{self.resolution}`\n"
                f"内容分级：`{self.content_rating}`"
            ),
            inline=False,
        )
        embed.add_field(
            name="OpenAI 图片接口参数",
            value=(
                f"模型覆盖：`{self.model_name_override or '默认'}`\n"
                f"图片尺寸：`{self.openai_image_size or '默认'}`\n"
                f"返回格式：`{self.openai_response_format or '默认'}`\n"
                f"stream：`{self.openai_stream if self.openai_stream is not None else '默认'}`\n"
                f"quality：`{self.openai_quality or '默认'}`\n"
                f"style：`{self.openai_style or '默认'}`\n"
                f"路由：`{self.openai_image_api_mode or 'auto'}` → `{self._current_route_hint()}`"
            ),
            inline=False,
        )
        if self.reference_images:
            file_names = [
                str(item.get("filename") or f"reference_{index + 1}.png")
                for index, item in enumerate(self.reference_images)
            ]
            embed.add_field(
                name="已载入参考图",
                value="\n".join(f"- `{name}`" for name in file_names[:MAX_REFERENCE_IMAGES]),
                inline=False,
            )
        else:
            embed.add_field(
                name="已载入参考图",
                value="暂无。点击“上传参考图”后会自动切到图生图。",
                inline=False,
            )
        return embed

    async def refresh_panel_message(self) -> None:
        if not self.panel_message:
            return
        self._refresh_buttons()
        try:
            await self.panel_message.edit(embed=self.build_embed(), view=self)
        except Exception:
            pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这个面板只给命令发起者使用。", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.panel_message:
            try:
                await self.panel_message.edit(view=self)
            except Exception:
                pass

    async def _wait_user_message(
        self,
        interaction: discord.Interaction,
        timeout_seconds: int = 120,
    ) -> Optional[discord.Message]:
        channel = interaction.channel
        if channel is None:
            return None

        def _check(message: discord.Message) -> bool:
            return message.author.id == self.user_id and message.channel.id == channel.id

        try:
            return await self.bot.wait_for("message", timeout=timeout_seconds, check=_check)
        except Exception:
            return None

    async def load_reference_attachments(
        self,
        attachments: List[discord.Attachment],
    ) -> int:
        loaded: List[Dict[str, Any]] = []
        for attachment in attachments:
            if not _is_image_attachment(attachment):
                continue
            image_bytes = await attachment.read()
            if not image_bytes:
                continue
            loaded.append(
                {
                    "data": image_bytes,
                    "mime_type": attachment.content_type or "image/png",
                    "filename": attachment.filename,
                }
            )
        if not loaded:
            return 0
        self.reference_images = loaded[-MAX_REFERENCE_IMAGES:]
        self.mode = "image_edit"
        self._refresh_buttons()
        return len(self.reference_images)

    async def load_avatar_reference(
        self,
        *,
        target_user_id: str,
        guild: Optional[discord.Guild] = None,
    ) -> bool:
        avatar_image = await fetch_avatar_image(
            user_id=target_user_id,
            bot=self.bot,
            guild=guild,
        )
        if not avatar_image or not avatar_image.get("data"):
            return False

        loaded_images = list(self.reference_images)
        loaded_images.append(
            {
                "data": avatar_image["data"],
                "mime_type": avatar_image.get("mime_type") or "image/png",
                "filename": avatar_image.get("filename") or f"avatar_{target_user_id}.png",
                "source": "discord_avatar",
            }
        )
        self.reference_images = loaded_images[-MAX_REFERENCE_IMAGES:]
        self.mode = "image_edit"
        self._refresh_buttons()
        return True

    @discord.ui.button(label="编辑提示词", style=discord.ButtonStyle.primary, row=0)
    async def edit_prompt_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(ImagenPromptModal(self))

    @discord.ui.button(label="基础参数", style=discord.ButtonStyle.secondary, row=0)
    async def basic_params_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(ImagenBasicParamsModal(self))

    @discord.ui.button(label="OpenAI 参数", style=discord.ButtonStyle.secondary, row=0)
    async def openai_params_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_modal(ImagenOpenAIParamsModal(self))

    @discord.ui.button(label="上传参考图", style=discord.ButtonStyle.secondary, row=1)
    async def upload_reference_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        await interaction.response.send_message(
            f"请在当前频道 {120} 秒内发送图片附件，我会取最后 {MAX_REFERENCE_IMAGES} 张作为参考图。",
            ephemeral=True,
        )
        message = await self._wait_user_message(interaction, timeout_seconds=120)
        if message is None:
            await interaction.followup.send("等待上传超时了。", ephemeral=True)
            return

        image_attachments = [attachment for attachment in message.attachments if _is_image_attachment(attachment)]
        if not image_attachments:
            await interaction.followup.send("这条消息里没有可用图片附件。", ephemeral=True)
            return

        loaded_count = await self.load_reference_attachments(image_attachments)
        await interaction.followup.send(f"已载入 {loaded_count} 张参考图，并切到图生图模式。", ephemeral=True)
        await self.refresh_panel_message()

    @discord.ui.button(label="使用我的头像", style=discord.ButtonStyle.secondary, row=1)
    async def use_avatar_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        loaded = await self.load_avatar_reference(
            target_user_id=str(interaction.user.id),
            guild=interaction.guild,
        )
        if not loaded:
            await interaction.response.send_message(
                "暂时读取不到你的 Discord 头像，请稍后重试，或直接上传参考图。",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "已载入你的 Discord 头像作为参考图，并切换到图生图模式。",
            ephemeral=True,
        )
        await self.refresh_panel_message()

    @discord.ui.button(label="清空参考图", style=discord.ButtonStyle.secondary, row=1)
    async def clear_reference_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.reference_images = []
        await interaction.response.send_message("参考图已清空。", ephemeral=True)
        await self.refresh_panel_message()

    @discord.ui.button(label="切换模式", style=discord.ButtonStyle.secondary, row=1)
    async def toggle_mode_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        self.mode = "image" if self.mode == "image_edit" else "image_edit"
        await interaction.response.send_message(
            f"已切换到 {'图生图' if self.mode == 'image_edit' else '文生图'} 模式。",
            ephemeral=True,
        )
        await self.refresh_panel_message()

    @discord.ui.button(label="开始生成", style=discord.ButtonStyle.success, row=2)
    async def start_generation_button(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button,
    ) -> None:
        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("当前频道不可用。", ephemeral=True)
            return
        if not gemini_imagen_service.is_available():
            await interaction.response.send_message("图片生成服务当前不可用。", ephemeral=True)
            return

        prompt = self.prompt.strip()
        if self.mode == "image" and not prompt:
            await interaction.response.send_message("文生图模式至少要填写提示词。", ephemeral=True)
            return

        if self.mode == "image_edit":
            if not self.reference_images:
                await interaction.response.send_message("图生图模式需要至少一张参考图。", ephemeral=True)
                return
            if not prompt:
                prompt = "请在保留主体的前提下优化细节与画质。"

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            if self.mode == "image_edit":
                result = await edit_image_tool(
                    edit_prompt=prompt,
                    aspect_ratio=self.aspect_ratio,
                    resolution=self.resolution,
                    content_rating=self.content_rating,
                    reference_image_mode="multi" if len(self.reference_images) > 1 else "single",
                    max_reference_images=len(self.reference_images),
                    preview_message="正在生成图片...",
                    success_message="图片生成完成~",
                    model_name_override=self.model_name_override,
                    openai_image_size=self.openai_image_size,
                    openai_response_format=self.openai_response_format,
                    openai_stream=self.openai_stream,
                    openai_quality=self.openai_quality,
                    openai_style=self.openai_style,
                    openai_image_api_mode=self.openai_image_api_mode,
                    channel=channel,
                    user_id=str(interaction.user.id),
                    bot=self.bot,
                    request_user=interaction.user,
                    _prepared_reference_images=self.reference_images,
                    _prepared_reference_image=self.reference_images[0],
                )
                if result and result.get("edit_failed"):
                    raise ValueError(result.get("hint") or "图生图失败。")
            else:
                result = await generate_image_tool(
                    prompt=prompt,
                    negative_prompt=self.negative_prompt or None,
                    aspect_ratio=self.aspect_ratio,
                    number_of_images=self.count,
                    resolution=self.resolution,
                    content_rating=self.content_rating,
                    preview_message="正在生成图片...",
                    success_message="图片生成完成~",
                    model_name_override=self.model_name_override,
                    openai_image_size=self.openai_image_size,
                    openai_response_format=self.openai_response_format,
                    openai_stream=self.openai_stream,
                    openai_quality=self.openai_quality,
                    openai_style=self.openai_style,
                    openai_image_api_mode=self.openai_image_api_mode,
                    channel=channel,
                    user_id=str(interaction.user.id),
                    bot=self.bot,
                    request_user=interaction.user,
                )
                if result and result.get("generation_failed"):
                    raise ValueError(result.get("hint") or "文生图失败。")

            await interaction.followup.send("已提交生成请求，结果会直接发到当前频道。", ephemeral=True)
        except Exception as error:
            log.error("Imagen 面板触发生成失败: %s", error, exc_info=True)
            await interaction.followup.send(f"生成失败：{error}", ephemeral=True)


async def open_imagen_generation_panel(
    *,
    interaction: discord.Interaction,
    bot: commands.Bot,
    mode: str = "image",
    prompt: str = "",
    negative_prompt: str = "",
    aspect_ratio: Optional[str] = None,
    count: int = 1,
    resolution: str = "default",
    content_rating: str = "sfw",
    model_name_override: Optional[str] = None,
    openai_image_size: Optional[str] = None,
    openai_response_format: Optional[str] = None,
    openai_stream: Optional[bool] = None,
    openai_quality: Optional[str] = None,
    openai_style: Optional[str] = None,
    openai_image_api_mode: Optional[str] = None,
    reference_attachments: Optional[List[discord.Attachment]] = None,
) -> ImagenGenerationPanelView:
    view = ImagenGenerationPanelView(
        bot=bot,
        user_id=interaction.user.id,
        prompt=prompt,
        negative_prompt=negative_prompt,
        mode=mode,
        aspect_ratio=aspect_ratio,
        count=count,
        resolution=resolution,
        content_rating=content_rating,
        model_name_override=model_name_override,
        openai_image_size=openai_image_size,
        openai_response_format=openai_response_format,
        openai_stream=openai_stream,
        openai_quality=openai_quality,
        openai_style=openai_style,
        openai_image_api_mode=openai_image_api_mode,
    )

    if reference_attachments:
        await view.load_reference_attachments(reference_attachments)

    await interaction.response.send_message(
        embed=view.build_embed(),
        view=view,
        ephemeral=True,
    )
    view.panel_message = await interaction.original_response()
    return view
