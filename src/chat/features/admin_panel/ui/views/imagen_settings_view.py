# -*- coding: utf-8 -*-

import discord
import logging
from typing import Optional

from src.chat.config import chat_config

log = logging.getLogger(__name__)


class ImagenSettingsView(discord.ui.View):
    """Gemini Imagen 设置管理视图"""

    def __init__(self, interaction: discord.Interaction, message: discord.Message):
        super().__init__(timeout=300)
        self.author_id = interaction.user.id
        self.message = message
        self._initialize_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "你不能操作这个视图。", ephemeral=True
            )
            return False
        return True

    def _initialize_components(self):
        """构建UI组件"""
        self.clear_items()
        
        # 编辑 API URL 按钮
        edit_url_button = discord.ui.Button(
            label="编辑 API URL",
            emoji="🔗",
            style=discord.ButtonStyle.primary,
            row=0
        )
        edit_url_button.callback = self.edit_api_url
        self.add_item(edit_url_button)
        
        # 测试连接按钮
        test_button = discord.ui.Button(
            label="测试连接",
            emoji="🔬",
            style=discord.ButtonStyle.secondary,
            row=0
        )
        test_button.callback = self.test_connection
        self.add_item(test_button)
        
        # 返回按钮
        back_button = discord.ui.Button(
            label="返回主菜单",
            emoji="⬅️",
            style=discord.ButtonStyle.secondary,
            row=1
        )
        back_button.callback = self.go_back
        self.add_item(back_button)

    async def get_embed(self) -> discord.Embed:
        """生成 Imagen 设置的 Embed"""
        config = chat_config.GEMINI_IMAGEN_CONFIG
        
        embed = discord.Embed(
            title="🎨 Gemini Imagen 绘图设置",
            description="配置 Gemini Imagen API 用于 `/paint` 绘图命令。",
            color=discord.Color.purple()
        )
        
        # API URL
        api_url = config.get("API_URL", "未配置")
        # 隐藏部分 URL 以保护隐私
        if api_url and len(api_url) > 30:
            masked_url = api_url[:20] + "..." + api_url[-10:]
        else:
            masked_url = api_url
        embed.add_field(
            name="🔗 API URL",
            value=f"`{masked_url}`",
            inline=False
        )
        
        # 模型
        model = config.get("MODEL", "未配置")
        embed.add_field(
            name="🤖 模型",
            value=f"`{model}`",
            inline=True
        )
        
        # 默认图片数量
        default_count = config.get("DEFAULT_NUMBER_OF_IMAGES", 1)
        embed.add_field(
            name="📊 默认图片数量",
            value=f"`{default_count}`",
            inline=True
        )
        
        # 支持的宽高比
        aspect_ratios = config.get("ASPECT_RATIOS", {})
        ratios_text = ", ".join(aspect_ratios.keys()) if aspect_ratios else "无"
        embed.add_field(
            name="📐 支持的宽高比",
            value=f"`{ratios_text}`",
            inline=False
        )
        
        embed.set_footer(text="提示：修改 API URL 后将实时生效")
        
        return embed

    async def update_view(self):
        """更新视图"""
        embed = await self.get_embed()
        self._initialize_components()
        if self.message:
            await self.message.edit(embed=embed, view=self)

    async def edit_api_url(self, interaction: discord.Interaction):
        """编辑 API URL"""
        modal = EditImagenUrlModal(self)
        await interaction.response.send_modal(modal)

    async def test_connection(self, interaction: discord.Interaction):
        """测试 Imagen API 连接"""
        await interaction.response.defer(ephemeral=True)
        
        try:
            from src.chat.features.image_generation.services.gemini_imagen_service import (
                gemini_imagen_service
            )
            
            # 尝试生成一个简单的测试图片
            result = await gemini_imagen_service.generate_single_image(
                prompt="A simple test image of a white circle on black background",
                aspect_ratio="1:1"
            )
            
            if result.get("success"):
                await interaction.followup.send(
                    "✅ Gemini Imagen API 连接测试成功！",
                    ephemeral=True
                )
            else:
                error = result.get("error", "未知错误")
                await interaction.followup.send(
                    f"❌ 连接测试失败：{error}",
                    ephemeral=True
                )
        except Exception as e:
            log.error(f"Imagen API 测试失败: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 连接测试出错：{str(e)}",
                ephemeral=True
            )

    async def go_back(self, interaction: discord.Interaction):
        """返回主菜单"""
        await interaction.response.defer()
        # 导入在这里以避免循环导入
        from .db_management_view import DBManagementView
        parent_view = DBManagementView(self.author_id)
        parent_view.message = self.message
        await parent_view.update_view()


class EditImagenUrlModal(discord.ui.Modal, title="编辑 Gemini Imagen API URL"):
    """编辑 Imagen API URL 的模态窗口"""

    api_url = discord.ui.TextInput(
        label="API URL",
        placeholder="请输入 Gemini Imagen API 的完整 URL...",
        style=discord.TextStyle.long,
        required=True,
        max_length=500
    )

    def __init__(self, parent_view: ImagenSettingsView):
        super().__init__()
        self.parent_view = parent_view
        # 预填充当前值
        current_url = chat_config.GEMINI_IMAGEN_CONFIG.get("API_URL", "")
        if current_url:
            self.api_url.default = current_url

    async def on_submit(self, interaction: discord.Interaction):
        new_url = self.api_url.value.strip()
        
        if not new_url:
            await interaction.response.send_message(
                "❌ API URL 不能为空。",
                ephemeral=True
            )
            return
        
        # 验证 URL 格式
        if not (new_url.startswith("http://") or new_url.startswith("https://")):
            await interaction.response.send_message(
                "❌ 请输入有效的 URL（以 http:// 或 https:// 开头）。",
                ephemeral=True
            )
            return
        
        # 更新运行时配置
        old_url = chat_config.GEMINI_IMAGEN_CONFIG.get("API_URL", "")
        chat_config.GEMINI_IMAGEN_CONFIG["API_URL"] = new_url
        
        log.info(
            f"管理员 {interaction.user.display_name} 更新了 Gemini Imagen API URL。"
            f"旧值: {old_url[:30] if old_url else 'N/A'}... -> 新值: {new_url[:30]}..."
        )
        
        await interaction.response.send_message(
            f"✅ API URL 已更新！\n\n"
            f"**注意**：此更改仅在当前运行时有效。\n"
            f"如需持久化，请更新 `.env` 文件中的 `GEMINI_IMAGEN_API_URL` 环境变量。",
            ephemeral=True
        )
        
        # 更新视图
        await self.parent_view.update_view()