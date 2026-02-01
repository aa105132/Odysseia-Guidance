# -*- coding: utf-8 -*-

"""
图生图工具
让LLM可以在对话中自动识别用户发送的图片并根据指令修改
"""

import logging
import discord
from typing import Optional, List, Dict, Any

from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 图片生成相关的emoji
GENERATING_EMOJI = "🎨"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败


async def edit_image(
    edit_prompt: str,
    aspect_ratio: str = "1:1",
    resolution: str = "default",
    preview_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    修改用户发送的图片。当用户发送了一张图片并请求修改、编辑、调整时调用此工具。
    
    使用场景：
    - 用户发送一张图片并说"帮我把背景改成蓝色"
    - 用户发送一张图片并说"把这个人物变成动漫风格"
    - 用户发送一张图片并说"添加一些特效"
    - 用户回复一张图片并请求修改
    
    注意：此工具需要用户在对话中发送了图片才能使用。如果用户没有发送图片，
    请提示用户先发送一张图片。
    
    Args:
        edit_prompt: 编辑指令，用中文描述希望如何修改图片。
                你需要根据用户的请求，用中文详细描述想要的修改效果。
                
                描述要点：
                - 清晰描述想要的修改（改变颜色、添加元素、改变风格等）
                - 可以添加风格描述（二次元风格、油画风格等）
                - 保留不变的部分可以不提
                
                例如用户说"把背景改成夕阳"，你应该生成：
                "将图片的背景更改为美丽的夕阳景色，保持主体不变，添加温暖的橙红色调"
                
        aspect_ratio: 输出图片的宽高比，根据用户需求选择：
                - "1:1" 保持正方形
                - "3:4" 或 "4:3" 竖版/横版
                - "9:16" 手机壁纸比例
                - "16:9" 电脑壁纸比例
                如果用户没有特别要求，建议保持原图的大致比例。
                
        resolution: 图片分辨率，根据用户需求选择：
                - "default" 默认分辨率（最快）
                - "2k" 2K高清（用户明确要求高清、2K时使用）
                - "4k" 4K超高清（用户明确要求超高清、4K时使用）
                如果用户没有特别要求分辨率，使用 "default"
                
        preview_message: （必填）在修改图片前发送给用户的预告消息。
                根据用户的修改请求和你的性格特点，写一句有趣的话告诉用户你正在处理。
                例如："让我看看这张图...好的，我来帮你改改！" 或 "这个修改我可以做到~稍等哦！"
    
    Returns:
        成功后修改后的图片会直接发送给用户，你需要用语言告诉用户图片已经修改好了。
    """
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    
    # 获取消息对象（用于获取图片和添加反应）
    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel")
    
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
    
    # 辅助函数：从消息中提取图片
    async def extract_image_from_message(msg: discord.Message) -> Optional[Dict[str, Any]]:
        """从消息中提取第一张图片"""
        if msg.attachments:
            for attachment in msg.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    try:
                        image_bytes = await attachment.read()
                        return {
                            "data": image_bytes,
                            "mime_type": attachment.content_type,
                            "filename": attachment.filename
                        }
                    except Exception as e:
                        log.error(f"读取附件图片失败: {e}")
        return None
    
    # 1. 尝试获取用户发送的图片
    reference_image = None
    
    # 首先检查当前消息的附件
    if message:
        reference_image = await extract_image_from_message(message)
        
        # 如果当前消息没有图片，检查回复的消息
        if not reference_image and message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    reference_image = await extract_image_from_message(ref_msg)
                    
                    # 也检查转发消息中的图片
                    if not reference_image and hasattr(ref_msg, "message_snapshots") and ref_msg.message_snapshots:
                        for snapshot in ref_msg.message_snapshots:
                            if hasattr(snapshot, "attachments") and snapshot.attachments:
                                for attachment in snapshot.attachments:
                                    if attachment.content_type and attachment.content_type.startswith("image/"):
                                        try:
                                            image_bytes = await attachment.read()
                                            reference_image = {
                                                "data": image_bytes,
                                                "mime_type": attachment.content_type,
                                                "filename": attachment.filename
                                            }
                                            break
                                        except Exception as e:
                                            log.error(f"读取转发消息图片失败: {e}")
                            if reference_image:
                                break
            except Exception as e:
                log.warning(f"获取回复消息失败: {e}")
    
    # 如果还是没有找到图片，返回错误
    if not reference_image:
        return {
            "edit_failed": True,
            "reason": "no_image_found",
            "hint": "用户没有发送图片。请用自己的语气告诉用户，如果想要修改图片，需要先发送一张图片给你，或者回复一张图片并说明想要怎么修改。"
        }
    
    # 检查服务是否可用
    if not gemini_imagen_service.is_available():
        log.warning("Gemini Imagen 服务不可用")
        return {
            "edit_failed": True,
            "reason": "service_unavailable",
            "hint": "图片修改服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }
    
    # 获取用户ID（如果提供）用于扣费
    user_id = kwargs.get("user_id")
    cost = GEMINI_IMAGEN_CONFIG.get("IMAGE_EDIT_COST", 40)
    
    # 检查用户余额（如果需要扣费）
    if user_id and cost > 0:
        try:
            user_id_int = int(user_id)
            balance = await coin_service.get_balance(user_id_int)
            if balance < cost:
                return {
                    "edit_failed": True,
                    "reason": "insufficient_balance",
                    "cost": cost,
                    "balance": balance,
                    "hint": f"用户月光币不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够，让他们去赚点月光币再来。"
                }
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")
    
    log.info(f"调用图生图工具，编辑指令: {edit_prompt[:100]}...")
    
    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)
    
    # 发送预告消息
    if channel and preview_message:
        try:
            # 替换表情占位符为实际表情
            processed_message = replace_emojis(preview_message)
            await channel.send(processed_message)
            log.info(f"已发送图生图预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")
    
    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
            log.warning(f"无效的宽高比，已重置为默认值 1:1")
        
        # 调用图生图服务
        edited_image_bytes = await gemini_imagen_service.edit_image(
            reference_image=reference_image["data"],
            edit_prompt=edit_prompt,
            reference_mime_type=reference_image["mime_type"],
            aspect_ratio=aspect_ratio,
            resolution=resolution,
        )
        
        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)
        
        if edited_image_bytes:
            # 添加成功反应
            await add_reaction(SUCCESS_EMOJI)
            
            # 扣除月光币
            if user_id and cost > 0:
                try:
                    user_id_int = int(user_id)
                    await coin_service.remove_coins(
                        user_id_int, cost, f"AI图生图: {edit_prompt[:30]}..."
                    )
                    log.info(f"用户 {user_id_int} 图生图成功，扣除 {cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")
            
            # 直接发送图片到频道
            if channel:
                try:
                    import io
                    file = discord.File(io.BytesIO(edited_image_bytes), filename="SPOILER_edited_image.png")
                    # 发送图片和提示词（带遮罩）
                    prompt_text = f"```\n{edit_prompt}\n```"
                    await channel.send(content=prompt_text, file=file)
                    log.info("修改后的图片已直接发送到频道（带遮罩）")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}")
            
            # 返回成功信息给 AI
            return {
                "success": True,
                "edit_prompt_used": edit_prompt,
                "cost": cost,
                "message": "图片已成功修改并展示给用户了！请用自己的语气告诉用户图片已经改好了。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)
            
            # 图片编辑失败
            log.warning(f"图生图返回空结果。编辑指令: {edit_prompt}")
            return {
                "edit_failed": True,
                "reason": "edit_failed",
                "hint": "图片修改失败了，可能是编辑指令不够清晰或者图片格式有问题。请用自己的语气告诉用户换个描述试试，或者换一张图片。"
            }
            
    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        log.error(f"图生图工具执行错误: {e}", exc_info=True)
        return {
            "edit_failed": True,
            "reason": "system_error",
            "hint": f"图片修改时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }