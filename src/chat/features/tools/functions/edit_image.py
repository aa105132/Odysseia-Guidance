# -*- coding: utf-8 -*-

"""
图生图工具
让LLM可以在对话中自动识别用户发送的图片并根据指令修改
也支持从Discord自定义表情或用户头像提取图片作为参考
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
    content_rating: str = "sfw",
    emoji_id: Optional[str] = None,
    avatar_user_id: Optional[str] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    修改用户发送的图片。当用户发送了一张图片并请求修改、编辑、调整时调用此工具。
    也支持直接使用Discord自定义表情图片或用户头像作为参考图进行编辑。
    
    使用场景：
    - 用户发送一张图片并说"帮我把背景改成蓝色"
    - 用户发送一张图片并说"把这个人物变成动漫风格"
    - 用户发送一张图片并说"添加一些特效"
    - 用户回复一张图片并请求修改
    - 用户发送了自定义表情（如 <:name:123456>）并说"把这个表情改成..."
    - 用户说"提取xxx的头像帮我改成..."
    
    注意：此工具需要参考图片。图片来源优先级：
    1. emoji_id 参数指定的Discord自定义表情
    2. avatar_user_id 参数指定的用户头像
    3. 用户在对话中发送的图片附件
    如果以上都没有，请提示用户先发送一张图片。
    
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
        
        content_rating: 内容分级，根据图片内容和编辑请求判断：
                - "sfw" 安全内容（默认，适用于普通图片）
                - "nsfw" 成人内容（仅当原图或编辑请求明显涉及成人内容时使用）
                
                判断标准：
                - 如果原图包含裸露、性暗示或成人内容，应使用 "nsfw"
                - 如果编辑请求涉及色情、裸露、性感化等成人元素，应使用 "nsfw"
                - 其他情况使用 "sfw"
        
        emoji_id: （可选）Discord自定义表情的数字ID，用于提取表情图片作为参考图。
                当用户发送了自定义表情（如 <:smile:1234567890> 或 <a:dance:1234567890>）
                并要求以此表情为基础进行编辑时，填写表情的数字ID部分。
                例如用户发送了 <:myemoji:1234567890>，则填 "1234567890"
                
        avatar_user_id: （可选）Discord用户的数字ID，用于提取该用户头像作为参考图。
                当用户说"提取xxx的头像并修改"、"用ID为123的人的头像生成图片"时，
                填写目标用户的Discord数字ID。
                
        preview_message: （必填）你对这次图片修改请求的回复消息。
                这条消息会在生成前先发送给用户，作为预告。
                根据用户的修改请求和你的性格特点，写一句有趣的话告诉用户你正在处理。
                例如："让我看看这张图...好的，我来帮你改改！" 或 "这个修改我可以做到~稍等哦！"
                
        success_message: （必填）图片修改成功后的回复消息。
                这条消息会在图片修改成功后和图片一起发送给用户。
                根据修改结果，写一句符合你性格的话来回应用户。
                例如："改好了~看看满不满意？" 或 "嘿嘿，这是你要的效果吗？"
                **注意：图片修改成功后不会再有后续回复，所以这条 success_message 就是你的最终回复。**
    
    Returns:
        成功后修改后的图片和你的预告消息会发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
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
    
    # 1. 尝试获取参考图片（优先级：emoji_id > avatar_user_id > 消息附件 > 回复 > 历史）
    reference_image = None
    user_id = kwargs.get("user_id")  # 获取当前用户ID
    
    # 优先从 emoji_id 提取表情图片
    if emoji_id and not reference_image:
        try:
            from src.chat.features.tools.utils.discord_image_utils import fetch_emoji_image
            emoji_result = await fetch_emoji_image(emoji_id)
            if emoji_result:
                reference_image = emoji_result
                log.info(f"已从Discord表情提取参考图 (ID: {emoji_id})")
            else:
                log.warning(f"无法从Discord表情提取图片 (ID: {emoji_id})")
        except Exception as e:
            log.error(f"提取Discord表情图片失败: {e}")
    
    # 其次从 avatar_user_id 提取用户头像
    if avatar_user_id and not reference_image:
        try:
            from src.chat.features.tools.utils.discord_image_utils import fetch_avatar_image
            bot = kwargs.get("bot")
            guild = message.guild if message else None
            avatar_result = await fetch_avatar_image(
                user_id=avatar_user_id,
                bot=bot,
                guild=guild,
            )
            if avatar_result:
                reference_image = avatar_result
                log.info(f"已从Discord用户头像提取参考图 (用户ID: {avatar_user_id})")
            else:
                log.warning(f"无法提取Discord用户头像 (用户ID: {avatar_user_id})")
        except Exception as e:
            log.error(f"提取Discord用户头像失败: {e}")
    
    # 然后检查当前消息的附件
    if not reference_image and message:
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
        
        # 如果还是没有找到图片，检查频道的最近消息（用户可能先发图片再请求修改）
        if not reference_image and channel:
            try:
                log.info("未在当前消息或回复中找到图片，正在搜索频道最近消息...")
                # 获取最近的 5 条消息（包含所有用户，让AI自行判断上下文）
                async for hist_msg in channel.history(limit=5):
                    # 跳过当前消息
                    if hist_msg.id == message.id:
                        continue
                    # 搜索所有用户发送的图片
                    found_image = await extract_image_from_message(hist_msg)
                    if found_image:
                        log.info(f"在最近消息中找到图片 (消息 ID: {hist_msg.id}, 发送者: {hist_msg.author})")
                        reference_image = found_image
                        break
            except Exception as e:
                log.warning(f"搜索频道历史消息失败: {e}")
    
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
    
    # 添加"正生成"反应
    await add_reaction(GENERATING_EMOJI)
    
    # 发送预告消息并保存消息引用
    preview_msg: Optional[discord.Message] = None
    if channel and preview_message:
        try:
            # 替换表情占位符为实际表情
            processed_message = replace_emojis(preview_message)
            preview_msg = await channel.send(processed_message)
            log.info(f"已发送图生图预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")
    
    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
            log.warning(f"无效的宽高比，已重置为默认值 1:1")
        
        # 验证内容分级参数
        if content_rating not in ["sfw", "nsfw"]:
            content_rating = "sfw"
            log.warning(f"无效的内容分级参数，已重置为默认值 sfw")
        
        log.info(f"图生图内容分级: {content_rating}")
        
        # 调用图生图服务
        edited_image_bytes = await gemini_imagen_service.edit_image(
            reference_image=reference_image["data"],
            edit_prompt=edit_prompt,
            reference_mime_type=reference_image["mime_type"],
            aspect_ratio=aspect_ratio,
            resolution=resolution,
            content_rating=content_rating,
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
            
            # 直接发送图片到频道（Embed 格式 + 重新生成按钮）
            if channel:
                try:
                    import io
                    from src.chat.features.tools.ui.regenerate_view import RegenerateView
                    
                    # 获取实际使用的模型名称
                    edit_model_name = gemini_imagen_service._get_model_for_resolution(
                        resolution=resolution, is_edit=True, content_rating=content_rating
                    )
                    
                    # 构建 Discord Embed（标题+提示词+成功回复全在 Embed 内）
                    embed = discord.Embed(
                        title="AI 图生图",
                        color=0x2b2d31,
                    )
                    # 设置请求者头像和名称
                    if message and hasattr(message, 'author') and message.author:
                        embed.set_author(
                            name=message.author.display_name,
                            icon_url=message.author.display_avatar.url if message.author.display_avatar else None,
                        )
                    embed.add_field(
                        name="编辑提示词",
                        value=f"```\n{edit_prompt[:1016]}\n```",
                        inline=False,
                    )
                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.add_field(
                            name="",
                            value=processed_success[:1024],
                            inline=False,
                        )
                    embed.set_footer(text=f"模型: {edit_model_name}")
                    
                    # 创建重新生成按钮视图
                    regenerate_view = None
                    if user_id:
                        try:
                            user_id_int_view = int(user_id)
                            regenerate_view = RegenerateView(
                                generation_type="edit_image",
                                original_params={
                                    "prompt": edit_prompt,
                                    "aspect_ratio": aspect_ratio,
                                    "resolution": resolution,
                                    "content_rating": content_rating,
                                    "original_success_message": success_message or "",
                                },
                                user_id=user_id_int_view,
                            )
                        except (ValueError, TypeError):
                            pass
                    
                    file = discord.File(io.BytesIO(edited_image_bytes), filename="edited_image.png", spoiler=True)
                    send_kwargs = {"embed": embed, "file": file}
                    if regenerate_view:
                        send_kwargs["view"] = regenerate_view
                    await channel.send(**send_kwargs)
                    log.info("修改后的图片已直接发送到频道（Embed格式+重新生成按钮）")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}")
            
            # 返回成功信息给 AI（标记跳过后续AI回复）
            return {
                "success": True,
                "skip_ai_response": True,
                "cost": cost,
                "message": "图片已成功修改并发送给用户，预告消息已发送，无需再回复。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)
            
            # 图片编辑失败 - 编辑预告消息为失败内容
            log.warning(f"图生图返回空结果。编辑指令: {edit_prompt}")
            
            if preview_msg:
                try:
                    await preview_msg.edit(content="图片修改失败了...可能是编辑指令不够清晰或者图片格式有问题，换个描述试试吧~")
                except Exception as e:
                    log.warning(f"编辑预告消息失败: {e}")
            
            return {
                "edit_failed": True,
                "reason": "edit_failed",
                "hint": "图片修改失败了，可能是编辑指令不够清晰或者图片格式有问题。请用自己的语气告诉用户换个描述试试，或者换一张图片。"
            }
            
    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        # 编辑预告消息为失败内容
        if preview_msg:
            try:
                await preview_msg.edit(content="图片修改时发生了系统错误，请稍后再试...")
            except Exception as edit_e:
                log.warning(f"编辑预告消息失败: {edit_e}")
        
        log.error(f"图生图工具执行错误: {e}", exc_info=True)
        return {
            "edit_failed": True,
            "reason": "system_error",
            "hint": f"图片修改时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }