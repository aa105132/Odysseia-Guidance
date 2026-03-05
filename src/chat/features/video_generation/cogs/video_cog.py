# -*- coding: utf-8 -*-

"""
视频生成斜杠命令 Cog
提供：
1) /视频生成（可直接生成，也可不填描述直接打开参数面板）
2) /视频面板（打开中文参数面板，支持生成与视频延长）
"""

import io
import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config.chat_config import VIDEO_GEN_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.features.video_generation.services.video_service import VideoResult, video_service

log = logging.getLogger(__name__)


def _parse_bool_text(value: str, default: bool = True) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return default
    if text in {"1", "true", "yes", "y", "是", "开", "开启"}:
        return True
    if text in {"0", "false", "no", "n", "否", "关", "关闭"}:
        return False
    return default


class VideoGenerateParamsModal(discord.ui.Modal, title="填写生成参数"):
    """视频生成参数模态框"""

    def __init__(self, parent_view: "VideoPanelView"):
        super().__init__()
        self.parent_view = parent_view

        self.prompt_input = discord.ui.TextInput(
            label="视频描述",
            style=discord.TextStyle.paragraph,
            placeholder="例如：海边日落，镜头缓慢推进，海浪轻拍沙滩",
            required=True,
            max_length=1200,
            default=self.parent_view.generate_prompt,
        )
        self.duration_input = discord.ui.TextInput(
            label="时长（秒）",
            style=discord.TextStyle.short,
            required=True,
            default=str(self.parent_view.generate_duration),
            max_length=3,
        )
        self.model_input = discord.ui.TextInput(
            label="模型（可留空）",
            style=discord.TextStyle.short,
            required=False,
            default=self.parent_view.model_name,
            placeholder="例如：grok-imagine-1.0-video",
            max_length=120,
        )
        self.aspect_ratio_input = discord.ui.TextInput(
            label="宽高比（可留空）",
            style=discord.TextStyle.short,
            required=False,
            default=self.parent_view.aspect_ratio,
            placeholder="例如：16:9",
            max_length=20,
        )
        self.resolution_input = discord.ui.TextInput(
            label="分辨率（可留空）",
            style=discord.TextStyle.short,
            required=False,
            default=self.parent_view.resolution,
            placeholder="例如：720p",
            max_length=20,
        )

        self.add_item(self.prompt_input)
        self.add_item(self.duration_input)
        self.add_item(self.model_input)
        self.add_item(self.aspect_ratio_input)
        self.add_item(self.resolution_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            duration = int(float(str(self.duration_input.value).strip()))
        except (TypeError, ValueError):
            await interaction.response.send_message("时长必须是数字。", ephemeral=True)
            return

        self.parent_view.generate_prompt = str(self.prompt_input.value).strip()
        self.parent_view.generate_duration = max(1, min(60, duration))
        self.parent_view.model_name = str(self.model_input.value or "").strip()
        self.parent_view.aspect_ratio = str(self.aspect_ratio_input.value or "").strip() or "16:9"
        self.parent_view.resolution = str(self.resolution_input.value or "").strip() or "720p"

        embed = self.parent_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class VideoExtendParamsModal(discord.ui.Modal, title="填写延长参数"):
    """视频延长参数模态框"""

    def __init__(self, parent_view: "VideoPanelView"):
        super().__init__()
        self.parent_view = parent_view

        self.post_id_input = discord.ui.TextInput(
            label="原视频 Post ID",
            style=discord.TextStyle.short,
            required=True,
            default=self.parent_view.extend_post_id,
            placeholder="填要延长的视频 post_id",
            max_length=200,
        )
        self.prompt_input = discord.ui.TextInput(
            label="延长提示词",
            style=discord.TextStyle.paragraph,
            required=True,
            default=self.parent_view.extend_prompt,
            placeholder="例如：让镜头继续向前推进",
            max_length=1200,
        )
        self.video_length_input = discord.ui.TextInput(
            label="延长时长（秒）",
            style=discord.TextStyle.short,
            required=True,
            default=str(self.parent_view.extend_length),
            max_length=3,
        )
        self.start_time_input = discord.ui.TextInput(
            label="延长起始时间（秒，可留空）",
            style=discord.TextStyle.short,
            required=False,
            default=(
                "" if self.parent_view.extend_start_time is None else str(self.parent_view.extend_start_time)
            ),
            max_length=20,
        )
        self.stitch_input = discord.ui.TextInput(
            label="是否拼接（是/否）",
            style=discord.TextStyle.short,
            required=False,
            default="是" if self.parent_view.stitch_with_extend else "否",
            max_length=8,
        )

        self.add_item(self.post_id_input)
        self.add_item(self.prompt_input)
        self.add_item(self.video_length_input)
        self.add_item(self.start_time_input)
        self.add_item(self.stitch_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            video_length = int(float(str(self.video_length_input.value).strip()))
        except (TypeError, ValueError):
            await interaction.response.send_message("延长时长必须是数字。", ephemeral=True)
            return

        start_time_text = str(self.start_time_input.value or "").strip()
        start_time: Optional[float] = None
        if start_time_text:
            try:
                start_time = float(start_time_text)
            except (TypeError, ValueError):
                await interaction.response.send_message("延长起始时间必须是数字。", ephemeral=True)
                return

        self.parent_view.extend_post_id = str(self.post_id_input.value).strip()
        self.parent_view.extend_prompt = str(self.prompt_input.value).strip()
        self.parent_view.extend_length = max(1, min(60, video_length))
        self.parent_view.extend_start_time = start_time
        self.parent_view.stitch_with_extend = _parse_bool_text(self.stitch_input.value, default=True)

        embed = self.parent_view.build_embed()
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class VideoPanelView(discord.ui.View):
    """视频参数面板（中文按钮）"""

    def __init__(
        self,
        cog: "VideoGenerationCog",
        owner_user_id: int,
        *,
        initial_prompt: str = "",
        initial_duration: int = 5,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_user_id = owner_user_id

        # 生成参数
        self.generate_prompt = initial_prompt
        self.generate_duration = max(1, min(60, initial_duration))
        self.model_name = VIDEO_GEN_CONFIG.get("MODEL_NAME", "grok-imagine-1.0-video")
        self.aspect_ratio = "16:9"
        self.resolution = "720p"
        self.stream = False
        self.image_data = image_data
        self.image_mime_type = image_mime_type

        # 延长参数
        self.extend_post_id = ""
        self.extend_prompt = ""
        self.extend_length = 10
        self.extend_start_time: Optional[float] = None
        self.stitch_with_extend = True

    def build_embed(self) -> discord.Embed:
        mode = "图生视频" if self.image_data else "文生视频"
        extend_start_text = (
            "自动衔接"
            if self.extend_start_time is None
            else f"{self.extend_start_time:g}s"
        )

        embed = discord.Embed(
            title="视频生成参数面板",
            description="先点击按钮填写参数，再执行“开始生成”或“开始延长”。",
            color=0x2b2d31,
        )
        embed.add_field(
            name="生成参数",
            value=(
                f"模式：{mode}\n"
                f"描述：{(self.generate_prompt[:90] + '...') if len(self.generate_prompt) > 90 else (self.generate_prompt or '未填写')}\n"
                f"时长：{self.generate_duration}s\n"
                f"模型：{self.model_name or '默认'}\n"
                f"宽高比：{self.aspect_ratio}\n"
                f"分辨率：{self.resolution}"
            ),
            inline=False,
        )
        embed.add_field(
            name="延长参数",
            value=(
                f"Post ID：{self.extend_post_id or '未填写'}\n"
                f"提示词：{(self.extend_prompt[:90] + '...') if len(self.extend_prompt) > 90 else (self.extend_prompt or '未填写')}\n"
                f"延长时长：{self.extend_length}s\n"
                f"起始时间：{extend_start_text}\n"
                f"拼接：{'是' if self.stitch_with_extend else '否'}"
            ),
            inline=False,
        )
        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)
        embed.set_footer(text=f"每次操作预计消耗 {cost} 月光币")
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

    @discord.ui.button(label="填写生成参数", style=discord.ButtonStyle.secondary, row=0)
    async def edit_generate_params(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(VideoGenerateParamsModal(self))

    @discord.ui.button(label="填写延长参数", style=discord.ButtonStyle.secondary, row=0)
    async def edit_extend_params(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.send_modal(VideoExtendParamsModal(self))

    @discord.ui.button(label="开始生成", style=discord.ButtonStyle.primary, row=1)
    async def do_generate(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self.cog.run_generate_from_panel(interaction, self)

    @discord.ui.button(label="开始延长", style=discord.ButtonStyle.success, row=1)
    async def do_extend(self, interaction: discord.Interaction, _: discord.ui.Button):
        await interaction.response.defer(thinking=True)
        await self.cog.run_extend_from_panel(interaction, self)

    @discord.ui.button(label="关闭面板", style=discord.ButtonStyle.danger, row=1)
    async def close_panel(self, interaction: discord.Interaction, _: discord.ui.Button):
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(content="视频面板已关闭。", embed=None, view=self)


class VideoGenerationCog(commands.Cog):
    """视频生成功能模块"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="视频生成", description="AI 视频生成（可直接生成，也可打开参数面板）")
    @app_commands.describe(
        描述="可选：留空将打开参数面板",
        时长="视频时长（秒，默认5秒）",
        图片="参考图片（可选，上传后将以图生视频模式生成）",
    )
    async def 视频生成(
        self,
        interaction: discord.Interaction,
        描述: Optional[str] = None,
        时长: int = 5,
        图片: Optional[discord.Attachment] = None,
    ):
        if not video_service.is_available():
            await interaction.response.send_message(
                "视频生成服务当前未启用，请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return

        # 未填描述时，打开参数面板
        if not (描述 or "").strip():
            await self._open_video_panel(interaction, initial_prompt="", initial_duration=时长, image=图片)
            return

        await interaction.response.defer(thinking=True)
        await self._run_direct_generate(
            interaction=interaction,
            prompt=str(描述).strip(),
            duration=时长,
            image=图片,
        )

    @app_commands.command(name="视频面板", description="打开视频生成参数面板（支持视频延长）")
    async def 视频面板(self, interaction: discord.Interaction):
        if not video_service.is_available():
            await interaction.response.send_message(
                "视频生成服务当前未启用，请联系管理员在 Dashboard 中配置。",
                ephemeral=True,
            )
            return
        await self._open_video_panel(interaction)

    async def _open_video_panel(
        self,
        interaction: discord.Interaction,
        *,
        initial_prompt: str = "",
        initial_duration: int = 5,
        image: Optional[discord.Attachment] = None,
    ):
        image_data: Optional[bytes] = None
        image_mime_type: Optional[str] = None
        if image is not None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.response.send_message("上传的文件不是图片格式，请上传 PNG/JPG/WEBP 等图片文件。", ephemeral=True)
                return
            try:
                image_data = await image.read()
                image_mime_type = image.content_type
            except Exception as e:
                log.error(f"读取面板图片附件失败: {e}")
                await interaction.response.send_message("读取图片失败，请重试。", ephemeral=True)
                return

        view = VideoPanelView(
            cog=self,
            owner_user_id=interaction.user.id,
            initial_prompt=initial_prompt,
            initial_duration=initial_duration,
            image_data=image_data,
            image_mime_type=image_mime_type,
        )
        await interaction.response.send_message(embed=view.build_embed(), view=view)

    async def run_generate_from_panel(self, interaction: discord.Interaction, panel: VideoPanelView):
        prompt = panel.generate_prompt.strip()
        if not prompt:
            await interaction.followup.send("请先填写“生成参数”里的视频描述。", ephemeral=True)
            return

        await self._run_direct_generate(
            interaction=interaction,
            prompt=prompt,
            duration=panel.generate_duration,
            image_data=panel.image_data,
            image_mime_type=panel.image_mime_type,
            model_name=panel.model_name,
        )

    async def run_extend_from_panel(self, interaction: discord.Interaction, panel: VideoPanelView):
        post_id = panel.extend_post_id.strip()
        extend_prompt = panel.extend_prompt.strip()
        if not post_id:
            await interaction.followup.send("请先填写“延长参数”里的 Post ID。", ephemeral=True)
            return
        if not extend_prompt:
            await interaction.followup.send("请先填写“延长参数”里的延长提示词。", ephemeral=True)
            return

        user_id = interaction.user.id
        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)
        balance = await coin_service.get_balance(user_id)
        if balance < cost:
            await interaction.followup.send(
                f"你的月光币余额不足！视频延长需要 {cost} 月光币，你当前只有 {balance}。",
                ephemeral=True,
            )
            return

        result = await video_service.extend_video(
            post_id=post_id,
            prompt=extend_prompt,
            video_length=panel.extend_length,
            model=panel.model_name,
            aspect_ratio=panel.aspect_ratio,
            resolution=panel.resolution,
            stream=panel.stream,
            video_extension_start_time=panel.extend_start_time,
            stitch_with_extend=panel.stitch_with_extend,
        )
        if result is None:
            await interaction.followup.send("视频延长失败了，请稍后再试或调整参数。")
            return

        new_balance = await coin_service.remove_coins(
            user_id=user_id,
            amount=cost,
            reason=f"视频延长: {extend_prompt[:50]}",
        )
        if new_balance is None:
            await interaction.followup.send("月光币扣除失败，余额不足。", ephemeral=True)
            return

        await self._send_video_result(
            interaction=interaction,
            result=result,
            prompt_text=extend_prompt,
            cost=cost,
            new_balance=new_balance,
            title="AI 视频延长",
            footer_extra=f"延长时长: {panel.extend_length}s | Post ID: {post_id[:40]}",
            with_regenerate_view=False,
        )

    async def _run_direct_generate(
        self,
        *,
        interaction: discord.Interaction,
        prompt: str,
        duration: int = 5,
        image: Optional[discord.Attachment] = None,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
        user_id = interaction.user.id
        cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)

        balance = await coin_service.get_balance(user_id)
        if balance < cost:
            await interaction.followup.send(
                f"你的月光币余额不足！生成视频需要 {cost} 月光币，你当前只有 {balance}。",
                ephemeral=True,
            )
            return

        local_image_data = image_data
        local_image_mime = image_mime_type
        if image is not None and local_image_data is None:
            if not image.content_type or not image.content_type.startswith("image/"):
                await interaction.followup.send("上传的文件不是图片格式，请上传 PNG/JPG/WEBP 等图片文件。", ephemeral=True)
                return
            try:
                local_image_data = await image.read()
                local_image_mime = image.content_type
            except Exception as e:
                log.error(f"读取图片附件失败: {e}")
                await interaction.followup.send("读取图片失败，请重试。", ephemeral=True)
                return

        result = await video_service.generate_video(
            prompt=prompt,
            duration=duration,
            image_data=local_image_data,
            image_mime_type=local_image_mime,
            model_override=model_name,
        )
        if result is None:
            await interaction.followup.send("视频生成失败了...请稍后再试或更换描述词。")
            return

        new_balance = await coin_service.remove_coins(
            user_id=user_id,
            amount=cost,
            reason=f"视频生成: {prompt[:50]}",
        )
        if new_balance is None:
            await interaction.followup.send("月光币扣除失败，余额不足。", ephemeral=True)
            return

        await self._send_video_result(
            interaction=interaction,
            result=result,
            prompt_text=prompt,
            cost=cost,
            new_balance=new_balance,
            title="AI 视频生成",
            footer_extra=f"时长: ~{max(1, min(60, int(duration)))}s",
            with_regenerate_view=True,
            duration=duration,
            reference_image_data=local_image_data,
            reference_image_mime_type=local_image_mime,
            model_name_override=model_name,
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
        duration: int = 5,
        reference_image_data: Optional[bytes] = None,
        reference_image_mime_type: Optional[str] = None,
        model_name_override: Optional[str] = None,
    ):
        embed = discord.Embed(title=title, color=0x2b2d31)
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        embed.add_field(name="视频提示词", value=f"```\n{prompt_text[:1016]}\n```", inline=False)

        model_name = model_name_override or VIDEO_GEN_CONFIG.get("MODEL_NAME", "unknown")
        post_id_text = f" | post_id: {result.post_id}" if result.post_id else ""
        embed.set_footer(
            text=f"消耗 {cost} 月光币 | 余额: {new_balance} | {footer_extra} | 模型: {model_name}{post_id_text}"
        )

        regenerate_view = None
        if with_regenerate_view:
            from src.chat.features.tools.ui.regenerate_view import SlashCommandRegenerateView

            original_params = {
                "prompt": prompt_text,
                "duration": duration,
                "post_id": result.post_id,
                "video_model_name": model_name,
                "aspect_ratio": "16:9",
                "resolution": "720p",
                "stream": False,
            }
            if reference_image_data:
                original_params["reference_image_data"] = reference_image_data
                original_params["reference_image_mime_type"] = reference_image_mime_type or "image/png"
            regenerate_view = SlashCommandRegenerateView(
                generation_type="video",
                original_params=original_params,
                user_id=interaction.user.id,
            )

        if result.format_type == "url" and result.url:
            video_file = await self._try_download_video(result.url)
            if video_file:
                await interaction.followup.send(embed=embed, file=video_file, view=regenerate_view)
            else:
                embed.add_field(name="视频链接", value=f"[点击观看]({result.url})", inline=False)
                await interaction.followup.send(embed=embed, view=regenerate_view)
            return

        if result.format_type == "html" and result.html_content:
            html_file = discord.File(io.BytesIO(result.html_content.encode("utf-8")), filename="video.html")
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

        await interaction.followup.send("视频处理完成，但未获取到可发送的视频内容。", view=regenerate_view)

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
                        log.warning(f"视频下载失败，HTTP {response.status}")
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
            log.warning(f"下载视频失败: {e}")
            return None


async def setup(bot: commands.Bot):
    await bot.add_cog(VideoGenerationCog(bot))
