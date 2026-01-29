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
    **kwargs
) -> dict:
    """
    使用AI生成图片。当用户请求生成、绘制、画图片时调用此工具。
    
    注意：此功能无法生成色情、暴力或其他违规内容。如果生成失败，
    可能是因为提示词包含不当内容。
    
    Args:
        prompt: 图片描述提示词，需要用英文描述想要生成的图片内容。
                例如："a cute fox girl with white fur, anime style, moonlight"
        negative_prompt: 负面提示词（可选），描述不希望出现的内容。
                例如："low quality, blurry, text, watermark"
        aspect_ratio: 图片宽高比，支持 "1:1", "3:4", "4:3", "9:16", "16:9"。
                默认为 "1:1"。
    
    Returns:
        如果成功，返回包含 image_data 的字典，LLM会将图片展示给用户。
        如果失败，返回错误信息字符串。
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
            
            # 返回图片数据，ToolService 会处理这个格式
            return {
                "image_data": {
                    "mime_type": "image/png",
                    "data": image_bytes
                },
                "message": "图片生成成功！"
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