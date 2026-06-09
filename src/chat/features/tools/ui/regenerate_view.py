# -*- coding: utf-8 -*-

"""
重新生成交互组件
为图片/视频生成提供"重新生成"、"修改提示词重新生成"和"更换模型"功能
支持对话工具调用和斜杠命令两种场景
"""

import logging
import io
import discord
from typing import Optional, Dict, Any, Callable, Awaitable, List

from src.chat.features.image_generation.utils.spoiler_policy import (
    should_spoiler_image,
)
log = logging.getLogger(__name__)

OPENAI_IMAGE_PARAM_KEYS = (
    "model_name_override",
    "openai_image_size",
    "openai_response_format",
    "openai_stream",
    "openai_quality",
    "openai_style",
    "openai_image_api_mode",
)


def _build_model_options(generation_type: str, current_resolution: str = "default", current_rating: str = "sfw") -> List[discord.SelectOption]:
    """
    构建模型选项列表，根据生成类型返回可用的分辨率+内容分级组合。

    对于图片生成（image / edit_image）：返回 分辨率 × 内容分级 的组合
    对于视频生成（video）：视频目前没有分辨率和内容分级选项，不显示下拉菜单
    """
    if generation_type in ("image", "edit_image"):
        options = []
        combinations = [
            ("default", "sfw", "标准 | SFW"),
            ("default", "nsfw", "标准 | NSFW"),
            ("2k", "sfw", "2K 高清 | SFW"),
            ("2k", "nsfw", "2K 高清 | NSFW"),
            ("4k", "sfw", "4K 超清 | SFW"),
            ("4k", "nsfw", "4K 超清 | NSFW"),
        ]
        for resolution, rating, label in combinations:
            value = f"{resolution}|{rating}"
            is_default = (resolution == current_resolution and rating == current_rating)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    default=is_default,
                    description=f"分辨率: {resolution.upper()}, 内容: {rating.upper()}"
                )
            )
        return options
    return []


class EditPromptModal(discord.ui.Modal):
    """修改提示词的模态框"""

    def __init__(self, current_prompt: str, regenerate_callback: Callable[..., Awaitable]):
        super().__init__(title="修改提示词重新生成")
        self.regenerate_callback = regenerate_callback

        self.prompt_input = discord.ui.TextInput(
            label="提示词",
            style=discord.TextStyle.paragraph,
            placeholder="输入新的提示词...",
            default=current_prompt,
            max_length=2000,
            required=True,
        )
        self.add_item(self.prompt_input)

    async def on_submit(self, interaction: discord.Interaction):
        """提交修改后的提示词"""
        new_prompt = self.prompt_input.value.strip()
        if not new_prompt:
            await interaction.response.send_message("提示词不能为空哦！", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            await self.regenerate_callback(
                interaction=interaction,
                new_prompt=new_prompt,
            )
        except Exception as e:
            log.error(f"修改提示词重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("重新生成失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass


class ExtendVideoModal(discord.ui.Modal):
    """延长视频时补充续写要求。"""

    def __init__(self, extend_callback: Callable[..., Awaitable]):
        super().__init__(title="延长视频")
        self.extend_callback = extend_callback

        self.idea_input = discord.ui.TextInput(
            label="续写要求（可选）",
            style=discord.TextStyle.paragraph,
            placeholder="例如：继续向前推进镜头，角色转身挥手；留空则按原提示词自然延续",
            max_length=1000,
            required=False,
        )
        self.add_item(self.idea_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            await self.extend_callback(
                interaction=interaction,
                user_idea=(self.idea_input.value or "").strip(),
            )
        except Exception as e:
            log.error(f"延长视频失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("延长视频失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass


def _aspect_ratio_to_video_size(aspect_ratio: Optional[str], fallback_size: str = "1280x720") -> str:
    """把 Imagen 宽高比转换成视频内部 size。"""
    ratio = str(aspect_ratio or "").strip()
    return {
        "16:9": "1280x720",
        "9:16": "720x1280",
        "1:1": "1024x1024",
        "4:3": "1280x720",
        "3:4": "720x1280",
    }.get(ratio, fallback_size)


async def _download_video_bytes(video_url: str) -> Optional[bytes]:
    """下载视频 URL，供提取尾帧。"""
    normalized_url = str(video_url or "").strip()
    if not normalized_url.startswith(("http://", "https://")):
        return None
    try:
        import aiohttp

        async with aiohttp.ClientSession() as session:
            async with session.get(
                normalized_url,
                timeout=aiohttp.ClientTimeout(total=180),
            ) as response:
                if response.status != 200:
                    log.warning("下载待延长视频失败，状态码: %s", response.status)
                    return None
                return await response.read()
    except Exception as e:
        log.warning("下载待延长视频异常: %s", e)
        return None


def _video_tail_frame_to_png(video_bytes: bytes, mime_type: str = "video/mp4") -> bytes:
    """从视频 bytes 提取尾帧并转为 PNG bytes。"""
    from src.chat.utils.image_utils import extract_video_tail_frame_for_ai

    tail_frame, _ = extract_video_tail_frame_for_ai(
        video_bytes=video_bytes,
        mime_type=mime_type,
    )
    output = io.BytesIO()
    tail_frame.save(output, format="PNG")
    return output.getvalue()


def _build_extend_video_prompt(base_prompt: str, user_idea: str, duration: int) -> str:
    """构建基于上一段尾帧继续延长的视频提示词。"""
    normalized_base = str(base_prompt or "").strip()
    normalized_idea = str(user_idea or "").strip()
    safe_duration = max(5, int(duration or 6))
    midpoint = max(2, min(safe_duration - 1, safe_duration // 2))

    prompt = (
        "基于上一段视频尾帧继续生成下一段视频：把尾帧作为新片段第 0 秒的连续起点，"
        "保持主体身份、服装、场景、光影、构图方向和画风一致，不要突然换人、换场景或跳镜。"
        f"0-{midpoint}秒，延续上一段结尾的动作惯性，镜头平滑推进或轻微跟随，发丝、衣摆、环境光影自然运动；"
        f"{midpoint}-{safe_duration}秒，让动作进一步展开并自然收束，画面保持连续稳定。"
    )
    if normalized_base:
        prompt += f"原视频分镜意图：{normalized_base}"
    if normalized_idea:
        prompt += f" 本次续写补充要求：{normalized_idea}"
    prompt += " 不要文字，不要水印，不要闪烁，不要变脸，不要肢体畸变，不要背景乱变。"
    return prompt


class RegenerateView(discord.ui.View):
    """
    重新生成交互视图（对话工具调用版本）

    提供：
    1. 重新生成按钮 - 使用相同参数重新生成
    2. 修改提示词按钮 - 弹出模态框修改提示词后重新生成
    3. 切换到 NovelAI 按钮 - 使用 NovelAI 引擎重新生成（仅图片类型）
    4. 更换模型下拉菜单 - 切换分辨率和内容分级后重新生成（仅图片类型）
    """

    def __init__(
        self,
        generation_type: str,  # "image", "edit_image", "video"
        original_params: Dict[str, Any],
        user_id: int,
        timeout: float = 300,  # 5分钟超时
    ):
        super().__init__(timeout=timeout)
        self.generation_type = generation_type
        self.original_params = original_params
        self.user_id = user_id

        # 为图片类型添加"切换到 NovelAI"按钮
        if generation_type in ("image", "edit_image"):
            novelai_button = discord.ui.Button(
                label="切换到 NovelAI",
                style=discord.ButtonStyle.success,
                row=0,
            )
            novelai_button.callback = self._switch_to_novelai
            self.add_item(novelai_button)

            generate_video_button = discord.ui.Button(
                label="生成视频",
                style=discord.ButtonStyle.secondary,
                emoji="🎬",
                row=0,
            )
            generate_video_button.callback = self._generate_video_from_image
            self.add_item(generate_video_button)

        if generation_type == "video":
            extend_video_button = discord.ui.Button(
                label="延长视频",
                style=discord.ButtonStyle.secondary,
                emoji="⏩",
                row=0,
            )
            extend_video_button.callback = self._show_extend_video_modal
            self.add_item(extend_video_button)

        # 为图片类型添加模型选择下拉菜单
        if generation_type in ("image", "edit_image"):
            current_resolution = original_params.get("resolution", "default")
            current_rating = original_params.get("content_rating", "sfw")
            model_options = _build_model_options(generation_type, current_resolution, current_rating)
            if model_options:
                self.model_select = discord.ui.Select(
                    placeholder="更换模型重新生成",
                    options=model_options,
                    min_values=1,
                    max_values=1,
                    row=1,
                )
                self.model_select.callback = self._on_model_select
                self.add_item(self.model_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """任何用户都可以使用按钮"""
        return True

    async def on_timeout(self):
        """超时后禁用所有按钮"""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    @discord.ui.button(
        label="重新生成",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        row=0,
    )
    async def regenerate_same(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用相同参数重新生成"""
        await interaction.response.defer()

        try:
            await self._do_regenerate(
                interaction=interaction,
                new_prompt=None,
            )
        except Exception as e:
            log.error(f"重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("重新生成失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(
        label="修改提示词",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        row=0,
    )
    async def regenerate_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        """弹出模态框修改提示词"""
        current_prompt = self.original_params.get("prompt", "")
        modal = EditPromptModal(
            current_prompt=current_prompt,
            regenerate_callback=self._do_regenerate,
        )
        await interaction.response.send_modal(modal)

    async def _on_model_select(self, interaction: discord.Interaction):
        """处理模型选择下拉菜单的回调"""
        await interaction.response.defer()

        selected_value = self.model_select.values[0]  # 格式: "resolution|rating"
        resolution, rating = selected_value.split("|")

        try:
            await self._do_regenerate(
                interaction=interaction,
                new_prompt=None,
                override_resolution=resolution,
                override_rating=rating,
            )
        except Exception as e:
            log.error(f"更换模型重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("重新生成失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass

    async def _switch_to_novelai(self, interaction: discord.Interaction):
        """切换到 NovelAI 引擎生成图片"""
        await interaction.response.defer()
        try:
            await _do_novelai_regenerate(
                interaction=interaction,
                prompt=self.original_params.get("prompt", ""),
                user_id=interaction.user.id,
            )
        except Exception as e:
            log.error(f"切换到 NovelAI 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换到 NovelAI 失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    async def _generate_video_from_image(self, interaction: discord.Interaction):
        """从当前 Imagen 图片结果生成视频。"""
        try:
            from src.chat.features.tools.functions.generate_image_novelai import (
                GenerateVideoModal,
            )

            modal = GenerateVideoModal(
                image_prompt=self.original_params.get("prompt", ""),
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            log.error(f"打开生成视频弹窗失败: {e}", exc_info=True)
            try:
                await interaction.response.send_message("打开生成视频面板失败，请稍后再试。", ephemeral=True)
            except Exception:
                pass

    async def _show_extend_video_modal(self, interaction: discord.Interaction):
        """弹出延长视频补充要求输入框。"""
        modal = ExtendVideoModal(self._extend_video_from_tail_frame)
        await interaction.response.send_modal(modal)

    async def _extend_video_from_tail_frame(
        self,
        interaction: discord.Interaction,
        user_idea: str = "",
    ):
        """提取当前视频尾帧，并基于尾帧续写下一段视频。"""
        channel = interaction.channel
        if not channel:
            await interaction.followup.send("当前频道不可用，无法延长视频。", ephemeral=True)
            return

        video_bytes = self.original_params.get("video_data")
        video_mime_type = self.original_params.get("video_mime_type", "video/mp4")
        if not video_bytes:
            video_url = self.original_params.get("video_url") or self.original_params.get("result_url")
            video_bytes = await _download_video_bytes(str(video_url or ""))

        if not video_bytes and isinstance(interaction.message, discord.Message):
            for attachment in getattr(interaction.message, "attachments", []) or []:
                content_type = str(getattr(attachment, "content_type", "") or "").lower()
                filename = str(getattr(attachment, "filename", "") or "").lower()
                if content_type.startswith("video/") or filename.endswith((".mp4", ".webm", ".mov")):
                    try:
                        video_bytes = await attachment.read()
                        video_mime_type = content_type or "video/mp4"
                        break
                    except Exception as e:
                        log.warning(f"读取消息视频附件失败: {e}")

        if not video_bytes:
            await interaction.followup.send("没有拿到原视频文件或链接，暂时不能提取尾帧延长。", ephemeral=True)
            return

        try:
            tail_frame_png = _video_tail_frame_to_png(video_bytes, video_mime_type)
        except Exception as e:
            log.error(f"提取视频尾帧失败: {e}", exc_info=True)
            await interaction.followup.send("提取视频尾帧失败，暂时不能延长这段视频。", ephemeral=True)
            return

        from src.chat.features.tools.functions.generate_video import generate_video

        duration = int(self.original_params.get("duration", 6) or 6)
        base_prompt = self.original_params.get("base_prompt") or self.original_params.get("prompt", "")
        continuation_prompt = _build_extend_video_prompt(
            base_prompt=base_prompt,
            user_idea=user_idea,
            duration=duration,
        )

        result = await generate_video(
            prompt=continuation_prompt,
            duration=duration,
            use_reference_image=True,
            size=self.original_params.get("size", "1280x720"),
            quality=self.original_params.get("quality", "high"),
            model=self.original_params.get("model") or self.original_params.get("video_model_name"),
            generate_audio=self.original_params.get("generate_audio", True),
            prepare_video_first_frame=False,
            preview_message="正在提取上一段尾帧并继续延长视频...",
            success_message="延长片段生成完成，可以继续点击“延长视频”接着往后接。",
            channel=channel,
            user_id=str(interaction.user.id),
            bot=interaction.client if hasattr(interaction, "client") else None,
            request_user=interaction.user,
            user_message="用上一段视频尾帧作为续写起点延长视频",
            _prepared_reference_image={
                "data": tail_frame_png,
                "mime_type": "image/png",
                "filename": "video_tail_frame.png",
            },
        )

        if isinstance(result, dict) and result.get("success"):
            await interaction.followup.send("已基于尾帧继续生成下一段视频。", ephemeral=True)
            return

        hint = str((result or {}).get("hint") or "").strip() if isinstance(result, dict) else ""
        await interaction.followup.send(
            f"延长视频失败：{hint or '服务暂时不可用或本次请求未成功，请稍后重试。'}",
            ephemeral=True,
        )

    async def _do_regenerate(
        self,
        interaction: discord.Interaction,
        new_prompt: Optional[str] = None,
        override_resolution: Optional[str] = None,
        override_rating: Optional[str] = None,
    ):
        """
        执行重新生成逻辑
        """
        channel = interaction.channel
        if not channel:
            return

        prompt = new_prompt if new_prompt else self.original_params.get("prompt", "")
        # 使用点击者的用户ID进行扣费
        clicker_user_id = interaction.user.id

        # 应用模型覆盖
        resolution = override_resolution or self.original_params.get("resolution", "default")
        content_rating = override_rating or self.original_params.get("content_rating", "sfw")

        if self.generation_type == "image":
            await self._regenerate_image(channel, interaction, prompt, clicker_user_id, resolution, content_rating)
        elif self.generation_type == "edit_image":
            await self._regenerate_edit_image(channel, interaction, prompt, clicker_user_id, resolution, content_rating)
        elif self.generation_type == "video":
            await self._regenerate_video(channel, interaction, prompt, clicker_user_id)

    async def _regenerate_image(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
        resolution: str = "default",
        content_rating: str = "sfw",
    ):
        """重新生成图片"""
        from src.chat.features.tools.functions.generate_image import generate_image

        params = self.original_params.copy()
        params["prompt"] = prompt
        params["channel"] = channel
        params["user_id"] = str(clicker_user_id)
        params["resolution"] = resolution
        params["content_rating"] = content_rating
        params["preview_message"] = "正在重新生成图片..."
        params["success_message"] = params.get("original_success_message", "重新生成完成~")

        # 获取 bot 实例
        if hasattr(interaction, "client"):
            params["bot"] = interaction.client
        params["request_user"] = interaction.user
        for key in OPENAI_IMAGE_PARAM_KEYS:
            if key in self.original_params:
                params[key] = self.original_params.get(key)

        # 不传入 message（因为这是按钮交互，不是原始消息）
        params.pop("message", None)
        params.pop("original_success_message", None)

        result = await generate_image(**params)

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass

    async def _regenerate_edit_image(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
        resolution: str = "default",
        content_rating: str = "sfw",
    ):
        """重新生成图生图(使用保存的参考图片数据)"""
        from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service

        # 检查是否有保存的参考图片数据（兼容单图与多图）
        reference_image_data = self.original_params.get("reference_image_data")
        reference_image_mime_type = self.original_params.get("reference_image_mime_type")
        reference_images_data = self.original_params.get("reference_images_data") or []
        reference_images_mime_types = self.original_params.get("reference_images_mime_types") or []

        normalized_reference_images: List[Dict[str, Any]] = []
        if isinstance(reference_images_data, list):
            for idx, image_bytes in enumerate(reference_images_data):
                if not image_bytes:
                    continue
                mime_type = (
                    reference_images_mime_types[idx]
                    if idx < len(reference_images_mime_types) and reference_images_mime_types[idx]
                    else "image/png"
                )
                normalized_reference_images.append(
                    {"data": image_bytes, "mime_type": mime_type}
                )

        if not normalized_reference_images and reference_image_data:
            normalized_reference_images.append(
                {
                    "data": reference_image_data,
                    "mime_type": reference_image_mime_type or "image/png",
                }
            )

        result = None
        if not normalized_reference_images:
            # 如果没有保存参考图片,回退到普通图片生成
            from src.chat.features.tools.functions.generate_image import generate_image
            params = {
                "prompt": prompt,
                "aspect_ratio": self.original_params.get("aspect_ratio", "1:1"),
                "number_of_images": 1,
                "resolution": resolution,
                "content_rating": content_rating,
                "preview_message": "正在重新生成图片...",
                "success_message": self.original_params.get("original_success_message", "重新生成完成~"),
                "channel": channel,
                "user_id": str(clicker_user_id),
                "bot": interaction.client if hasattr(interaction, "client") else None,
                "request_user": interaction.user,
            }
            for key in OPENAI_IMAGE_PARAM_KEYS:
                if key in self.original_params:
                    params[key] = self.original_params.get(key)
            result = await generate_image(**params)
        else:
            # 使用保存的参考图片进行图生图
            try:
                import io
                from src.chat.features.odysseia_coin.service.coin_service import coin_service
                from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
                from src.chat.utils.prompt_utils import replace_emojis
                from src.chat.utils.database import chat_db_manager

                cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)

                # 检查绘图封禁
                ban_status = await chat_db_manager.get_image_generation_ban_status(clicker_user_id)
                if ban_status.get("is_banned"):
                    remaining_text = ban_status.get("remaining_text", "未知时长")
                    await interaction.followup.send(
                        f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
                        ephemeral=True,
                    )
                    return

                # 检查余额
                balance = await coin_service.get_balance(clicker_user_id)
                if balance < cost:
                    await interaction.followup.send(
                        f"灵石不足哦~需要 {cost} 个,你只有 {balance} 个呢。",
                        ephemeral=True
                    )
                    return

                # 发送预告消息
                preview_msg = await channel.send("正在重新生成图片...")

                # 调用图生图服务
                aspect_ratio = self.original_params.get("aspect_ratio", "1:1")
                edited_image_bytes = await gemini_imagen_service.edit_image(
                    reference_image=normalized_reference_images[0]["data"],
                    edit_prompt=prompt,
                    reference_mime_type=normalized_reference_images[0].get("mime_type", "image/png"),
                    reference_images=normalized_reference_images,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating=content_rating,
                    model_name_override=self.original_params.get("model_name_override"),
                    openai_image_size=self.original_params.get("openai_image_size"),
                    openai_response_format=self.original_params.get("openai_response_format"),
                    openai_stream=self.original_params.get("openai_stream"),
                    openai_quality=self.original_params.get("openai_quality"),
                    openai_style=self.original_params.get("openai_style"),
                    openai_image_api_mode=self.original_params.get("openai_image_api_mode"),
                )

                if edited_image_bytes:
                    # 扣除灵石
                    await coin_service.remove_coins(
                        clicker_user_id, cost, f"AI图生图重新生成: {prompt[:30]}..."
                    )

                    # 获取模型名称
                    edit_model_name = gemini_imagen_service._get_model_for_resolution(
                        resolution=resolution, is_edit=True, content_rating=content_rating
                    )

                    # 构建 Embed
                    embed = discord.Embed(title="AI 图生图", color=0x2b2d31)
                    embed.set_author(
                        name=interaction.user.display_name,
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    embed.add_field(
                        name="编辑提示词",
                        value=f"```\n{prompt[:1016]}\n```",
                        inline=False,
                    )
                    success_message = self.original_params.get("original_success_message", "重新生成完成~")
                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.add_field(name="\u200b", value=processed_success[:1024], inline=False)
                    embed.set_footer(text=f"模型: {edit_model_name}")

                    # 创建新的重新生成视图
                    new_view = RegenerateView(
                        generation_type="edit_image",
                        original_params=self.original_params.copy(),
                        user_id=clicker_user_id,
                    )
                    # 更新提示词
                    new_view.original_params["prompt"] = prompt
                    new_view.original_params["resolution"] = resolution
                    new_view.original_params["content_rating"] = content_rating

                    file = discord.File(
                        io.BytesIO(edited_image_bytes),
                        filename="edited_image.png",
                        spoiler=should_spoiler_image(content_rating),
                    )
                    sent_message = await channel.send(embed=embed, file=file, view=new_view)
                    if sent_message:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=clicker_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )
                else:
                    await interaction.followup.send("图片生成失败了，请稍后再试...", ephemeral=True)

            except Exception as e:
                log.error(f"图生图重新生成失败: {e}", exc_info=True)
                await interaction.followup.send("生成失败了，请稍后再试...", ephemeral=True)
                return

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass

    async def _regenerate_video(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
    ):
        """重新生成视频(保留原始参考图片)"""
        from src.chat.features.tools.functions.generate_video import generate_video

        params = self.original_params.copy()
        params["prompt"] = prompt
        params["channel"] = channel
        params["user_id"] = str(clicker_user_id)
        params["preview_message"] = "正在重新生成视频..."
        params["success_message"] = params.get("original_success_message", "重新生成完成~")

        if hasattr(interaction, "client"):
            params["bot"] = interaction.client
        params["request_user"] = interaction.user

        params.pop("message", None)
        params.pop("original_success_message", None)

        # 如果有保存的参考图片数据,使用图生视频模式（兼容单图与多图）
        reference_image_data = params.get("reference_image_data")
        reference_image_mime_type = params.get("reference_image_mime_type")
        reference_images_data = params.get("reference_images_data") or []
        reference_images_mime_types = params.get("reference_images_mime_types") or []

        normalized_reference_images: List[Dict[str, Any]] = []
        if isinstance(reference_images_data, list):
            for idx, image_bytes in enumerate(reference_images_data):
                if not image_bytes:
                    continue
                mime_type = (
                    reference_images_mime_types[idx]
                    if idx < len(reference_images_mime_types) and reference_images_mime_types[idx]
                    else "image/png"
                )
                normalized_reference_images.append(
                    {"data": image_bytes, "mime_type": mime_type}
                )

        if not normalized_reference_images and reference_image_data:
            normalized_reference_images.append(
                {
                    "data": reference_image_data,
                    "mime_type": reference_image_mime_type or "image/png",
                }
            )

        if normalized_reference_images:
            params["use_reference_image"] = True
            params["_prepared_reference_images"] = normalized_reference_images
            params["_prepared_reference_image"] = normalized_reference_images[0]
        else:
            # 文生视频模式
            params["use_reference_image"] = False

        result = await generate_video(**params)

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass


class SlashCommandRegenerateView(discord.ui.View):
    """
    斜杠命令重新生成交互视图

    用于 /画图、/图生图、/video 等斜杠命令的结果消息
    重新生成时直接在当前频道调用对应的工具函数

    提供：
    1. 重新生成按钮
    2. 修改提示词按钮
    3. 切换到 NovelAI 按钮（仅图片类型）
    4. 更换模型下拉菜单（仅图片类型）
    """

    def __init__(
        self,
        generation_type: str,  # "image", "image_edit", "video"
        original_params: Dict[str, Any],
        user_id: int,
        timeout: float = 300,
    ):
        super().__init__(timeout=timeout)
        self.generation_type = generation_type
        self.original_params = original_params
        self.user_id = user_id

        # 为图片类型添加"切换到 NovelAI"按钮
        if generation_type in ("image", "image_edit"):
            novelai_button = discord.ui.Button(
                label="切换到 NovelAI",
                style=discord.ButtonStyle.success,
                row=0,
            )
            novelai_button.callback = self._switch_to_novelai
            self.add_item(novelai_button)

            generate_video_button = discord.ui.Button(
                label="生成视频",
                style=discord.ButtonStyle.secondary,
                emoji="🎬",
                row=0,
            )
            generate_video_button.callback = self._generate_video_from_image
            self.add_item(generate_video_button)

        if generation_type == "video":
            extend_video_button = discord.ui.Button(
                label="延长视频",
                style=discord.ButtonStyle.secondary,
                emoji="⏩",
                row=0,
            )
            extend_video_button.callback = self._show_extend_video_modal
            self.add_item(extend_video_button)

        # 为图片类型添加模型选择下拉菜单
        if generation_type in ("image", "image_edit"):
            current_resolution = original_params.get("resolution", "default")
            current_rating = original_params.get("content_rating", "sfw")
            model_options = _build_model_options("image", current_resolution, current_rating)
            if model_options:
                self.model_select = discord.ui.Select(
                    placeholder="更换模型重新生成",
                    options=model_options,
                    min_values=1,
                    max_values=1,
                    row=1,
                )
                self.model_select.callback = self._on_model_select
                self.add_item(self.model_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """任何用户都可以使用按钮"""
        return True

    async def on_timeout(self):
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True

    @discord.ui.button(
        label="重新生成",
        style=discord.ButtonStyle.primary,
        emoji="🔄",
        row=0,
    )
    async def regenerate_same(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        try:
            await self._do_slash_regenerate(interaction, new_prompt=None)
        except Exception as e:
            log.error(f"斜杠命令重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("重新生成失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(
        label="修改提示词",
        style=discord.ButtonStyle.secondary,
        emoji="✏️",
        row=0,
    )
    async def regenerate_edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        current_prompt = self.original_params.get("prompt", "")
        modal = EditPromptModal(
            current_prompt=current_prompt,
            regenerate_callback=self._do_slash_regenerate,
        )
        await interaction.response.send_modal(modal)

    async def _on_model_select(self, interaction: discord.Interaction):
        """处理模型选择下拉菜单的回调"""
        await interaction.response.defer()

        selected_value = self.model_select.values[0]
        resolution, rating = selected_value.split("|")

        try:
            await self._do_slash_regenerate(
                interaction,
                new_prompt=None,
                override_resolution=resolution,
                override_rating=rating,
            )
        except Exception as e:
            log.error(f"斜杠命令更换模型重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send("重新生成失败了，请稍后再试...", ephemeral=True)
            except Exception:
                pass

    async def _switch_to_novelai(self, interaction: discord.Interaction):
        """切换到 NovelAI 引擎生成图片"""
        await interaction.response.defer()
        try:
            await _do_novelai_regenerate(
                interaction=interaction,
                prompt=self.original_params.get("prompt", ""),
                user_id=interaction.user.id,
            )
        except Exception as e:
            log.error(f"切换到 NovelAI 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换到 NovelAI 失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    async def _generate_video_from_image(self, interaction: discord.Interaction):
        """从当前图片结果生成视频。"""
        try:
            from src.chat.features.tools.functions.generate_image_novelai import (
                GenerateVideoModal,
            )

            modal = GenerateVideoModal(
                image_prompt=self.original_params.get("prompt", ""),
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            log.error(f"打开生成视频弹窗失败: {e}", exc_info=True)
            try:
                await interaction.response.send_message("打开生成视频面板失败，请稍后再试。", ephemeral=True)
            except Exception:
                pass

    async def _show_extend_video_modal(self, interaction: discord.Interaction):
        """弹出延长视频补充要求输入框。"""
        modal = ExtendVideoModal(self._extend_video_from_tail_frame)
        await interaction.response.send_modal(modal)

    async def _extend_video_from_tail_frame(
        self,
        interaction: discord.Interaction,
        user_idea: str = "",
    ):
        """斜杠命令结果：基于当前视频尾帧续写下一段。"""
        # 与对话工具视图共用同一套实现；临时借用 RegenerateView 方法避免两份逻辑漂移。
        proxy = RegenerateView(
            generation_type="video",
            original_params=self.original_params,
            user_id=self.user_id,
        )
        await proxy._extend_video_from_tail_frame(
            interaction=interaction,
            user_idea=user_idea,
        )

    async def _do_slash_regenerate(
        self,
        interaction: discord.Interaction,
        new_prompt: Optional[str] = None,
        override_resolution: Optional[str] = None,
        override_rating: Optional[str] = None,
    ):
        """执行斜杠命令的重新生成"""
        channel = interaction.channel
        if not channel:
            return

        prompt = new_prompt if new_prompt else self.original_params.get("prompt", "")
        # 使用点击者的用户ID进行扣费
        clicker_user_id = interaction.user.id

        # 应用模型覆盖
        resolution = override_resolution or self.original_params.get("resolution", "default")
        content_rating = override_rating or self.original_params.get("content_rating", "sfw")

        if self.generation_type == "image":
            await self._regenerate_slash_image(channel, interaction, prompt, clicker_user_id, resolution, content_rating)
        elif self.generation_type == "image_edit":
            await self._regenerate_slash_image_edit(channel, interaction, prompt, clicker_user_id, resolution, content_rating)
        elif self.generation_type == "video":
            await self._regenerate_slash_video(channel, interaction, prompt, clicker_user_id)

    async def _regenerate_slash_image(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
        resolution: str = "default",
        content_rating: str = "sfw",
    ):
        """斜杠命令重新生成图片"""
        from src.chat.features.tools.functions.generate_image import generate_image

        params = {
            "prompt": prompt,
            "negative_prompt": self.original_params.get("negative_prompt"),
            "aspect_ratio": self.original_params.get("aspect_ratio", "1:1"),
            "number_of_images": self.original_params.get("number_of_images", 1),
            "resolution": resolution,
            "content_rating": content_rating,
            "preview_message": "正在重新生成图片...",
            "success_message": "重新生成完成~",
            "channel": channel,
            "user_id": str(clicker_user_id),
            "bot": interaction.client if hasattr(interaction, "client") else None,
            "request_user": interaction.user,
        }
        for key in OPENAI_IMAGE_PARAM_KEYS:
            if key in self.original_params:
                params[key] = self.original_params.get(key)

        result = await generate_image(**params)

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass

    async def _regenerate_slash_image_edit(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
        resolution: str = "default",
        content_rating: str = "sfw",
    ):
        """斜杠命令重新生成图生图(使用保存的参考图片数据)"""
        from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service

        # 检查是否有保存的参考图片数据（兼容单图与多图）
        reference_image_data = self.original_params.get("reference_image_data")
        reference_image_mime_type = self.original_params.get("reference_image_mime_type")
        reference_images_data = self.original_params.get("reference_images_data") or []
        reference_images_mime_types = self.original_params.get("reference_images_mime_types") or []

        normalized_reference_images: List[Dict[str, Any]] = []
        if isinstance(reference_images_data, list):
            for idx, image_bytes in enumerate(reference_images_data):
                if not image_bytes:
                    continue
                mime_type = (
                    reference_images_mime_types[idx]
                    if idx < len(reference_images_mime_types) and reference_images_mime_types[idx]
                    else "image/png"
                )
                normalized_reference_images.append(
                    {"data": image_bytes, "mime_type": mime_type}
                )

        if not normalized_reference_images and reference_image_data:
            normalized_reference_images.append(
                {
                    "data": reference_image_data,
                    "mime_type": reference_image_mime_type or "image/png",
                }
            )

        result = None
        if not normalized_reference_images:
            # 如果没有保存参考图片,回退到普通图片生成
            from src.chat.features.tools.functions.generate_image import generate_image
            params = {
                "prompt": prompt,
                "aspect_ratio": self.original_params.get("aspect_ratio", "1:1"),
                "number_of_images": self.original_params.get("number_of_images", 1),
                "resolution": resolution,
                "content_rating": content_rating,
                "preview_message": "正在重新生成图片...",
                "success_message": "重新生成完成~",
                "channel": channel,
                "user_id": str(clicker_user_id),
                "bot": interaction.client if hasattr(interaction, "client") else None,
                "request_user": interaction.user,
            }
            for key in OPENAI_IMAGE_PARAM_KEYS:
                if key in self.original_params:
                    params[key] = self.original_params.get(key)
            result = await generate_image(**params)
        else:
            # 使用保存的参考图片进行图生图
            try:
                import io
                from src.chat.features.odysseia_coin.service.coin_service import coin_service
                from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
                from src.chat.utils.prompt_utils import replace_emojis
                from src.chat.utils.database import chat_db_manager

                cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)

                # 检查绘图封禁
                ban_status = await chat_db_manager.get_image_generation_ban_status(clicker_user_id)
                if ban_status.get("is_banned"):
                    remaining_text = ban_status.get("remaining_text", "未知时长")
                    await interaction.followup.send(
                        f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
                        ephemeral=True,
                    )
                    return

                # 检查余额
                balance = await coin_service.get_balance(clicker_user_id)
                if balance < cost:
                    await interaction.followup.send(
                        f"灵石不足哦~需要 {cost} 个,你只有 {balance} 个呢。",
                        ephemeral=True
                    )
                    return

                # 发送预告消息
                preview_msg = await channel.send("正在重新生成图片...")

                # 调用图生图服务
                aspect_ratio = self.original_params.get("aspect_ratio", "1:1")
                edited_image_bytes = await gemini_imagen_service.edit_image(
                    reference_image=normalized_reference_images[0]["data"],
                    edit_prompt=prompt,
                    reference_mime_type=normalized_reference_images[0].get("mime_type", "image/png"),
                    reference_images=normalized_reference_images,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating=content_rating,
                    model_name_override=self.original_params.get("model_name_override"),
                    openai_image_size=self.original_params.get("openai_image_size"),
                    openai_response_format=self.original_params.get("openai_response_format"),
                    openai_stream=self.original_params.get("openai_stream"),
                    openai_quality=self.original_params.get("openai_quality"),
                    openai_style=self.original_params.get("openai_style"),
                    openai_image_api_mode=self.original_params.get("openai_image_api_mode"),
                )

                if edited_image_bytes:
                    # 扣除灵石
                    await coin_service.remove_coins(
                        clicker_user_id, cost, f"AI图生图重新生成: {prompt[:30]}..."
                    )

                    # 获取模型名称
                    edit_model_name = gemini_imagen_service._get_model_for_resolution(
                        resolution=resolution, is_edit=True, content_rating=content_rating
                    )

                    # 构建 Embed
                    embed = discord.Embed(title="AI 图生图", color=0x2b2d31)
                    embed.set_author(
                        name=interaction.user.display_name,
                        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
                    )
                    embed.add_field(
                        name="编辑提示词",
                        value=f"```\n{prompt[:1016]}\n```",
                        inline=False,
                    )
                    embed.add_field(name="\u200b", value="重新生成完成~", inline=False)
                    embed.set_footer(text=f"模型: {edit_model_name}")

                    # 创建新的重新生成视图
                    new_view = SlashCommandRegenerateView(
                        generation_type="image_edit",
                        original_params=self.original_params.copy(),
                        user_id=clicker_user_id,
                    )
                    # 更新提示词
                    new_view.original_params["prompt"] = prompt
                    new_view.original_params["resolution"] = resolution
                    new_view.original_params["content_rating"] = content_rating

                    file = discord.File(
                        io.BytesIO(edited_image_bytes),
                        filename="edited_image.png",
                        spoiler=should_spoiler_image(content_rating),
                    )
                    sent_message = await channel.send(embed=embed, file=file, view=new_view)
                    if sent_message:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=clicker_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )
                else:
                    await interaction.followup.send("图片生成失败了，请稍后再试...", ephemeral=True)

            except Exception as e:
                log.error(f"斜杠命令图生图重新生成失败: {e}", exc_info=True)
                await interaction.followup.send("生成失败了，请稍后再试...", ephemeral=True)
                return

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass

    async def _regenerate_slash_video(
        self,
        channel: discord.abc.Messageable,
        interaction: discord.Interaction,
        prompt: str,
        clicker_user_id: int,
    ):
        """斜杠命令重新生成视频(保留原始参考图片)"""
        from src.chat.features.tools.functions.generate_video import generate_video

        params = {
            "prompt": prompt,
            "duration": self.original_params.get("duration", 6),
            "size": self.original_params.get("size", "1280x720"),
            "quality": self.original_params.get("quality", "high"),
            "model": self.original_params.get("model")
            or self.original_params.get("video_model_name"),
            "reference_image_url": self.original_params.get("reference_image_url"),
            "preview_message": "正在重新生成视频...",
            "success_message": "重新生成完成~",
            "channel": channel,
            "user_id": str(clicker_user_id),
            "bot": interaction.client if hasattr(interaction, "client") else None,
            "request_user": interaction.user,
        }

        # 如果有保存的参考图片数据,使用图生视频模式（兼容单图与多图）
        reference_image_data = self.original_params.get("reference_image_data")
        reference_image_mime_type = self.original_params.get("reference_image_mime_type")
        reference_images_data = self.original_params.get("reference_images_data") or []
        reference_images_mime_types = self.original_params.get("reference_images_mime_types") or []

        normalized_reference_images: List[Dict[str, Any]] = []
        if isinstance(reference_images_data, list):
            for idx, image_bytes in enumerate(reference_images_data):
                if not image_bytes:
                    continue
                mime_type = (
                    reference_images_mime_types[idx]
                    if idx < len(reference_images_mime_types) and reference_images_mime_types[idx]
                    else "image/png"
                )
                normalized_reference_images.append(
                    {"data": image_bytes, "mime_type": mime_type}
                )

        if not normalized_reference_images and reference_image_data:
            normalized_reference_images.append(
                {
                    "data": reference_image_data,
                    "mime_type": reference_image_mime_type or "image/png",
                }
            )

        if normalized_reference_images:
            # 保持图生视频模式
            params["use_reference_image"] = True
            params["_prepared_reference_images"] = normalized_reference_images
            params["_prepared_reference_image"] = normalized_reference_images[0]
        else:
            # 文生视频模式
            params["use_reference_image"] = False

        result = await generate_video(**params)

        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass


# ==================== 切换到 NovelAI 的共享逻辑 ====================


async def _do_novelai_regenerate(
    interaction: discord.Interaction,
    prompt: str,
    user_id: int,
):
    """
    使用 NovelAI 引擎重新生成图片（从 Imagen 切换过来）。
    切换时强制触发 AI 重写：将当前提示词统一转换为 NovelAI 需要的 Danbooru 标签格式。
    """
    from src.chat.features.novelai_generation.services.novelai_service import novelai_service
    from src.chat.config.chat_config import NOVELAI_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager
    from src.chat.features.tools.functions.generate_image_novelai import (
        _convert_imagen_prompt_to_novelai_prompt,
    )

    # 检查 NovelAI 服务可用性
    if not novelai_service.is_available():
        await interaction.followup.send("NovelAI 服务当前不可用，请在 Dashboard 中配置。", ephemeral=True)
        return

    cost = NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5)

    # 检查绘图封禁
    ban_status = await chat_db_manager.get_image_generation_ban_status(user_id)
    if ban_status.get("is_banned"):
        remaining_text = ban_status.get("remaining_text", "未知时长")
        await interaction.followup.send(
            f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
            ephemeral=True,
        )
        return

    # 检查余额
    if cost > 0:
        try:
            balance = await coin_service.get_balance(user_id)
            if balance < cost:
                await interaction.followup.send(
                    f"灵石不足（需要 {cost}，当前 {balance}）",
                    ephemeral=True,
                )
                return
        except Exception:
            pass

    # 切换时强制做一次 AI 重写：自然语言/混合词 -> Danbooru Tag
    novelai_prompt = await _convert_imagen_prompt_to_novelai_prompt(
        prompt,
        force_rewrite=True,
    )

    # 调用 NovelAI 生成
    result = await novelai_service.generate_image(
        prompt=novelai_prompt,
        width=832,
        height=1216,
        seed=None,
    )

    if result is None:
        await interaction.followup.send("NovelAI 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if cost > 0:
        try:
            await coin_service.remove_coins(
                user_id, cost, f"NovelAI生图(切换): {novelai_prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除灵石失败: {e}")

    # 构建 Embed
    embed = discord.Embed(
        title="NovelAI 图片生成（从 Imagen 切换）",
        color=0x9B59B6,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    # 生成信息（紧凑排列，提示词通过按钮查看）
    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
    embed.add_field(name="种子", value=str(result.seed), inline=True)
    embed.add_field(
        name="参数",
        value=f"{result.width}x{result.height}",
        inline=True,
    )
    embed.set_footer(text=f"消耗 {cost} 灵石 | {model_name}")

    image_file = discord.File(
        io.BytesIO(result.image_data),
        filename="novelai_generated.png",
        spoiler=True,
    )
    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

    # 创建 NovelAI 结果的交互按钮
    from src.chat.features.tools.functions.generate_image_novelai import NovelAIResultView
    novelai_view = NovelAIResultView(
        prompt=novelai_prompt,
        negative_prompt=None,
        width=832,
        height=1216,
        steps=28,
        scale=5.0,
        sampler="k_euler_ancestral",
        preset_name=None,
        user_id=str(user_id),
        cost=cost,
    )

    sent_message = await interaction.followup.send(
        embed=embed, file=image_file, view=novelai_view, wait=True
    )
    if sent_message:
        await chat_db_manager.register_generated_image_message(
            message_id=sent_message.id,
            user_id=user_id,
            guild_id=sent_message.guild.id if sent_message.guild else None,
            channel_id=sent_message.channel.id,
        )
    log.info(f"已从 Imagen 切换到 NovelAI 生成图片, 种子: {result.seed}")
