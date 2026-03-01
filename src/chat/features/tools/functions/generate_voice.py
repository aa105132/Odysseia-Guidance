# -*- coding: utf-8 -*-

"""
语音生成工具
让 LLM 可以在对话中自动调用语音合成服务生成语音并发送到频道。
"""

import base64
import io
import logging
import re
from typing import Optional

import discord

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


def _strip_emoji_placeholders(text: str) -> str:
    """
    移除文本中的“表情占位符”，避免 TTS 读出类似 <微笑> / <生气>。
    仅处理单行尖括号占位，尽量避免误删长段文本。
    """
    if not text:
        return ""

    cleaned = re.sub(r"<[^<>\r\n]{1,24}>", "", text)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _estimate_ogg_opus_duration_seconds(audio_bytes: bytes) -> Optional[float]:
    """
    粗略估算 OGG/Opus 时长（秒）。
    - 通过读取 Ogg Page 的 granule position 计算，采样率按 Opus 48kHz。
    - 失败时返回 None，由调用方使用兜底时长。
    """
    if not audio_bytes or len(audio_bytes) < 27:
        return None

    try:
        offset = 0
        max_granule = -1

        while offset + 27 <= len(audio_bytes):
            if audio_bytes[offset : offset + 4] != b"OggS":
                break

            page_segments = audio_bytes[offset + 26]
            seg_table_start = offset + 27
            seg_table_end = seg_table_start + page_segments
            if seg_table_end > len(audio_bytes):
                break

            segment_table = audio_bytes[seg_table_start:seg_table_end]
            body_size = sum(segment_table)
            page_end = seg_table_end + body_size
            if page_end > len(audio_bytes):
                break

            granule = int.from_bytes(
                audio_bytes[offset + 6 : offset + 14], "little", signed=False
            )
            if granule > max_granule:
                max_granule = granule

            offset = page_end

        if max_granule <= 0:
            return None

        duration = max_granule / 48000.0
        return max(0.1, min(60 * 60, float(duration)))
    except Exception:
        return None


def _build_voice_waveform_base64(audio_bytes: bytes, points: int = 64) -> str:
    """
    生成 Discord 语音消息需要的 waveform（base64 字符串）。
    这里使用轻量近似算法：对音频字节分段取均值，构造 0~255 振幅序列。
    """
    points = max(16, min(256, int(points)))
    if not audio_bytes:
        return base64.b64encode(bytes([128] * points)).decode("ascii")

    chunk_size = max(1, len(audio_bytes) // points)
    amplitudes = []
    for idx in range(points):
        start = idx * chunk_size
        end = len(audio_bytes) if idx == points - 1 else min(len(audio_bytes), (idx + 1) * chunk_size)
        chunk = audio_bytes[start:end]
        if not chunk:
            amplitudes.append(0)
            continue
        amplitudes.append(int(sum(chunk) / len(chunk)) & 0xFF)

    return base64.b64encode(bytes(amplitudes)).decode("ascii")


class _VoiceMessageFile(discord.File):
    """带 voice metadata 的文件对象，用于 Discord 原生语音消息样式。"""

    def __init__(
        self,
        fp,
        *,
        filename: str,
        duration_secs: float,
        waveform: str,
    ):
        super().__init__(fp, filename=filename)
        self._duration_secs = max(0.1, float(duration_secs))
        self._waveform = waveform

    def to_dict(self, index: int) -> dict:
        payload = super().to_dict(index)
        payload["duration_secs"] = round(self._duration_secs, 3)
        payload["waveform"] = self._waveform
        return payload


async def _send_native_voice_message(
    channel: discord.abc.Messageable,
    *,
    audio_bytes: bytes,
    filename: str,
    duration_secs: float,
    reference: Optional[discord.Message] = None,
) -> None:
    """
    走底层 HTTP 参数发送"原生语音消息"：
    - 设置 MessageFlags.is_voice_message
    - 在附件里附带 duration_secs + waveform
    """
    from discord.http import handle_message_parameters
    from discord.message import MessageFlags

    state = getattr(channel, "_state", None)
    channel_id = getattr(channel, "id", None)
    if state is None or channel_id is None:
        raise RuntimeError("channel 不支持原生语音消息发送（缺少 _state/id）")

    waveform = _build_voice_waveform_base64(audio_bytes)
    voice_file = _VoiceMessageFile(
        io.BytesIO(audio_bytes),
        filename=filename,
        duration_secs=duration_secs,
        waveform=waveform,
    )

    # 兼容不同 discord.py 发行版：直接使用 is_voice_message 对应 bit(8192)，
    # 避免某些版本缺少 flags.is_voice_message 属性 setter。
    flags = MessageFlags._from_value(8192)

    # 构造回复引用
    msg_reference = None
    if reference is not None:
        msg_reference = discord.MessageReference.from_message(reference)

    with handle_message_parameters(
        content=None,
        attachments=[voice_file],
        flags=flags,
        allowed_mentions=state.allowed_mentions,
        message_reference=msg_reference,
    ) as params:
        await state.http.send_message(channel_id, params=params)


async def generate_voice(
    text: str,
    voice_type: Optional[str] = None,
    speed_ratio: Optional[float] = None,
    pitch_ratio: Optional[float] = None,
    *,
    emotion: Optional[str] = None,
    enable_emotion: Optional[bool] = None,
    emotion_scale: Optional[float] = None,
    force_send: bool = False,
    send_text_after_voice: bool = False,
    **kwargs,
) -> dict:
    """
    使用 AI 语音合成服务将文本转换为语音并发送给用户。

    使用场景：
    - 这个工具是「可选工具」，不是每轮都必须调用。
    - 当你（月月）想用语音表达时，可以主动调用（想发就发，不想发就不用发）。
    - 当用户明确说“发语音”“语音回复”“念出来”等需求时，也可以调用。
    - 支持通过参数覆盖音色、语速、音调、情感风格（音量由系统固定配置控制，不允许工具层动态改）。

    Args:
        text: 要合成语音的文本内容。
        voice_type: （可选）音色名称，留空使用后台默认音色。
        speed_ratio: （可选）语速倍率，建议 0.2~3.0。
        pitch_ratio: （可选）音调倍率，建议 0.1~3.0。
        emotion: （可选）情感风格。豆包可用情感建议如下：
            中文音色：happy(开心), sad(悲伤), angry(生气), surprised(惊讶), fear(恐惧), hate(厌恶),
            excited(激动), coldness(冷漠), neutral(中性), depressed(沮丧), lovey-dovey(撒娇),
            shy(害羞), comfort(安慰鼓励), tension(咆哮/焦急), tender(温柔),
            storytelling(讲故事/自然讲述), radio(情感电台), magnetic(磁性), advertising(广告营销),
            vocal_fry / vocal-fry(气泡音), asmr(低语), news(新闻播报), entertainment(娱乐八卦), dialect(方言)。
            英文音色：neutral(中性), happy(愉悦), angry(愤怒), sad(悲伤), excited(兴奋),
            chat(对话/闲聊), asmr(低语), warm(温暖), affectionate(深情), authoritative(权威)。
        enable_emotion: （可选）情感增强开关。
        emotion_scale: （可选）情感强度，建议 1.0~5.0（推荐 4.0）。
        force_send: 是否强制执行发送。默认 False。通常无需设置，除非你明确要无条件发语音。
        send_text_after_voice: 语音发送成功后，是否补发一条同文文本消息。默认 False。

    Returns:
        成功时会把音频文件直接发送到频道，并返回 skip_ai_response=True。
        语音发送成功后，可按 send_text_after_voice 决定是否补发同文文本。
        计费策略：默认仅在“用户明确要求语音”时扣费；月月主动语音默认不扣费。
    """
    from src.chat.config.chat_config import VOICE_CONFIG
    from src.chat.features.voice_generation.services.voice_service import voice_service

    text = _strip_emoji_placeholders((text or "").strip())
    if not text:
        return {
            "generation_failed": True,
            "reason": "empty_text",
            "hint": "用户没有提供可朗读的内容（或文本仅包含表情占位符）。请让用户给出要转换为语音的文本。",
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

    # 保留 user_id 透传（用于 provider 侧 uid 标识），但不再做余额拦截/扣费
    parsed_user_id: Optional[int] = None
    if user_id is not None:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    selected_emotion = str(emotion or "").strip() or None
    selected_enable_emotion = bool(enable_emotion) if enable_emotion is not None else None
    selected_emotion_scale: Optional[float] = None
    if emotion_scale is not None:
        try:
            selected_emotion_scale = float(emotion_scale)
            if selected_emotion_scale < 1.0:
                selected_emotion_scale = 1.0
            elif selected_emotion_scale > 5.0:
                selected_emotion_scale = 5.0
        except (TypeError, ValueError):
            log.warning("emotion_scale 非法，已忽略: %s", emotion_scale)
            selected_emotion_scale = None

    # 添加“正在生成”反应
    await add_reaction(GENERATING_EMOJI)

    should_send_text_after_voice = bool(send_text_after_voice)

    try:
        result = await voice_service.generate_voice(
            text=text,
            voice_type=voice_type,
            speed_ratio=speed_ratio,
            pitch_ratio=pitch_ratio,
            emotion=selected_emotion,
            enable_emotion=selected_enable_emotion,
            emotion_scale=selected_emotion_scale,
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

        text_sent_after_voice = False

        # 发送语音文件（优先原生语音消息样式；失败自动回退普通附件）
        if channel:
            try:
                sent_native_voice = False
                file_ext = (result.file_ext or "").strip().lower()

                # Discord 原生语音消息主要针对 ogg/opus
                if file_ext in {"ogg", "opus"}:
                    native_filename = "voice-message.ogg"
                    estimated_duration = _estimate_ogg_opus_duration_seconds(result.audio_bytes)
                    if estimated_duration is None:
                        # 兜底：按文本长度给一个近似时长（仅用于展示）
                        estimated_duration = max(1.0, min(300.0, len(text) / 6.0))

                    try:
                        await _send_native_voice_message(
                            channel,
                            audio_bytes=result.audio_bytes,
                            filename=native_filename,
                            duration_secs=estimated_duration,
                            reference=message,
                        )
                        sent_native_voice = True
                        log.info(
                            f"语音已按原生语音消息样式发送: provider={result.provider}, model={result.model_name}, voice={result.voice_type}"
                        )
                    except Exception as native_send_error:
                        log.warning(
                            f"原生语音消息发送失败，将回退普通附件: {native_send_error}"
                        )

                # 回退：普通附件消息（不再附带大卡片）
                if not sent_native_voice:
                    filename = f"generated_voice.{result.file_ext or 'mp3'}"
                    audio_file = discord.File(
                        io.BytesIO(result.audio_bytes),
                        filename=filename,
                    )
                    if message:
                        await message.reply(file=audio_file, mention_author=False)
                    else:
                        await channel.send(file=audio_file)
                    log.info(
                        f"语音已按普通附件发送: provider={result.provider}, model={result.model_name}, ext={result.file_ext}"
                    )

                # 可选：发送同文文本
                if should_send_text_after_voice:
                    try:
                        await channel.send(text)
                        text_sent_after_voice = True
                        log.info("语音后已补发同文文本。")
                    except Exception as text_send_error:
                        log.warning(f"语音发送成功，但补发同文文本失败: {text_send_error}")

            except Exception as e:
                log.error(f"发送语音到频道失败: {e}", exc_info=True)

        return {
            "success": True,
            "skip_ai_response": True,
            "cost": 0,
            "charged": False,
            "requested_by_user": bool(user_requested),
            "provider": result.provider,
            "model_name": result.model_name,
            "voice_type": result.voice_type,
            "audio_format": result.file_ext,
            "requested_text_after_voice": bool(should_send_text_after_voice),
            "text_sent_after_voice": bool(text_sent_after_voice),
            "message": (
                "语音已成功生成并发送给用户。"
                + ("已补发同文文本。" if text_sent_after_voice else "未补发同文文本。")
            ),
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