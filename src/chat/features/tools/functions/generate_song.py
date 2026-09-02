# -*- coding: utf-8 -*-

"""
音频生成工具（唱歌/哼唱）
使用 ElevenLabs V3 模型，固定使用银月克隆音色。
歌词前加 ♪ 符号，V3 模型会以唱歌方式生成音频。
"""

import base64
import io
import logging
import os
from typing import Optional

import aiohttp
import discord

from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

# ElevenLabs 配置
_ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
_ELEVENLABS_VOICE_ID = "l09yLd0oPD6mye4jJFg2"  # 银月克隆音色
_ELEVENLABS_MODEL = "eleven_v3"
_ELEVENLABS_SETTINGS = {
    "stability": 0.2,
    "similarity_boost": 0.95,
    "style": 0.3,
    "use_speaker_boost": True,
}

_ELEVENLABS_TTS_URL = f"https://api.elevenlabs.io/v1/text-to-speech/{_ELEVENLABS_VOICE_ID}?output_format=pcm_44100"


def _clean_emoji(text: str) -> str:
    """移除表情占位符。"""
    import re
    return re.sub(r":[a-zA-Z0-9_]+:", "", text).strip()


@tool_metadata(
    name="唱歌",
    description="用银月的音色唱歌或哼唱。传入歌词内容，工具会以唱歌方式生成音频并发送到频道。",
    emoji="🎵",
    category="娱乐",
)
async def generate_song(
    text_prompt: str,
    **kwargs,
) -> dict:
    """
    用银月的音色唱歌或哼唱，生成音频并发送到当前频道。

    使用场景：
    - 用户说"唱首歌""来一首""哼一段"等需求时调用。
    - 用户指定某首歌时，先用 web_search 搜索歌词，再调用本工具。

    重要：text_prompt 的写法直接决定生成效果！必须综合使用以下三种控制方式：

    1. ♪ 符号 — 必须加在歌词前后，让 V3 模型知道这是要唱的，不是念的。
       格式：♪ 歌词内容 ♪

    2. 括号描述唱法 — 在歌词前用中文括号描述具体的唱法、情绪、语气。
       例如：（温柔轻声哼唱）（深情地唱）（欢快地唱）（低声呢喃）
       这会直接影响 V3 模型的演唱风格和情感表达。

    3. XML情感标签 — 在最外层用 XML 标签控制整体语气和情感基调。
       可用标签：<singing softly with emotion>...</singing softly with emotion>
                 <gentle humming>...</gentle humming>
                 <passionate singing>...</passionate singing>
                 <soft whisper singing>...</soft whisper singing>

    综合格式（三种方式同时使用效果最好）：
    <singing softly with emotion>（温柔轻声哼唱）♪ 月亮代表我的心，你问我爱你有多深，我爱你有几分 ♪ 我的情也真，我的爱也真，月亮代表我的心 ♪</singing softly with emotion>

    更多示例：
    <gentle humming>（低声呢喃）♪ 一闪一闪亮晶晶，满天都是小星星 ♪</gentle humming>
    <passionate singing>（深情地唱）♪ 后来我总算学会了如何去爱，可惜你早已远去消失在人海 ♪</passionate singing>

    禁止：
    - 不要加 [Pop] [Soft] 等方括号音乐风格标签（会触发内容审核）。
    - 不要只传一句歌词，尽量传完整段落（至少4句），V3 需要足够长的歌词才能进入唱歌模式。
    - 不要解释歌词含义，直接传歌词原文。
    - 不要去掉 ♪ 符号，否则会变成念歌词（捧读）。

    Args:
        text_prompt: 要唱的内容，综合使用 ♪ 符号 + 括号描述 + XML情感标签。
            例如：<singing softly with emotion>（温柔轻声哼唱）♪ 月亮代表我的心，你问我爱你有多深 ♪ 我的爱也真，月亮代表我的心 ♪</singing softly with emotion>

    Returns:
        成功发送音频时返回 skip_ai_response=True。
    """
    raw_prompt = str(text_prompt or "").strip()
    if not raw_prompt:
        return {
            "error": True,
            "reason": "empty_prompt",
            "hint": "歌词内容不能为空。",
        }

    if not _ELEVENLABS_API_KEY:
        return {
            "error": True,
            "reason": "no_api_key",
            "hint": "ELEVENLABS_API_KEY 环境变量未配置。",
        }

    # 清理表情
    clean_prompt = _clean_emoji(raw_prompt)

    # 确保有 ♪ 符号
    if "♪" not in clean_prompt:
        clean_prompt = f"♪ {clean_prompt} ♪"

    message = kwargs.get("message")
    channel = kwargs.get("channel") or (getattr(message, "channel", None) if message else None)

    if message is None and channel is None:
        return {
            "error": True,
            "reason": "no_channel",
            "hint": "无法找到频道上下文。",
        }

    try:
        # 调用 ElevenLabs TTS API
        timeout = aiohttp.ClientTimeout(total=120, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {
                "text": clean_prompt,
                "model_id": _ELEVENLABS_MODEL,
                "voice_settings": _ELEVENLABS_SETTINGS,
            }

            async with session.post(
                _ELEVENLABS_TTS_URL,
                headers={
                    "xi-api-key": _ELEVENLABS_API_KEY,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json=payload,
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    log.error(f"ElevenLabs API 返回 {response.status}: {error_text[:300]}")
                    return {
                        "error": True,
                        "reason": "api_error",
                        "hint": f"音频生成失败（HTTP {response.status}）。可能是歌词内容触发了内容审核。",
                    }

                audio_bytes = await response.read()

                if not audio_bytes or len(audio_bytes) < 1000:
                    return {
                        "error": True,
                        "reason": "empty_audio",
                        "hint": "音频生成返回空数据。",
                    }

                log.info(f"ElevenLabs 唱歌生成成功: {len(audio_bytes)} bytes, prompt={clean_prompt[:80]}")

                # PCM → ogg_opus 转换（Discord 原生语音消息需要 ogg/opus）
                import subprocess as _sp
                proc = _sp.run(
                    [
                        "ffmpeg", "-y",
                        "-f", "s16le", "-ar", "44100", "-ac", "1",
                        "-i", "pipe:0",
                        "-c:a", "libopus", "-b:a", "64k",
                        "-f", "ogg", "pipe:1",
                    ],
                    input=audio_bytes,
                    capture_output=True,
                    timeout=15,
                )
                if proc.returncode != 0 or not proc.stdout:
                    log.error(f"唱歌 PCM→OGG 转换失败: {proc.stderr.decode()[-300:]}")
                    return {
                        "error": True,
                        "reason": "ffmpeg_failed",
                        "hint": "音频格式转换失败。",
                    }

                ogg_bytes = proc.stdout
                log.info(f"ElevenLabs 唱歌 OGG 转换成功: {len(ogg_bytes)} bytes")

                # 用原生语音消息发送
                audio_file = discord.File(
                    fp=io.BytesIO(ogg_bytes),
                    filename="yueyue_singing.ogg",
                )

                if message is not None:
                    await message.reply(file=audio_file, mention_author=False)
                elif channel is not None:
                    await channel.send(file=audio_file)

                return {
                    "success": True,
                    "skip_ai_response": True,
                    "voice_text": clean_prompt,
                    "audio_size": len(audio_bytes),
                    "message": "唱歌音频已发送到频道。",
                }

    except aiohttp.ClientError as exc:
        log.error(f"ElevenLabs 网络错误: {exc}", exc_info=True)
        return {
            "error": True,
            "reason": "network_error",
            "hint": f"网络错误：{exc}",
        }
    except Exception as exc:
        log.error(f"ElevenLabs 唱歌工具失败: {exc}", exc_info=True)
        return {
            "error": True,
            "reason": "unknown_error",
            "hint": f"生成失败：{exc}",
        }
