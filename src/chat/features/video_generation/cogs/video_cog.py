# -*- coding: utf-8 -*-

"""
视频生成斜杠命令 Cog

提供：
1) /视频生成（总是弹出视频专用参数面板）
"""

import io
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config import chat_config as app_config
from src.chat.config.chat_config import VIDEO_GEN_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.video_generation.services.video_service import (
    VideoResult,
    video_service,
)

log = logging.getLogger(__name__)


def _video_size_to_ratio_label(size: Optional[str]) -> str:
    """将内部尺寸值转换为用户可读的宽高比。"""
    ratio_map = {
        "1280x720": "16:9",
        "1792x1024": "16:9",
        "720x1280": "9:16",
        "1024x1792": "9:16",
        "1024x1024": "1:1",
    }
    normalized = str(size or "").strip()
    return ratio_map.get(normalized, normalized or "16:9")


def _normalize_duration(value: Optional[int]) -> int:
    min_seconds = 5
    if value is None:
        return min_seconds
    try:
        parsed_value = int(value)
    except (TypeError, ValueError):
        parsed_value = min_seconds
    return min(max(min_seconds, parsed_value), app_config.VIDEO_GEN_MAX_SECONDS)


class VideoPromptModal(discord.ui.Modal, title="设置视频描述"):
    """视频描述输入框"""

    def __init__(self, parent_view: "VideoGenerationPanelView"):
        super().__init__()
        self.parent_view = parent_view

        self.prompt_input = discord.ui.TextInput(
            label="视频描述",
            style=discord.TextStyle.paragraph,
            placeholder="例如：霓虹雨夜街头，慢镜头追拍，镜头缓慢推进",
            required=True,
            max_length=1200,
            default=self.parent_view.prompt,
        )
        self.add_item(self.prompt_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.prompt = str(self.prompt_input.value or "").strip()
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(),
            view=self.parent_view,
        )


class VideoOptionsModal(discord.ui.Modal, title="设置主参数"):
    """视频主参数输入框"""

    def __init__(self, parent_view: "VideoGenerationPanelView"):
        super().__init__()
        self.parent_view = parent_view

        self.duration_input = discord.ui.TextInput(
            label="时长（秒）",
            style=discord.TextStyle.short,
            required=True,
            default=str(self.parent_view.duration),
            placeholder="5 ~ 30",
            max_length=3,
        )
        self.size_input = discord.ui.TextInput(
            label="宽高比",
            style=discord.TextStyle.short,
            required=True,
            default=self.parent_view.size,
            placeholder="16:9 / 9:16 / 1:1（也兼容 1280x720 等旧值）",
            max_length=20,
        )
        self.quality_input = discord.ui.TextInput(
            label="视频质量",
            style=discord.TextStyle.short,
            required=True,
            default=self.parent_view.quality,
            placeholder="standard 或 high",
            max_length=20,
        )
        self.model_input = discord.ui.TextInput(
            label="模型（可留空）",
            style=discord.TextStyle.short,
            required=False,
            default=self.parent_view.model_name,
            placeholder="例如：grok-imagine-1.0-video",
            max_length=120,
        )

        self.add_item(self.duration_input)
        self.add_item(self.size_input)
        self.add_item(self.quality_input)
        self.add_item(self.model_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration = int(float(str(self.duration_input.value).strip()))
        except (TypeError, ValueError):
            await interaction.response.send_message("时长必须是数字。", ephemeral=True)
            return

        size = str(self.size_input.value or "").strip()
        if size not in app_config.VIDEO_GEN_ALLOWED_SIZES:
            allowed_sizes = "、".join(app_config.VIDEO_GEN_ALLOWED_SIZES)
            await interaction.response.send_message(
                f"宽高比不支持，请使用：16:9、9:16、1:1（也兼容：{allowed_sizes}）",
                ephemeral=True,
            )
            return

        quality = str(self.quality_input.value or "").strip().lower()
        if quality not in app_config.VIDEO_GEN_ALLOWED_QUALITIES:
            allowed_qualities = "、".join(app_config.VIDEO_GEN_ALLOWED_QUALITIES)
            await interaction.response.send_message(
                f"视频质量不支持，请使用：{allowed_qualities}",
                ephemeral=True,
            )
            return

        self.parent_view.duration = _normalize_duration(duration)
        self.parent_view.size = size
        self.parent_view.quality = quality
        self.parent_view.model_name = str(self.model_input.value or "").strip()
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(),
            view=self.parent_view,
        )


class VideoReferenceUrlModal(discord.ui.Modal, title="设置参考图 URL"):
    """参考图 URL 输入框"""

    def __init__(self, parent_view: "VideoGenerationPanelView"):
        super().__init__()
        self.parent_view = parent_view

        self.reference_url_input = discord.ui.TextInput(
            label="参考图 URL 或 Data URI",
            style=discord.TextStyle.paragraph,
            required=False,
            default=self.parent_view.reference_image_url,
            placeholder="留空表示不使用 URL 参考图",
            max_length=1200,
        )
        self.add_item(self.reference_url_input)

    async def on_submit(self, interaction: discord.Interaction):
        normalized_url = str(self.reference_url_input.value or "").strip()
        if normalized_url and not normalized_url.startswith(
            ("http://", "https://", "data:")
        ):
            await interaction.response.send_message(
                "参考图只支持 http(s) URL 或 data URI。",
                ephemeral=True,
            )
            return

        self.parent_view.reference_image_url = normalized_url
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(),
            view=self.parent_view,
        )


class VideoGenerationPanelView(discord.ui.View):
    """视频生成专用参数面板"""

    def __init__(
        self,
        cog: "VideoGenerationCog",
        owner_user_id: int,
        *,
        initial_prompt: str = "",
        initial_duration: Optional[int] = None,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_user_id = owner_user_id
        self.panel_message: Optional[discord.Message] = None

        self.prompt = initial_prompt
        self.duration = _normalize_duration(initial_duration)
        self.model_name = ""
        self.size = str(
            VIDEO_GEN_CONFIG.get("DEFAULT_SIZE", app_config.VIDEO_GEN_ALLOWED_SIZES[0])
        ).strip()
        if self.size not in app_config.VIDEO_GEN_ALLOWED_SIZES:
            self.size = app_config.VIDEO_GEN_ALLOWED_SIZES[0]
        self.quality = str(VIDEO_GEN_CONFIG.get("DEFAULT_QUALITY", "high")).strip().lower()
        if self.quality not in app_config.VIDEO_GEN_ALLOWED_QUALITIES:
            self.quality = "high"

        self.image_data = image_data
        self.image_mime_type = image_mime_type
        self.reference_image_url = ""

    @property
    def resolution_label(self) -> str:
        return app_config.VIDEO_GEN_QUALITY_TO_RESOLUTION.get(self.quality, "720p")

    def _reference_status(self) -> str:
        if self.reference_image_url:
            preview_url = self.reference_image_url[:80]
            if len(self.reference_image_url) > 80:
                preview_url += "..."
            return f"🌐 URL 参考图\n{preview_url}"
        if self.image_data:
            mime = self.image_mime_type or "image/png"
            return f"🖼️ 已载入命令附件\n类型：{mime}"
        return "（未设置）\n如需本地图片，请在 `/视频生成` 命令里附带 `图片` 参数。"

    def _mode_label(self) -> str:
        if self.reference_image_url or self.image_data:
            return "🖼️ 图生视频\n会把第 1 张参考图作为视频参考输入"
        return "📝 文生视频\n仅根据描述直接生成视频"

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎬 视频生成面板",
            description="调好参数后点击「开始生成」",
            color=0x2B2D31,
        )
        embed.add_field(name="🎯 模式", value=self._mode_label(), inline=False)

        prompt_preview = self.prompt[:220] if self.prompt else "（未设置）"
        if self.prompt and len(self.prompt) > 220:
            prompt_preview += "..."
        embed.add_field(name="📝 视频描述", value=prompt_preview, inline=False)

        embed.add_field(name="⏱️ 时长", value=f"{self.duration} 秒", inline=True)
        embed.add_field(name="📐 宽高比", value=_video_size_to_ratio_label(self.size), inline=True)
        embed.add_field(
            name="🎞️ 质量",
            value=f"{self.quality}（{self.resolution_label}）",
            inline=True,
        )
        embed.add_field(
            name="🤖 模型",
            value=self.model_name or "自动（按当前模式选择）",
            inline=False,
        )
        embed.add_field(name="🖼️ 参考图", value=self._reference_status(), inline=False)

        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)
        embed.set_footer(
            text=(
                f"每次生成预计消耗 {cost} 灵石 | 默认质量 high=720P | "
                "上游已支持 5~30 秒服务端自动链式扩展"
            )
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_user_id:
            await interaction.response.send_message("这个面板只能由命令发起者使用。", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.panel_message:
            try:
                await self.panel_message.edit(view=self)
            except Exception:
                pass

    @discord.ui.button(label="视频描述", style=discord.ButtonStyle.secondary, emoji="📝", row=0)
    async def edit_prompt(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(VideoPromptModal(self))

    @discord.ui.button(label="主参数", style=discord.ButtonStyle.secondary, emoji="⚙️", row=0)
    async def edit_options(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(VideoOptionsModal(self))

    @discord.ui.button(label="参考图 URL", style=discord.ButtonStyle.secondary, emoji="🌐", row=0)
    async def edit_reference_url(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        await interaction.response.send_modal(VideoReferenceUrlModal(self))

    @discord.ui.button(label="清空参考图", style=discord.ButtonStyle.secondary, emoji="🧹", row=1)
    async def clear_reference(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ):
        self.image_data = None
        self.image_mime_type = None
        self.reference_image_url = ""
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="开始生成", style=discord.ButtonStyle.primary, emoji="🎬", row=1)
    async def do_generate(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self.cog.run_generate_from_panel(interaction, self)

    @discord.ui.button(label="关闭面板", style=discord.ButtonStyle.danger, emoji="✖️", row=1)
    async def close_panel(self, interaction: discord.Interaction, _: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(content="视频面板已关闭。", embed=None, view=self)


class VideoGenerationCog(commands.Cog):
    """视频生成功能模块"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="视频生成", description="打开视频生成专用参数面板")
    @app_commands.describe(
        描述="可选：预填到面板中的视频描述",
        时长="可选：预填到面板中的目标时长（6~30秒）",
        图片="可选：预载到面板中的参考图",
    )
    async def 视频生成(
        self,
        interaction: discord.Interaction,
        描述: Optional[str] = None,
        时长: Optional[int] = None,
        图片: Optional[discord.Attachment] = None,
    ):
        if not video_service.is_available():
            await interaction.response.send_message(
                "视频生成服务当前未启用，请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        await self._open_video_panel(
            interaction,
            initial_prompt=str(描述 or "").strip(),
            initial_duration=时长,
            image=图片,
        )

    async def _open_video_panel(
        self,
        interaction: discord.Interaction,
        *,
        initial_prompt: str = "",
        initial_duration: Optional[int] = None,
        image: Optional[discord.Attachment] = None,
    ):
        image_data: Optional[bytes] = None
        image_mime_type: Optional[str] = None

        if image is not None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.response.send_message(
                    "上传的文件不是图片格式，请上传 PNG/JPG/WEBP 等图片文件。",
                    ephemeral=True,
                )
                return
            try:
                image_data = await image.read()
                image_mime_type = image.content_type
            except Exception as e:
                log.error("读取面板图片附件失败: %s", e)
                await interaction.response.send_message("读取图片失败，请重试。", ephemeral=True)
                return

        view = VideoGenerationPanelView(
            cog=self,
            owner_user_id=interaction.user.id,
            initial_prompt=initial_prompt,
            initial_duration=initial_duration,
            image_data=image_data,
            image_mime_type=image_mime_type,
        )
        await interaction.response.send_message(
            embed=view.build_embed(),
            view=view,
            ephemeral=True,
        )
        try:
            view.panel_message = await interaction.original_response()
        except Exception:
            view.panel_message = None

    async def run_generate_from_panel(
        self,
        interaction: discord.Interaction,
        panel: VideoGenerationPanelView,
    ):
        prompt = panel.prompt.strip()
        if not prompt:
            await interaction.followup.send("请先填写视频描述。", ephemeral=True)
            return

        await self._run_direct_generate(
            interaction=interaction,
            prompt=prompt,
            duration=panel.duration,
            image_data=panel.image_data,
            image_mime_type=panel.image_mime_type,
            model_name=panel.model_name,
            size=panel.size,
            quality=panel.quality,
            reference_image_url=panel.reference_image_url,
        )

    async def _run_direct_generate(
        self,
        *,
        interaction: discord.Interaction,
        prompt: str,
        duration: int = app_config.VIDEO_GEN_MIN_SECONDS,
        image: Optional[discord.Attachment] = None,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
        model_name: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        reference_image_url: Optional[str] = None,
    ):
        user_id = interaction.user.id
        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)

        balance = await coin_service.get_balance(user_id)
        if balance < cost:
            await interaction.followup.send(
                f"你的灵石余额不足！生成视频需要 {cost} 灵石，你当前只有 {balance}。",
                ephemeral=True,
            )
            return

        local_image_data = image_data
        local_image_mime = image_mime_type
        if image is not None and local_image_data is None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.followup.send(
                    "上传的文件不是图片格式，请上传 PNG/JPG/WEBP 等图片文件。",
                    ephemeral=True,
                )
                return
            try:
                local_image_data = await image.read()
                local_image_mime = image.content_type
            except Exception as e:
                log.error("读取图片附件失败: %s", e)
                await interaction.followup.send("读取图片失败，请重试。", ephemeral=True)
                return

        normalized_size = size or VIDEO_GEN_CONFIG.get(
            "DEFAULT_SIZE", app_config.VIDEO_GEN_ALLOWED_SIZES[0]
        )
        normalized_quality = quality or VIDEO_GEN_CONFIG.get("DEFAULT_QUALITY", "high")
        normalized_duration = _normalize_duration(duration)

        result = await video_service.generate_video(
            prompt=prompt,
            duration=normalized_duration,
            image_data=local_image_data,
            image_mime_type=local_image_mime,
            model_override=model_name,
            size=normalized_size,
            quality=normalized_quality,
            reference_image_url=reference_image_url,
        )
        if result is None:
            await interaction.followup.send("视频生成失败了，请稍后再试或调整参数。")
            return

        new_balance = await coin_service.remove_coins(
            user_id=user_id,
            amount=cost,
            reason=f"视频生成: {prompt[:50]}",
        )
        if new_balance is None:
            await interaction.followup.send("灵石扣除失败，余额不足。", ephemeral=True)
            return

        await self._send_video_result(
            interaction=interaction,
            result=result,
            prompt_text=prompt,
            cost=cost,
            new_balance=new_balance,
            title="AI 视频生成",
            footer_extra=(
                f"时长: {normalized_duration}s | 宽高比: {_video_size_to_ratio_label(normalized_size)} | "
                f"质量: {normalized_quality}({app_config.VIDEO_GEN_QUALITY_TO_RESOLUTION.get(normalized_quality, '720p')})"
            ),
            with_regenerate_view=True,
            duration=normalized_duration,
            reference_image_data=local_image_data,
            reference_image_mime_type=local_image_mime,
            reference_image_url=reference_image_url,
            model_name_override=model_name,
            size=normalized_size,
            quality=normalized_quality,
        )

    async def _send_video_result(
        self,
        *,
        interaction: discord.Interaction,
        result: VideoResult,
        prompt_text: str,
        cost: int,
        new_balance: int,
        title: str,
        footer_extra: str,
        with_regenerate_view: bool,
        duration: int = app_config.VIDEO_GEN_MIN_SECONDS,
        reference_image_data: Optional[bytes] = None,
        reference_image_mime_type: Optional[str] = None,
        reference_image_url: Optional[str] = None,
        model_name_override: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
    ):
        embed = discord.Embed(title=title, color=0x2B2D31)
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url
            if interaction.user.display_avatar
            else None,
        )
        embed.add_field(name="视频提示词", value=f"```\n{prompt_text[:1016]}\n```", inline=False)

        explicit_model_name = (
            str(model_name_override).strip() if isinstance(model_name_override, str) else ""
        )
        post_id_text = f" | post_id: {result.post_id}" if result.post_id else ""
        embed.set_footer(
            text=(
                f"消耗 {cost} 灵石 | 余额: {new_balance} | {footer_extra} | "
                f"模型: {explicit_model_name or '自动'}{post_id_text}"
            )
        )

        regenerate_view = None
        if with_regenerate_view:
            from src.chat.features.tools.ui.regenerate_view import SlashCommandRegenerateView

            original_params = {
                "prompt": prompt_text,
                "duration": duration,
                "post_id": result.post_id,
                "size": size or VIDEO_GEN_CONFIG.get(
                    "DEFAULT_SIZE", app_config.VIDEO_GEN_ALLOWED_SIZES[0]
                ),
                "quality": quality or VIDEO_GEN_CONFIG.get("DEFAULT_QUALITY", "high"),
            }
            if explicit_model_name:
                original_params["video_model_name"] = explicit_model_name
            if reference_image_data:
                original_params["reference_image_data"] = reference_image_data
                original_params["reference_image_mime_type"] = (
                    reference_image_mime_type or "image/png"
                )
            if reference_image_url:
                original_params["reference_image_url"] = reference_image_url
            regenerate_view = SlashCommandRegenerateView(
                generation_type="video",
                original_params=original_params,
                user_id=interaction.user.id,
            )

        if result.format_type == "url" and result.url:
            video_file = await self._try_download_video(result.url)
            if video_file:
                await interaction.followup.send(
                    embed=embed,
                    file=video_file,
                    view=regenerate_view,
                )
            else:
                embed.add_field(name="视频链接", value=f"[点击观看]({result.url})", inline=False)
                await interaction.followup.send(embed=embed, view=regenerate_view)
            return

        if result.format_type == "html" and result.html_content:
            html_file = discord.File(
                io.BytesIO(result.html_content.encode("utf-8")),
                filename="video.html",
            )
            files = [html_file]
            if result.url:
                video_file = await self._try_download_video(result.url)
                if video_file:
                    files.append(video_file)
            await interaction.followup.send(embed=embed, files=files, view=regenerate_view)
            return

        if result.text_response:
            embed.add_field(name="响应", value=result.text_response[:1024], inline=False)
            await interaction.followup.send(embed=embed, view=regenerate_view)
            return

        await interaction.followup.send(
            "视频处理完成，但未获取到可发送的视频内容。",
            view=regenerate_view,
        )

    async def _try_download_video(self, url: str) -> discord.File | None:
        """尝试下载视频并以附件发送（限制 25MB）"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=60),
                    headers={"User-Agent": "Mozilla/5.0"},
                ) as response:
                    if response.status != 200:
                        log.warning("视频下载失败，HTTP %s", response.status)
                        return None

                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > 25 * 1024 * 1024:
                        return None

                    data = await response.read()
                    if len(data) > 25 * 1024 * 1024:
                        return None

                    ext = "mp4"
                    content_type = response.headers.get("Content-Type", "")
                    if "webm" in content_type:
                        ext = "webm"
                    elif "mov" in content_type or "quicktime" in content_type:
                        ext = "mov"

                    return discord.File(
                        io.BytesIO(data),
                        filename=f"generated_video.{ext}",
                        spoiler=True,
                    )
        except Exception as e:
            log.warning("下载视频失败: %s", e)
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoGenerationCog(bot))
