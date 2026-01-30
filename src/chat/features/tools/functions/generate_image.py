# -*- coding: utf-8 -*-

"""
图片生成工具
让LLM可以在对话中自动调用Gemini Imagen生成图片
"""

import logging
import discord
from typing import Optional

log = logging.getLogger(__name__)

# 图片生成相关的emoji
GENERATING_EMOJI = "🎨"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败


async def generate_image(
    prompt: str,
    negative_prompt: Optional[str] = None,
    aspect_ratio: str = "1:1",
    preview_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用AI生成图片。当用户请求生成、绘制、画图片时调用此工具。
    
    注意：此功能无法生成色情、暴力或其他违规内容。如果生成失败，
    可能是因为提示词包含不当内容。
    
    Args:
        prompt: 图片描述提示词，直接使用中文自然语言描述即可。
                你需要根据用户的请求，用中文详细描述想要生成的图片内容，
                包括主体、风格、氛围、细节等。
                
                描述要点：
                - 描述画面主体（人物、动物、场景等）
                - 添加风格描述（二次元风格、写实风格、水彩画风格等）
                - 添加氛围/光照（柔和的光线、夕阳、夜晚等）
                - 添加细节描述（毛茸茸的、闪闪发光的、精致的等）
                
                例如用户说"画一只可爱的小猫"，你应该生成：
                "一只可爱的小猫，毛茸茸的皮毛，大而圆的眼睛，二次元风格，柔和的光线，高画质，细节丰富"
                
        negative_prompt: 负面提示词（可选），用中文描述不希望出现的内容。
                例如："低画质, 模糊, 文字水印, 变形"
                
        aspect_ratio: 图片宽高比，根据内容类型选择合适的比例：
                - "1:1" 适合头像、图标
                - "3:4" 或 "4:3" 适合人物立绘、风景
                - "9:16" 适合手机壁纸
                - "16:9" 适合电脑壁纸、场景图
                
        preview_message: （必填）在生成图片前先发送给用户的预告消息。
                根据用户的请求内容和你的性格特点，写一句有趣的话告诉用户你正在画图。
                例如："哇，你想要一只可爱的小猫？让我来画~" 或 "这个我很拿手哦，稍等一下~"
    
    Returns:
        成功后图片会直接发送给用户，你需要用语言告诉用户图已经画好了。
    """
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    
    # 获取消息对象（用于添加反应）
    message: Optional[discord.Message] = kwargs.get("message")
    
    # 辅助函数：安全地添加反应
    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")
    
    # 辅助函数：安全地移除反应
    async def remove_reaction(emoji: str):
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")
    
    # 检查服务是否可用
    if not gemini_imagen_service.is_available():
        log.warning("Gemini Imagen 服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "图片生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }
    
    # 获取用户ID（如果提供）用于扣费
    user_id = kwargs.get("user_id")
    cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 30)
    
    # 检查用户余额（如果需要扣费）
    if user_id and cost > 0:
        try:
            user_id_int = int(user_id)
            balance = await coin_service.get_balance(user_id_int)
            if balance < cost:
                return {
                    "generation_failed": True,
                    "reason": "insufficient_balance",
                    "cost": cost,
                    "balance": balance,
                    "hint": f"用户月光币不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够，让他们去赚点月光币再来。"
                }
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")
    
    log.info(f"调用图片生成工具，提示词: {prompt[:100]}...")
    
    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)
    
    # 发送预告消息（先回复用户，使用 LLM 生成的消息）
    channel = kwargs.get("channel")
    if channel and preview_message:
        try:
            await channel.send(preview_message)
            log.info(f"已发送图片生成预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")
    
    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
            log.warning(f"无效的宽高比，已重置为默认值 1:1")
        
        # 调用图片生成服务
        image_bytes = await gemini_imagen_service.generate_single_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
        )
        
        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)
        
        if image_bytes:
            # 添加成功反应
            await add_reaction(SUCCESS_EMOJI)
            
            # 扣除月光币
            if user_id and cost > 0:
                try:
                    user_id_int = int(user_id)
                    await coin_service.remove_coins(
                        user_id_int, cost, f"AI图片生成: {prompt[:30]}..."
                    )
                    log.info(f"用户 {user_id_int} 生成图片成功，扣除 {cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")
            
            # 直接发送图片到频道
            if channel:
                try:
                    import io
                    file = discord.File(io.BytesIO(image_bytes), filename="generated_image.png")
                    await channel.send(file=file)
                    log.info("图片已直接发送到频道")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}")
            
            # 返回成功信息给 AI（不再返回图片数据，因为已经直接发送了）
            return {
                "success": True,
                "prompt_used": prompt,  # 返回原始中文提示词
                "cost": cost,
                "message": "图片已成功生成并展示给用户了！请用自己的语气告诉用户画好了，并展示使用的中文提示词（不要翻译成英文）。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)
            
            # 图片生成失败，可能是内容违规
            log.warning(f"图片生成返回空结果，可能是内容违规。提示词: {prompt}")
            return {
                "generation_failed": True,
                "reason": "content_violation_or_unclear",
                "hint": "图片生成失败了，可能是提示词包含不当内容或描述不够清晰。请用自己的语气告诉用户不能画这种图，让他们换个描述试试。"
            }
            
    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        log.error(f"图片生成工具执行错误: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "system_error",
            "hint": f"图片生成时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }