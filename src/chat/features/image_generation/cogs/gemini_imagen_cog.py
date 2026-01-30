# -*- coding: utf-8 -*-

"""
Gemini Imagen 图像生成命令 Cog
提供 /画图 和 /图生图 命令使用 Gemini Imagen 模型生成图像
"""

import logging
import io
import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG, PROMPT_CONFIG
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.config.emoji_config import replace_emotion_tags
from ..services.gemini_imagen_service import gemini_imagen_service

log = logging.getLogger(__name__)


class GeminiImagenCog(commands.Cog):
    """处理 Gemini Imagen 图像生成相关命令的 Cog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.image_cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 30)
        self.edit_cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)  # 图生图成本略高

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
            # 3. 并行调用：生成图片 + AI 个性化回复
            log.info(
                f"用户 {user_id} 请求 Gemini Imagen 生成图片, "
                f"提示词: {prompt[:100]}..., 宽高比: {aspect_ratio}"
            )
            
            # 使用 asyncio.gather 并行执行图片生成和 AI 回复
            import asyncio
            
            async def generate_ai_response():
                """让 AI 根据用户请求生成个性化回复"""
                try:
                    from src.chat.services.gemini_service import gemini_service as gs
                    if gs.is_available():
                        response = await gs.generate_simple_response(
                            prompt=f"""你是月月，一个傲娇的狐狸娘AI助手。用户请求你画一张图，提示词是："{prompt}"
请用你的傲娇性格回应用户，简短地评价一下这个画图请求，可以用<表情>标签表达情绪。
回复要简短（1-2句话），例如：
- "这个我画起来完全没问题啦！<傲娇>"
- "哼，你的品味还不错嘛~让我来画给你看！<得意>"
- "这...这种可爱的东西，正好是我擅长的！<害羞>"
只回复角色台词，不要解释。""",
                            generation_config={
                                "temperature": 1.0,
                                "max_output_tokens": 100,
                            }
                        )
                        return response.strip() if response else None
                except Exception as e:
                    log.warning(f"生成 AI 回复失败: {e}")
                return None
            
            # 并行执行
            image_data, ai_response = await asyncio.gather(
                gemini_imagen_service.generate_single_image(
                    prompt=prompt,
                    negative_prompt=negative_prompt if negative_prompt else None,
                    aspect_ratio=aspect_ratio,
                ),
                generate_ai_response(),
                return_exceptions=True
            )
            
            # 处理可能的异常
            if isinstance(image_data, Exception):
                log.error(f"图片生成异常: {image_data}")
                image_data = None
            if isinstance(ai_response, Exception):
                log.warning(f"AI 回复异常: {ai_response}")
                ai_response = None

            # 4. 发送结果
            if image_data:
                file = discord.File(io.BytesIO(image_data), filename="generated_image.png")
                
                # 使用 AI 生成的回复，如果没有则使用默认
                if ai_response:
                    # 替换表情标签
                    ai_response = replace_emotion_tags(ai_response)
                    response_msg = f"{ai_response}\n消耗 {self.image_cost} 月光币，剩余 {new_balance}。"
                else:
                    response_msg = (
                        f"图片画好啦！\n"
                        f"消耗 {self.image_cost} 月光币，剩余 {new_balance}。"
                    )
                
                response_msg += f"\n\n**提示词:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}"
                if negative_prompt:
                    response_msg += f"\n**排除:** {negative_prompt[:100]}{'...' if len(negative_prompt) > 100 else ''}"
                
                await interaction.followup.send(response_msg, file=file)
            else:
                # 生成失败,返还费用
                await coin_service.add_coins(
                    user_id, self.image_cost, "Gemini Imagen 生成失败返还"
                )
                await interaction.followup.send(
                    "呜...图片生成失败了，已经把月光币还给你了。\n"
                    "可能是提示词有问题，或者服务暂时不可用..."
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

    @app_commands.command(name="图生图", description="上传一张图片，让 AI 根据你的指令修改它")
    @app_commands.describe(
        image="要修改的参考图片",
        edit_prompt="描述你想要如何修改这张图片",
        aspect_ratio="输出图片的宽高比",
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
    async def image_to_image(
        self,
        interaction: discord.Interaction,
        image: discord.Attachment,
        edit_prompt: str,
        aspect_ratio: str = None,
    ):
        """/图生图 命令的实现"""
        # 检查附件是否是图片
        if not image.content_type or not image.content_type.startswith("image/"):
            await interaction.response.send_message(
                "你上传的不是图片哦！请上传一张 PNG、JPG 或 WebP 格式的图片。",
                ephemeral=True,
            )
            return

        # 检查服务是否可用
        if not gemini_imagen_service.is_available():
            await interaction.response.send_message(
                "哼...抱歉,图生图服务当前未启用。\n"
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
        if balance < self.edit_cost:
            await interaction.response.send_message(
                f"你的月光币余额不足哦!图生图需要 {self.edit_cost} 月光币,你当前只有 {balance}。\n"
                "...才不是因为心疼你才提醒的!",
                ephemeral=True,
            )
            return

        # 延迟响应，因为图像生成需要时间
        await interaction.response.defer(thinking=True)

        # 2. 先扣费
        new_balance = await coin_service.remove_coins(
            user_id, self.edit_cost, "Gemini Imagen 图生图"
        )
        if new_balance is None:
            await interaction.followup.send(
                "呜...扣费时发生错误,请稍后再试。"
            )
            return

        try:
            # 3. 读取参考图片
            log.info(
                f"用户 {user_id} 请求图生图, "
                f"编辑指令: {edit_prompt[:100]}..., 宽高比: {aspect_ratio}"
            )
            
            try:
                reference_image_bytes = await image.read()
                reference_mime_type = image.content_type
            except Exception as e:
                log.error(f"读取参考图片失败: {e}")
                await coin_service.add_coins(
                    user_id, self.edit_cost, "图生图读取图片失败返还"
                )
                await interaction.followup.send(
                    "读取你上传的图片时出了点问题...已经把月光币还给你了。"
                )
                return
            
            # 4. 并行调用：图生图 + AI 个性化回复
            import asyncio
            
            async def generate_ai_response():
                """让 AI 根据用户请求生成个性化回复"""
                try:
                    from src.chat.services.gemini_service import gemini_service as gs
                    if gs.is_available():
                        response = await gs.generate_simple_response(
                            prompt=f"""你是月月，一个傲娇的狐狸娘AI助手。用户请求你修改一张图，编辑指令是："{edit_prompt}"
请用你的傲娇性格回应用户，简短地评价一下这个图片修改请求，可以用<表情>标签表达情绪。
回复要简短（1-2句话），例如：
- "帮你改一改？可以是可以...让我看看！<傲娇>"
- "这种修改对我来说小菜一碟！<得意>"
- "好、好的，我来试试看...不要期待太高哦！<害羞>"
只回复角色台词，不要解释。""",
                            generation_config={
                                "temperature": 1.0,
                                "max_output_tokens": 100,
                            }
                        )
                        return response.strip() if response else None
                except Exception as e:
                    log.warning(f"生成 AI 回复失败: {e}")
                return None
            
            # 并行执行
            edited_image_data, ai_response = await asyncio.gather(
                gemini_imagen_service.edit_image(
                    reference_image=reference_image_bytes,
                    edit_prompt=edit_prompt,
                    reference_mime_type=reference_mime_type,
                    aspect_ratio=aspect_ratio,
                ),
                generate_ai_response(),
                return_exceptions=True
            )
            
            # 处理可能的异常
            if isinstance(edited_image_data, Exception):
                log.error(f"图生图异常: {edited_image_data}")
                edited_image_data = None
            if isinstance(ai_response, Exception):
                log.warning(f"AI 回复异常: {ai_response}")
                ai_response = None

            # 5. 发送结果
            if edited_image_data:
                file = discord.File(io.BytesIO(edited_image_data), filename="edited_image.png")
                
                # 使用 AI 生成的回复，如果没有则使用默认
                if ai_response:
                    # 替换表情标签
                    ai_response = replace_emotion_tags(ai_response)
                    response_msg = f"{ai_response}\n消耗 {self.edit_cost} 月光币，剩余 {new_balance}。"
                else:
                    response_msg = (
                        f"图片改好啦！\n"
                        f"消耗 {self.edit_cost} 月光币，剩余 {new_balance}。"
                    )
                
                response_msg += f"\n\n**编辑指令:** {edit_prompt[:200]}{'...' if len(edit_prompt) > 200 else ''}"
                
                await interaction.followup.send(response_msg, file=file)
            else:
                # 生成失败,返还费用
                await coin_service.add_coins(
                    user_id, self.edit_cost, "Gemini 图生图失败返还"
                )
                await interaction.followup.send(
                    "呜...图生图失败了，已经把月光币还给你了。\n"
                    "可能是编辑指令有问题，或者服务暂时不可用...\n"
                    "你可以试试更简单的指令，或者换一张图片。"
                )

        except Exception as e:
            log.error(f"处理 /图生图 命令时发生未知错误: {e}", exc_info=True)
            # 发生未知错误,返还费用
            await coin_service.add_coins(
                user_id, self.edit_cost, "Gemini 图生图异常返还"
            )
            await interaction.followup.send(
                "处理你的请求时发生了一个意料之外的错误,已返还你的月光币。\n"
                "才、才不是我的错呢!"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(GeminiImagenCog(bot))