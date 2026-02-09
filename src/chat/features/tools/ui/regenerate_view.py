# -*- coding: utf-8 -*-

"""
重新生成交互组件
为图片/视频生成提供"重新生成"、"修改提示词重新生成"和"更换模型"功能
支持对话工具调用和斜杠命令两种场景
"""

import logging
import discord
from typing import Optional, Dict, Any, Callable, Awaitable, List

log = logging.getLogger(__name__)


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


class RegenerateView(discord.ui.View):
    """
    重新生成交互视图（对话工具调用版本）
    
    提供：
    1. 重新生成按钮 - 使用相同参数重新生成
    2. 修改提示词按钮 - 弹出模态框修改提示词后重新生成
    3. 更换模型下拉菜单 - 切换分辨率和内容分级后重新生成（仅图片类型）
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
        """重新生成图生图（对话工具版本，不使用参考图片因为原图可能已不可用）"""
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
        }
        
        result = await generate_image(**params)
        
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
        """重新生成视频"""
        from src.chat.features.tools.functions.generate_video import generate_video
        
        params = self.original_params.copy()
        params["prompt"] = prompt
        params["channel"] = channel
        params["user_id"] = str(clicker_user_id)
        params["preview_message"] = "正在重新生成视频..."
        params["success_message"] = params.get("original_success_message", "重新生成完成~")
        
        if hasattr(interaction, "client"):
            params["bot"] = interaction.client
        
        params.pop("message", None)
        params.pop("original_success_message", None)
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
    3. 更换模型下拉菜单（仅图片类型）
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
        }
        
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
        """斜杠命令重新生成图生图（不使用参考图片，因为原图可能已不可用）"""
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
        }
        
        result = await generate_image(**params)
        
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
        """斜杠命令重新生成视频"""
        from src.chat.features.tools.functions.generate_video import generate_video
        
        params = {
            "prompt": prompt,
            "duration": self.original_params.get("duration", 5),
            "use_reference_image": False,
            "preview_message": "正在重新生成视频...",
            "success_message": "重新生成完成~",
            "channel": channel,
            "user_id": str(clicker_user_id),
            "bot": interaction.client if hasattr(interaction, "client") else None,
        }
        
        result = await generate_video(**params)
        
        if result and result.get("generation_failed"):
            hint = result.get("hint", "生成失败了，请稍后再试。")
            try:
                await interaction.followup.send(hint, ephemeral=True)
            except Exception:
                pass