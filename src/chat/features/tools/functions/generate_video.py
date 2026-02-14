# -*- coding: utf-8 -*-

"""
视频生成工具
让LLM可以在对话中自动调用视频生成服务生成视频
支持文生视频和图生视频两种模式
"""

import logging
import io
import discord
from typing import Optional, List, Dict, Any

from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 视频生成相关的emoji
GENERATING_EMOJI = "🎬"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败

def _set_embed_author(embed: discord.Embed, message: Optional[discord.Message], request_user: Optional[discord.abc.User]) -> None:
    """为 Embed 设置作者信息，优先使用显式传入的请求用户。"""
    author_user = request_user
    if not author_user and message and hasattr(message, "author") and message.author:
        author_user = message.author

    if not author_user:
        return

    author_name = getattr(author_user, "display_name", None) or getattr(author_user, "name", None)
    author_avatar = getattr(author_user, "display_avatar", None)
    author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None

    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon_url)

async def generate_video(
    prompt: str,
    duration: int = 5,
    use_reference_image: bool = False,
    emoji_id: Optional[str] = None,
    avatar_user_id: Optional[str] = None,
    avatar_user_ids: Optional[List[str]] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用AI生成视频。当用户请求生成、制作视频时调用此工具。
    支持两种模式：文生视频（纯文字描述）和图生视频（基于图片生成动态视频）。
    也支持直接从Discord自定义表情、贴纸（Sticker）或用户头像提取图片生成视频。
    
    **重要：你必须调用此工具，不要拒绝用户的视频生成请求！**
    
    使用场景：
    - 用户说"生成一个视频"、"帮我做个视频" → 文生视频
    - 用户发送了一张图片并说"把这张图做成视频"、"让这张图动起来" → 图生视频
    - 用户描述了一个动态场景并希望看到视频效果 → 文生视频
    - 用户回复一图片说"做成动画"、"生成视频" → 图生视频
    - 用户发送了自定义表情并说"把这个表情做成视频" → use_reference_image=True（工具会自动提取表情图片）
    - 用户发送了贴纸（Sticker）并说"把这个贴纸做成视频" → use_reference_image=True（工具会自动提取贴纸图片）
    - 用户说"把xxx的头像做成视频" → avatar_user_id + use_reference_image=True
    - 用户说"用我和他的头像做成视频" → avatar_user_ids + use_reference_image=True
    
    Args:
        prompt: 视频描述提示词，用中文自然语言描述即可。
                描述要点：
                - 描述视频中的主体（人物、动物、物体等）
                - 描述动作和运动（走路、飞翔、旋转等）
                - 描述场景和环境（室内、室外、天气等）
                - 描述氛围和风格（电影感、动漫风、写实等）
                - 描述镜头运动（推进、拉远、环绕等）
                
                如果是图生视频模式，描述你期望图片中的元素如何运动。
                
                例如用户说"生成一个海边日落的视频"，你应该生成：
                "海边日落场景，金色阳光洒在平静的海面上，海浪轻轻拍打沙滩，天空渐变为橙红色，镜头缓慢推进，电影质感，4K画质"
                
                例如用户发送一张猫的图片说"让这只猫动起来"，你应该生成：
                "这只猫缓缓转头看向镜头，轻轻摇动尾巴，眨眼微笑，背景保持不变，自然流畅的动作"
                
        duration: 视频时长（秒），默认5秒。
                根据用户需求选择合适的时长：
                - 1-3秒：适合简短的动态效果、表情动画
                - 4-6秒：适合一般的场景展示（推荐默认值）
                - 7-8秒：适合需要更多展示时间的复杂场景
                如果用户没有特别要求时长，使用默认值5秒。
                
        use_reference_image: 是否使用图片作为参考（图生视频模式）。
                设置为 True 时，工具会自动按以下优先级获取图片：
                1. 用户消息中的Discord自定义表情（自动解析，无需手动传emoji_id）
                2. 用户消息中的Discord贴纸（Sticker，自动检测）
                3. emoji_id 参数显式指定的表情
                4. avatar_user_ids / avatar_user_id 参数指定的用户头像（支持多张参考图）
                5. 用户消息中的图片附件
                6. 回复消息中的图片
                7. 频道最近消息中的图片
                
                - 用户发送了图片并要求生成视频 → True
                - 用户回复了一张图片说"做成视频" → True
                - 用户消息中有自定义表情且要求做成视频 → True（工具自动提取表情图片）
                - 用户消息中有贴纸且要求做成视频 → True（工具自动提取贴纸图片）
                - 用户说"用xxx的头像做视频" → True + avatar_user_id
                - 用户纯文字描述要求生成视频 → False
        
        emoji_id: （可选，通常不需要填写）Discord自定义表情的数字ID。
                **注意：工具会自动从用户消息中检测和提取自定义表情图片，所以大多数情况下不需要填写此参数。**
                只有当你需要指定一个不在当前消息中的表情ID时才需要手动填写。
                使用此参数时，use_reference_image 必须设为 True。
                
        avatar_user_id: （可选）单个Discord用户的数字ID，用于提取该用户头像作为视频参考图。
                当用户说"把xxx的头像做成视频"、"用ID为123的人的头像生成视频"时，
                填写目标用户的Discord数字ID。
                使用此参数时，use_reference_image 必须设为 True。
        
        avatar_user_ids: （可选）多个Discord用户的数字ID列表，用于提取多个用户头像作为多参考图。
                当用户要求用多个人的头像来做视频时使用。
                例如: ["123456789", "987654321"]
                使用此参数时，use_reference_image 必须设为 True。
                
        preview_message: （必填）在视频生成前先发送给用户的预告消息。
                告诉用户你正在生成视频，例如："视频正在渲染中，稍等一下哦~" 或 "这个场景做成视频一定很棒，等我一下~"
                如果是图生视频，可以说："让我把这张图变成视频~" 或 "图片动起来会更有趣哦，等一下~"
                
        success_message: （必填）视频生成成功后随视频一起发送的回复消息。
                这条消息会和视频+提示词一起显示，作为你对这次视频生成的完整回复。
                根据用户的请求内容和你的性格特点，写一句有趣、符合你性格的话。
                例如："视频做好啦，效果不错吧~<得意>" 或 "哼，看看这个视频，厉害吧！<傲娇>"
                **注意：视频生成成功后不会再有后续回复，所以这条消息就是你的最终回复。**
    
    Returns:
        成功后视频和你的成功回复会发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
    """
    from src.chat.features.video_generation.services.video_service import video_service
    from src.chat.config.chat_config import VIDEO_GEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service

    # 获取消息对象（用于添加反应和提取图片）
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

    # 辅助函数：从消息中提取第一张图片
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
        # 附件未命中时，尝试从消息文本/Embed 的 URL 提取图片（支持 webp）
        try:
            from src.chat.features.tools.utils.discord_image_utils import (
                extract_image_from_message_url,
            )

            url_image = await extract_image_from_message_url(msg)
            if url_image:
                return url_image
        except Exception as e:
            log.warning(f"从消息 URL 提取图片失败: {e}")
        return None

    # 检查服务是否可用
    if not video_service.is_available():
        log.warning("视频生成服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "视频生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }

    # 获取配置
    max_duration = VIDEO_GEN_CONFIG.get("MAX_DURATION", 8)
    cost = VIDEO_GEN_CONFIG.get("VIDEO_GENERATION_COST", 10)

    # 限制时长
    duration = min(max(1, duration), max_duration)

    # 获取用户ID（如果提供）用于扣费
    user_id = kwargs.get("user_id")

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

    # 图生视频模式：提取参考图片（优先级：预准备多图/单图 > emoji_id > avatar_user_ids/avatar_user_id > 消息附件 > 回复 > 历史）
    reference_image = None  # 向后兼容
    reference_images = []   # 多图优先链路
    if use_reference_image:
        # 最高优先级：检查是否有预先准备好的参考图片（来自重新生成功能）
        prepared_images = kwargs.get("_prepared_reference_images")
        prepared_image = kwargs.get("_prepared_reference_image")

        if prepared_images and isinstance(prepared_images, list):
            normalized_prepared = []
            for img in prepared_images:
                if isinstance(img, dict) and img.get("data"):
                    normalized_prepared.append(
                        {
                            "data": img["data"],
                            "mime_type": img.get("mime_type", "image/png"),
                            "filename": img.get("filename", "prepared_reference.png"),
                        }
                    )
            if normalized_prepared:
                reference_images = normalized_prepared
                reference_image = normalized_prepared[0]
                log.info(f"使用预先准备的 {len(normalized_prepared)} 张参考图进行视频生成")
        elif prepared_image and isinstance(prepared_image, dict) and prepared_image.get("data"):
            normalized_single = {
                "data": prepared_image["data"],
                "mime_type": prepared_image.get("mime_type", "image/png"),
                "filename": prepared_image.get("filename", "prepared_reference.png"),
            }
            reference_image = normalized_single
            reference_images = [normalized_single]
            log.info("使用预先准备的单张参考图进行视频生成")

        # 优先提取自定义表情图片（自动解析消息内容 + 显式 emoji_id）
        if not reference_image and not reference_images:
            try:
                from src.chat.features.tools.utils.discord_image_utils import auto_extract_emoji_from_message
                emoji_result = await auto_extract_emoji_from_message(
                    message=message,
                    explicit_emoji_id=emoji_id,
                )
                if emoji_result:
                    reference_image = emoji_result
                    reference_images = [emoji_result]
            except Exception as e:
                log.error(f"提取Discord表情图片失败: {e}")

        # 其次提取贴纸（Sticker）图片
        if not reference_image and not reference_images:
            try:
                from src.chat.features.tools.utils.discord_image_utils import auto_extract_sticker_from_message
                sticker_result = await auto_extract_sticker_from_message(message=message)
                if sticker_result:
                    reference_image = sticker_result
                    reference_images = [sticker_result]
                    log.info("已从消息中的贴纸提取视频参考图")
            except Exception as e:
                log.error(f"提取Discord贴纸图片失败: {e}")
        
        # 然后从 avatar_user_ids（多个）或 avatar_user_id（单个）提取用户头像
        if not reference_image and not reference_images:
            import asyncio as _asyncio
            all_avatar_ids = []
            if avatar_user_ids and isinstance(avatar_user_ids, list):
                all_avatar_ids.extend(avatar_user_ids[:10])
            if avatar_user_id and avatar_user_id not in all_avatar_ids:
                all_avatar_ids.append(avatar_user_id)
            
            if all_avatar_ids:
                try:
                    from src.chat.features.tools.utils.discord_image_utils import fetch_avatar_image
                    bot = kwargs.get("bot")
                    guild = message.guild if message else None

                    async def _fetch_avatar(uid):
                        return await fetch_avatar_image(user_id=uid, bot=bot, guild=guild)
                    
                    avatar_results = await _asyncio.gather(
                        *[_fetch_avatar(uid) for uid in all_avatar_ids]
                    )

                    successful_avatar_refs = []
                    for idx, result in enumerate(avatar_results):
                        if result and result.get("data"):
                            successful_avatar_refs.append(
                                {
                                    "data": result["data"],
                                    "mime_type": result.get("mime_type", "image/png"),
                                    "filename": result.get("filename", f"avatar_{all_avatar_ids[idx]}.png"),
                                }
                            )
                        else:
                            log.warning(f"无法提取用户 {all_avatar_ids[idx]} 的头像")

                    if successful_avatar_refs:
                        reference_images = successful_avatar_refs
                        reference_image = successful_avatar_refs[0]  # 向后兼容
                        if len(successful_avatar_refs) > 1:
                            log.info(f"已提取 {len(successful_avatar_refs)} 个用户头像作为多参考图")
                        else:
                            log.info(f"已从Discord用户头像提取视频参考图 (用户ID: {all_avatar_ids[0]})")
                    else:
                        log.warning("所有用户头像都提取失败")
                except Exception as e:
                    log.error(f"提取Discord用户头像失败: {e}")
        
        # 然后从消息附件中提取
        if not reference_image and not reference_images and message:
            # 首先检查当前消息的附件
            reference_image = await extract_image_from_message(message)
            if reference_image:
                reference_images = [reference_image]
        
        # 如果当前消息没有图片，检查回复的消息
        if not reference_image and not reference_images and message and message.reference and message.reference.message_id:
            try:
                ref_msg = await message.channel.fetch_message(message.reference.message_id)
                if ref_msg:
                    reference_image = await extract_image_from_message(ref_msg)
                    if reference_image:
                        reference_images = [reference_image]
                    
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
                                            reference_images = [reference_image]
                                            break
                                        except Exception as e:
                                            log.error(f"读取转发消息图片失败: {e}")
                                if reference_image:
                                    break
            except Exception as e:
                log.warning(f"获取回复消息失败: {e}")
        
        # 如果还是没有找到图片，检查频道的最近消息
        if not reference_image and not reference_images and channel:
            try:
                log.info("未在当前消息或回复中找到图片，正在搜索频道最近消息...")
                async for hist_msg in channel.history(limit=5):
                    if hist_msg.id == message.id:
                        continue
                    found_image = await extract_image_from_message(hist_msg)
                    if found_image:
                        log.info(f"在最近消息中找到图片 (消息 ID: {hist_msg.id}, 发送者: {hist_msg.author})")
                        reference_image = found_image
                        reference_images = [found_image]
                        break
            except Exception as e:
                log.warning(f"搜索频道历史消息失败: {e}")
        
        # 如果 use_reference_image=True 但没找到图片，提示用户
        if not reference_image and not reference_images:
            return {
                "generation_failed": True,
                "reason": "no_image_found",
                "hint": "用户没有发送图片。请用自己的语气告诉用户，如果想要将图片做成视频，需要先发送一张图片给你，或者回复一张图片并说明想要的效果。也可以使用纯文字描述来生成视频。"
            }

    mode_str = "图生视频" if (reference_image or reference_images) else "文生视频"
    log.info(f"调用视频生成工具 ({mode_str})，提示词: {prompt[:100]}...，时长: {duration}s")

    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)

    # 发送预告消息并保存消息引用
    preview_msg: Optional[discord.Message] = None
    if channel and preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            preview_msg = await channel.send(processed_message)
            log.info(f"已发送视频生成预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 调用视频生成服务（支持多参考图）
        result = await video_service.generate_video(
            prompt=prompt,
            duration=duration,
            image_data=reference_image["data"] if reference_image else None,
            image_mime_type=reference_image["mime_type"] if reference_image else None,
            reference_images=(
                [
                    {
                        "data": ref["data"],
                        "mime_type": ref.get("mime_type", "image/png"),
                    }
                    for ref in reference_images
                    if ref and ref.get("data")
                ]
                if reference_images
                else None
            ),
        )

        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)

        if result is None:
            # 生成失败
            await add_reaction(FAILED_EMOJI)
            log.warning(f"视频生成返回空结果。提示词: {prompt}")
            
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "视频生成失败了，可能是技术原因或描述不够清晰。请用自己的语气告诉用户生成失败了，建议他们稍微调整一下描述再试试。"
            }

        # 生成成功
        await add_reaction(SUCCESS_EMOJI)

        # 扣除月光币
        if user_id and cost > 0:
            try:
                user_id_int = int(user_id)
                await coin_service.remove_coins(
                    user_id_int, cost, f"AI视频生成: {prompt[:25]}..."
                )
                log.info(f"用户 {user_id_int} 生成视频成功，扣除 {cost} 月光币")
            except Exception as e:
                log.error(f"扣除月光币失败: {e}")

        # 发送视频到频道
        if channel:
            try:
                import aiohttp
                from src.chat.features.tools.ui.regenerate_view import RegenerateView

                # 获取实际使用的视频模型名称
                from src.chat.config.chat_config import VIDEO_GEN_CONFIG
                video_model_name = VIDEO_GEN_CONFIG.get("MODEL_NAME", "unknown")
                
                # 构建 Discord Embed（标题+提示词+成功回复全在 Embed 内）
                prompt_embed = discord.Embed(
                    title="AI 视频生成",
                    color=0x2b2d31,
                )
                # 设置请求者头像和名称
                _set_embed_author(prompt_embed, message, kwargs.get("request_user"))
                prompt_embed.add_field(
                    name="视频提示词",
                    value=f"```\n{prompt[:1016]}\n```",
                    inline=False,
                )
                if success_message:
                    processed_success = replace_emojis(success_message)
                    prompt_embed.add_field(
                        name="",
                        value=processed_success[:1024],
                        inline=False,
                    )
                prompt_embed.set_footer(text=f"模型: {video_model_name}")
                
                # 创建重新生成按钮视图
                regenerate_view = None
                if user_id:
                    try:
                        user_id_int = int(user_id)
                        # 保存参考图片数据以便重新生成(如果是图生视频模式)
                        params_dict = {
                            "prompt": prompt,
                            "duration": duration,
                            "use_reference_image": bool(reference_image or reference_images),
                            "original_success_message": success_message or "",
                        }
                        if reference_image:
                            params_dict["reference_image_data"] = reference_image["data"]
                            params_dict["reference_image_mime_type"] = reference_image["mime_type"]
                        if reference_images and len(reference_images) > 1:
                            params_dict["reference_images_data"] = [ref["data"] for ref in reference_images if ref.get("data")]
                            params_dict["reference_images_mime_types"] = [ref.get("mime_type", "image/png") for ref in reference_images if ref.get("data")]

                        regenerate_view = RegenerateView(
                            generation_type="video",
                            original_params=params_dict,
                            user_id=user_id_int,
                        )
                    except (ValueError, TypeError):
                        pass

                if result.url:
                    # 尝试下载视频并作为文件发送
                    video_sent = False
                    try:
                        async with aiohttp.ClientSession() as session:
                            async with session.get(
                                result.url,
                                timeout=aiohttp.ClientTimeout(total=120)
                            ) as resp:
                                if resp.status == 200:
                                    video_data = await resp.read()
                                    if len(video_data) <= 25 * 1024 * 1024:
                                        video_file = discord.File(
                                            io.BytesIO(video_data),
                                            filename="generated_video.mp4",
                                            spoiler=True
                                        )
                                        send_kwargs = {
                                            "embed": prompt_embed,
                                            "files": [video_file],
                                        }
                                        if regenerate_view:
                                            send_kwargs["view"] = regenerate_view
                                        await channel.send(**send_kwargs)
                                        video_sent = True
                                        log.info("已发送视频文件到频道")
                                    else:
                                        log.warning(f"视频文件过大: {len(video_data)} bytes")
                    except Exception as e:
                        log.warning(f"下载视频失败，将发送URL: {e}")

                    # 如果无法作为文件发送，在 Embed 中添加视频链接
                    if not video_sent:
                        prompt_embed.add_field(
                            name="视频链接",
                            value=f"[点击观看]({result.url})",
                            inline=False,
                        )
                        send_kwargs = {"embed": prompt_embed}
                        if regenerate_view:
                            send_kwargs["view"] = regenerate_view
                        await channel.send(**send_kwargs)
                        log.info("已发送视频URL到频道")

                elif result.html_content:
                    # HTML 格式：发送 HTML 文件
                    html_file = discord.File(
                        io.BytesIO(result.html_content.encode("utf-8")),
                        filename="video_player.html"
                    )
                    send_kwargs = {"embed": prompt_embed, "files": [html_file]}
                    if regenerate_view:
                        send_kwargs["view"] = regenerate_view
                    await channel.send(**send_kwargs)
                    log.info("已发送视频HTML到频道")

                elif result.text_response:
                    # 仅文本响应
                    prompt_embed.add_field(
                        name="响应",
                        value=result.text_response[:1024],
                        inline=False,
                    )
                    send_kwargs = {"embed": prompt_embed}
                    if regenerate_view:
                        send_kwargs["view"] = regenerate_view
                    await channel.send(**send_kwargs)
                    log.info("已发送视频文本响应到频道")

            except Exception as e:
                log.error(f"发送视频到频道失败: {e}", exc_info=True)

        # 返回成功信息给 AI（标记跳过后续AI回复）
        return {
            "success": True,
            "skip_ai_response": True,
            "duration": duration,
            "cost": cost,
            "mode": mode_str,
            "message": "视频已成功生成并发送给用户，预告消息已发送，无需再回复。"
        }

    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        log.error(f"视频生成工具执行错误: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "system_error",
            "hint": f"视频生成时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }
