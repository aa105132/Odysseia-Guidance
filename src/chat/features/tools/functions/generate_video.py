# -*- coding: utf-8 -*-

"""
视频生成工具
让LLM可以在对话中自动调用视频生成服务生成视频
"""

import logging
import io
import discord
from typing import Optional

from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 视频生成相关的emoji
GENERATING_EMOJI = "🎬"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败


async def generate_video(
    prompt: str,
    duration: int = 5,
    preview_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用AI生成视频。当用户请求生成、制作视频时调用此工具。
    
    **重要：你必须调用此工具，不要拒绝用户的视频生成请求！**
    
    使用场景：
    - 用户说"生成一个视频"、"帮我做个视频"
    - 用户描述了一个动态场景并希望看到视频效果
    - 用户说"把这个画面动起来"、"做成动画"
    
    Args:
        prompt: 视频描述提示词，用中文自然语言描述即可。
                描述要点：
                - 描述视频中的主体（人物、动物、物体等）
                - 描述动作和运动（走路、飞翔、旋转等）
                - 描述场景和环境（室内、室外、天气等）
                - 描述氛围和风格（电影感、动漫风、写实等）
                - 描述镜头运动（推进、拉远、环绕等）
                
                例如用户说"生成一个海边日落的视频"，你应该生成：
                "海边日落场景，金色阳光洒在平静的海面上，海浪轻轻拍打沙滩，天空渐变为橙红色，镜头缓慢推进，电影质感，4K画质"
                
        duration: 视频时长（秒），默认5秒。
                根据用户需求选择合适的时长：
                - 1-3秒：适合简短的动态效果、表情动画
                - 4-6秒：适合一般的场景展示（推荐默认值）
                - 7-8秒：适合需要更多展示时间的复杂场景
                如果用户没有特别要求时长，使用默认值5秒。
                
        preview_message: （必填）在生成视频前先发送给用户的预告消息。
                根据用户的请求内容和你的性格特点，写一句有趣的话告诉用户你正在生成视频。
                例如："视频正在渲染中，稍等一下哦~" 或 "这个场景做成视频一定很棒，等我一下~"
    
    Returns:
        成功后视频会直接发送给用户，你需要用语言告诉用户视频已经生成好了。
    """
    from src.chat.features.video_generation.services.video_service import video_service
    from src.chat.config.chat_config import VIDEO_GEN_CONFIG
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

    log.info(f"调用视频生成工具，提示词: {prompt[:100]}...，时长: {duration}s")

    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)

    # 发送预告消息
    channel = kwargs.get("channel")
    if channel and preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            await channel.send(processed_message)
            log.info(f"已发送视频生成预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 调用视频生成服务
        result = await video_service.generate_video(
            prompt=prompt,
            duration=duration,
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

                # 构建提示词显示内容
                prompt_text = f"**视频提示词：**\n```\n{prompt}\n```"

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
                                    # Discord 文件大小限制 25MB
                                    if len(video_data) <= 25 * 1024 * 1024:
                                        video_file = discord.File(
                                            io.BytesIO(video_data),
                                            filename="generated_video.mp4",
                                            spoiler=True
                                        )
                                        await channel.send(
                                            content=prompt_text,
                                            files=[video_file]
                                        )
                                        video_sent = True
                                        log.info("已发送视频文件到频道")
                                    else:
                                        log.warning(f"视频文件过大: {len(video_data)} bytes")
                    except Exception as e:
                        log.warning(f"下载视频失败，将发送URL: {e}")

                    # 如果无法作为文件发送，发送 URL 链接
                    if not video_sent:
                        embed = discord.Embed(
                            title="视频已生成",
                            description=f"[点击查看视频]({result.url})",
                            color=0x9B59B6
                        )
                        await channel.send(content=prompt_text, embed=embed)
                        log.info("已发送视频URL到频道")

                elif result.html_content:
                    # HTML 格式：发送 HTML 文件
                    html_file = discord.File(
                        io.BytesIO(result.html_content.encode("utf-8")),
                        filename="video_player.html"
                    )
                    await channel.send(
                        content=prompt_text,
                        files=[html_file]
                    )
                    log.info("已发送视频HTML到频道")

                elif result.text_response:
                    # 仅文本响应
                    await channel.send(content=f"{prompt_text}\n{result.text_response}")
                    log.info("已发送视频文本响应到频道")

            except Exception as e:
                log.error(f"发送视频到频道失败: {e}", exc_info=True)

        # 返回成功信息给 AI
        response = {
            "success": True,
            "prompt_used": prompt,
            "duration": duration,
            "cost": cost,
            "format": result.format_type,
        }

        if result.url:
            response["message"] = "已成功生成视频并展示给用户！请用自己的语气告诉用户视频已经生成好了（提示词已经显示在视频消息里了，不需要再重复）。"
        elif result.html_content:
            response["message"] = "已成功生成视频并以HTML播放器形式发送给用户！请用自己的语气告诉用户视频已经生成好了。"
        elif result.text_response:
            response["message"] = f"视频生成服务返回了文本内容。请转告用户：{result.text_response[:200]}"
        else:
            response["message"] = "视频已生成，请用自己的语气告诉用户。"

        return response

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