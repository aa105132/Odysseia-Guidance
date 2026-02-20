# -*- coding: utf-8 -*-

"""
语音生成工具
让 LLM 可以在对话中自动调用语音合成服务生成语音并发送到频道。
"""

import io
import logging
from typing import Optional

import discord

from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 语音生成相关反应
GENERATING_EMOJI = "🎙️"
SUCCESS_EMOJI = "✅"
FAILED_EMOJI = "❌"


def _set_embed_author(
    embed: discord.Embed,
    message: Optional[discord.Message],
    request_user: Optional[discord.abc.User],
) -> None:
    """为 Embed 设置作者信息，优先使用显式传入的请求用户。"""
    author_user = request_user
    if not author_user and message and hasattr(message, "author") and message.author:
        author_user = message.author

    if not author_user:
        return

    author_name = getattr(author_user, "display_name", None) or getattr(
        author_user, "name", None
    )
    author_avatar = getattr(author_user, "display_avatar", None)
    author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None

    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon_url)


def _is_explicit_voice_request(message: Optional[discord.Message]) -> bool:
    """粗粒度判断：用户是否明确提出了语音诉求。"""
    if not message:
        return False
    content = (getattr(message, "content", "") or "").strip().lower()
    if not content:
        return False

    keywords = [
        "语音",
        "发语音",
        "语音回复",
        "语音消息",
        "念出来",
        "读出来",
        "用声音",
        "voice",
        "tts",
    ]
    return any(k in content for k in keywords)


async def generate_voice(
    text: str,
    voice_type: Optional[str] = None,
    speed_ratio: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    pitch_ratio: Optional[float] = None,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    force_send: bool = False,
    **kwargs,
) -> dict:
    """
    使用 AI 语音合成服务将文本转换为语音并发送给用户。

    使用场景：
    - 这个工具是「可选工具」，不是每轮都必须调用。
    - 当你（月月）想用语音表达时，可以主动调用（想发就发，不想发就不用发）。
    - 当用户明确说“发语音”“语音回复”“念出来”等需求时，也可以调用。
    - 支持通过参数覆盖音色、语速、音量、音调。

    Args:
        text: 要合成语音的文本内容。
        voice_type: （可选）音色名称，留空使用后台默认音色。
        speed_ratio: （可选）语速倍率，建议 0.2~3.0。
        volume_ratio: （可选）音量倍率，建议 0.2~3.0。
        pitch_ratio: （可选）音调倍率，建议 0.1~3.0。
        preview_message: （建议填写）生成语音前先发送给用户的预告消息。
        success_message: （建议填写）语音发送成功后附带给用户的文字消息。
        force_send: 是否强制执行发送。默认 False。通常无需设置，除非你明确要无条件发语音。

    Returns:
        成功时会把音频文件直接发送到频道，并返回 skip_ai_response=True，
        表示无需再追加文本回复。
        计费策略：默认仅在“用户明确要求语音”时扣费；月月主动语音默认不扣费。
    """
    from src.chat.config.chat_config import VOICE_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.features.voice_generation.services.voice_service import voice_service

    text = (text or "").strip()
    if not text:
        return {
            "generation_failed": True,
            "reason": "empty_text",
            "hint": "用户没有提供可朗读的内容。请让用户给出要转换为语音的文本。",
        }

    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel") or (message.channel if message else None)
    user_id = kwargs.get("user_id")
    user_requested = bool(force_send) or _is_explicit_voice_request(message)

    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")

    async def remove_reaction(emoji: str):
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")

    if not voice_service.is_available():
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "语音服务当前不可用。请用你自己的语气告诉用户先检查语音配置或稍后重试。",
        }

    # 扣费配置
    cost = int(VOICE_CONFIG.get("VOICE_GENERATION_COST", 3))
    parsed_user_id: Optional[int] = None
    if user_id is not None:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    should_charge = parsed_user_id is not None and cost > 0 and user_requested
    if should_charge:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            return {
                "generation_failed": True,
                "reason": "insufficient_balance",
                "cost": cost,
                "balance": balance,
                "hint": f"用户月光币不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够。",
            }

    # 添加“正在生成”反应
    await add_reaction(GENERATING_EMOJI)

    # 发送预告消息
    if channel and preview_message:
        try:
            await channel.send(replace_emojis(preview_message))
        except Exception as e:
            log.warning(f"发送语音预告消息失败: {e}")

    try:
        result = await voice_service.generate_voice(
            text=text,
            voice_type=voice_type,
            speed_ratio=speed_ratio,
            volume_ratio=volume_ratio,
            pitch_ratio=pitch_ratio,
            user_id=str(parsed_user_id) if parsed_user_id is not None else None,
        )

        await remove_reaction(GENERATING_EMOJI)

        if not result or not result.audio_bytes:
            await add_reaction(FAILED_EMOJI)
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "语音生成失败了。请用自己的语气告诉用户稍后再试，或建议更换音色/文本后重试。",
            }

        # Discord 单文件大小上限保护（常见 25MB）
        if len(result.audio_bytes) > 25 * 1024 * 1024:
            await add_reaction(FAILED_EMOJI)
            return {
                "generation_failed": True,
                "reason": "file_too_large",
                "hint": "生成的语音文件过大，无法发送。请建议用户缩短文本后重试。",
            }

        await add_reaction(SUCCESS_EMOJI)

        # 扣币（仅成功后，且仅用户明确要求语音时）
        if should_charge:
            try:
                await coin_service.remove_coins(
                    parsed_user_id, cost, f"AI语音生成: {text[:25]}..."
                )
            except Exception as e:
                log.error(f"扣除月光币失败: {e}")

        # 发送语音文件
        if channel:
            try:
                embed = discord.Embed(
                    title="AI 语音合成",
                    color=0x2B2D31,
                )
                _set_embed_author(embed, message, kwargs.get("request_user"))
                embed.add_field(
                    name="文本",
                    value=f"```\n{text[:1016]}\n```",
                    inline=False,
                )
                if success_message:
                    embed.add_field(
                        name="\u200b",
                        value=replace_emojis(success_message)[:1024],
                        inline=False,
                    )
                embed.set_footer(
                    text=f"供应商: {result.provider} | 模型: {result.model_name} | 音色: {result.voice_type}"
                )

                filename = f"generated_voice.{result.file_ext or 'mp3'}"
                audio_file = discord.File(
                    io.BytesIO(result.audio_bytes),
                    filename=filename,
                )
                await channel.send(embed=embed, files=[audio_file])
                log.info(
                    f"语音生成成功并已发送: provider={result.provider}, model={result.model_name}, ext={result.file_ext}"
                )
            except Exception as e:
                log.error(f"发送语音到频道失败: {e}", exc_info=True)

        return {
            "success": True,
            "skip_ai_response": True,
            "cost": cost if should_charge else 0,
            "charged": bool(should_charge),
            "requested_by_user": bool(user_requested),
            "provider": result.provider,
            "model_name": result.model_name,
            "voice_type": result.voice_type,
            "audio_format": result.file_ext,
            "message": "语音已成功生成并发送给用户，无需再回复。",
        }

    except Exception as e:
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        log.error(f"语音生成工具执行错误: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "system_error",
            "hint": "语音生成时发生了系统错误。请用自己的语气安慰用户，并建议稍后重试。",
        }