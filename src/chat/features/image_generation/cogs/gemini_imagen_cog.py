# -*- coding: utf-8 -*-

"""
Gemini Imagen 图像生成命令 Cog
提供 /paint 命令使用 Gemini Imagen 模型生成图像
"""

import logging
import io
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from ..services.gemini_imagen_service import gemini_imagen_service

log = logging.getLogger(__name__)


class GeminiImagenCog(commands.Cog):
    """处理 Gemini Imagen 图像生成相关命令的 Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.image_cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 30)

    @app_commands.command(name="画图", description="使用 Gemini Imagen AI 生成一张图片")
    @app_commands.describe(
        prompt="描述你想要生成的图片内容",
        negative_prompt="你不希望在图片中出现的内容(可选)",
        aspect_ratio="图片宽高比",
    )
    @app_commands.choices(
        aspect_ratio=[
            app_commands.Choice(name="1:1 (正方形)", value="1:1"),
            app_commands.Choice(name="3:4 (竖版)", value="3:4"),
            app_commands.Choice(name="4:3 (横版)", value="4:3"),
            app_commands.Choice(name="9:16 (手机竖屏)", value="9:16"),
            app_commands.Choice(name="16:9 (宽屏)", value="16:9"),
        ]
    )
    async def paint(
        self,
        interaction: discord.Interaction,
        prompt: str,
        negative_prompt: str = "",
        aspect_ratio: str = None,
    ):
        """/paint 命令的实现"""
        # 检查服务是否可用
        if not gemini_imagen_service.is_available():
            await interaction.response.send_message(
                "哼...抱歉,Gemini Imagen 图像生成服务当前未启用。\n"
                "才、才不是因为服务器出问题了呢!",
                ephemeral=True,
            )
            return

        user_id = interaction.user.id

        # 使用默认宽高比如果未指定
        if aspect_ratio is None:
            aspect_ratio = GEMINI_IMAGEN_CONFIG.get("DEFAULT_ASPECT_RATIO", "1:1")

        # 1. 检查余额
        balance = await coin_service.get_balance(user_id)
        if balance < self.image_cost:
            await interaction.response.send_message(
                f"你的月光币余额不足哦!生成图片需要 {self.image_cost} 月光币,你当前只有 {balance}。\n"
                "...才不是因为心疼你才提醒的!",
                ephemeral=True,
            )
            return

        # 延迟响应，因为图像生成需要时间
        await interaction.response.defer(thinking=True)

        # 2. 先扣费
        new_balance = await coin_service.remove_coins(
            user_id, self.image_cost, "Gemini Imagen 图像生成"
        )
        if new_balance is None:
            await interaction.followup.send(
                "呜...扣费时发生错误,请稍后再试。"
            )
            return

        try:
            # 3. 调用服务生成图片
            log.info(
                f"用户 {user_id} 请求 Gemini Imagen 生成图片, "
                f"提示词: {prompt[:100]}..., 宽高比: {aspect_ratio}"
            )
            
            image_data = await gemini_imagen_service.generate_single_image(
                prompt=prompt,
                negative_prompt=negative_prompt if negative_prompt else None,
                aspect_ratio=aspect_ratio,
            )

            # 4. 发送结果
            if image_data:
                file = discord.File(io.BytesIO(image_data), filename="generated_image.png")
                
                # 构建响应消息
                response_msg = (
                    f"图片生成成功啦!<傲娇>\n"
                    f"消耗 {self.image_cost} 月光币,剩余 {new_balance}。\n\n"
                    f"**提示词:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}"
                )
                if negative_prompt:
                    response_msg += f"\n**排除:** {negative_prompt[:100]}{'...' if len(negative_prompt) > 100 else ''}"
                
                await interaction.followup.send(response_msg, file=file)
            else:
                # 生成失败,返还费用
                await coin_service.add_coins(
                    user_id, self.image_cost, "Gemini Imagen 生成失败返还"
                )
                await interaction.followup.send(
                    "呜...图片生成失败了,已经把月光币还给你了。\n"
                    "可能是提示词有问题,或者服务暂时不可用..."
                )

        except Exception as e:
            log.error(f"处理 /paint 命令时发生未知错误: {e}", exc_info=True)
            # 发生未知错误,返还费用
            await coin_service.add_coins(
                user_id, self.image_cost, "Gemini Imagen 异常返还"
            )
            await interaction.followup.send(
                "处理你的请求时发生了一个意料之外的错误,已返还你的月光币。\n"
                "才、才不是我的错呢!"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(GeminiImagenCog(bot))