# -*- coding: utf-8 -*-

"""
创作音频工具
使用火山引擎 SeedAudio API 合成音频，支持 TTS、唱歌、哼唱、音效等。
银月音色 (S_9Q5eU8Ec2) 为默认音色。
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

# 火山引擎 SeedAudio 配置
_SEED_AUDIO_API_KEY = os.getenv("SEED_AUDIO_API_KEY", "")
_SEED_AUDIO_APP_ID = os.getenv("VOICE_APP_ID", "1094070896")
_SEED_AUDIO_MODEL = "seed-audio-1.0"
_DEFAULT_SPEAKER = "S_9Q5eU8Ec2"  # 银月音色
_SEED_AUDIO_ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/create"

# 单次最长时长（秒）
_MAX_DURATION_SECONDS = 120


def _clean_emoji(text: str) -> str:
    """移除表情占位符。"""
    import re
    return re.sub(r":[a-zA-Z0-9_]+:", "", text).strip()


@tool_metadata(
    name="创作音频",
    description="使用火山引擎 SeedAudio 合成音频，支持 TTS、唱歌、哼唱、音效等。传入文本内容，工具会生成音频并发送到频道。",
    emoji="🔊",
    category="娱乐",
)
async def generate_audio(
    text_prompt: str,
    speaker: Optional[str] = None,
    audio_format: str = "mp3",
    **kwargs,
) -> dict:
    """
    使用火山引擎 SeedAudio 合成音频，并发送到当前频道。

    使用场景：
    - 用户说"合成音频""生成音频""做个音频"等需求时调用。
    - 支持 TTS 语音合成、唱歌、哼唱、音效等多种模式。

    重要：text_prompt 的写法直接决定生成效果！根据需求选择以下模式：

    1. TTS 模式 — 直接写要朗读的文本。
       例如：你好，我是月月，很高兴认识你！

    2. 哼唱/唱歌模式 — 在歌词前加 ♪ 符号，SeedAudio 会以唱歌方式生成。
       例如：♪ 月亮代表我的心，你问我爱你有多深 ♪

    3. 描述式模式 — 用括号描述唱法/情绪，配合 ♪ 符号使用。
       例如：（温柔轻声哼唱）♪ 一闪一闪亮晶晶，满天都是小星星 ♪
       例如：（深情地唱）♪ 后来我总算学会了如何去爱 ♪

    更多示例：
    - TTS: "今天天气真好，我们出去走走吧。"
    - 哼唱: "♪ 让我们荡起双桨，小船儿推开波浪 ♪"
    - 描述式: "（欢快地唱）♪ 如果感到幸福你就拍拍手 ♪"

    注意：
    - 单次合成最长 120 秒，请控制文本长度。
    - 不要加 [Pop] [Soft] 等方括号音乐风格标签。
    - 哼唱模式一定要加 ♪ 符号，否则会变成普通朗读。

    Args:
        text_prompt: 要合成的文本内容。
            - TTS模式：直接写要说的文本
            - 哼唱模式：歌词前加 ♪ 符号，如 ♪ 月亮代表我的心 ♪
            - 描述式：用括号描述唱法，如（温柔轻声哼唱）♪ 歌词 ♪
        speaker: （可选）音色ID，默认使用 S_9Q5eU8Ec2（银月音色）。
        audio_format: （可选）输出格式，默认 mp3，可选 mp3/wav/ogg。

    Returns:
        成功发送音频时返回 skip_ai_response=True。
    """
    raw_prompt = str(text_prompt or "").strip()
    if not raw_prompt:
        return {
            "error": True,
            "reason": "empty_prompt",
            "hint": "文本内容不能为空。",
        }

    if not _SEED_AUDIO_API_KEY:
        return {
            "error": True,
            "reason": "no_api_key",
            "hint": "SEED_AUDIO_API_KEY 环境变量未配置。",
        }

    # 清理表情
    clean_prompt = _clean_emoji(raw_prompt)

    selected_speaker = (speaker or "").strip() or _DEFAULT_SPEAKER
    selected_format = (audio_format or "mp3").strip().lower()
    if selected_format not in ("mp3", "wav", "ogg"):
        selected_format = "mp3"

    message = kwargs.get("message")
    channel = kwargs.get("channel") or (getattr(message, "channel", None) if message else None)

    if message is None and channel is None:
        return {
            "error": True,
            "reason": "no_channel",
            "hint": "无法找到频道上下文。",
        }

    try:
        # 调用火山引擎 SeedAudio API
        timeout = aiohttp.ClientTimeout(total=120, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            payload = {
                "user": {"uid": _SEED_AUDIO_APP_ID},
                "req_params": {
                    "model": _SEED_AUDIO_MODEL,
                    "service": "tts",
                    "audio_params": {
                        "format": selected_format,
                        "sample_rate": 24000,
                        "channel": 1,
                    },
                    "text": clean_prompt,
                    "speaker": selected_speaker,
                },
            }

            async with session.post(
                _SEED_AUDIO_ENDPOINT,
                headers={
                    "X-Api-Key": _SEED_AUDIO_API_KEY,
                    "Content-Type": "application/json",
                },
                json=payload,
            ) as response:

                if response.status != 200:
                    error_text = await response.text()
                    log.error(f"SeedAudio API 返回 {response.status}: {error_text[:300]}")
                    return {
                        "error": True,
                        "reason": "api_error",
                        "hint": f"音频生成失败（HTTP {response.status}）。",
                    }

                resp_json = await response.json()

                # 检查业务层错误码
                code = resp_json.get("code")
                if code and str(code) != "3000" and str(code) != "0":
                    err_msg = resp_json.get("message", "未知错误")
                    log.error(f"SeedAudio 业务错误: code={code}, msg={err_msg}")
                    return {
                        "error": True,
                        "reason": "api_business_error",
                        "hint": f"音频生成失败（code={code}）：{err_msg}",
                    }

                # 提取 base64 音频数据
                data_field = resp_json.get("data")
                if not data_field:
                    return {
                        "error": True,
                        "reason": "empty_audio",
                        "hint": "音频生成返回空数据。",
                    }

                # data 可能是字符串(base64) 或包含 audio 字段的字典
                audio_b64 = None
                if isinstance(data_field, str):
                    audio_b64 = data_field
                elif isinstance(data_field, dict):
                    audio_b64 = data_field.get("audio") or data_field.get("audio_data") or data_field.get("base64")

                if not audio_b64:
                    log.error(f"SeedAudio 响应 data 字段缺少音频: {str(resp_json)[:500]}")
                    return {
                        "error": True,
                        "reason": "empty_audio",
                        "hint": "音频生成返回空数据。",
                    }

                # 解码 base64
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                except Exception as decode_err:
                    log.error(f"SeedAudio base64 解码失败: {decode_err}")
                    return {
                        "error": True,
                        "reason": "decode_failed",
                        "hint": "音频数据解码失败。",
                    }

                if not audio_bytes or len(audio_bytes) < 100:
                    return {
                        "error": True,
                        "reason": "empty_audio",
                        "hint": "音频生成返回空数据。",
                    }

                log.info(f"SeedAudio 合成成功: {len(audio_bytes)} bytes, format={selected_format}, speaker={selected_speaker}")

                # 发送音频文件到 Discord
                filename = f"yueyue_audio.{selected_format}"
                audio_file = discord.File(
                    fp=io.BytesIO(audio_bytes),
                    filename=filename,
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
                    "audio_format": selected_format,
                    "speaker": selected_speaker,
                    "message": "音频已发送到频道。",
                }

    except aiohttp.ClientError as exc:
        log.error(f"SeedAudio 网络错误: {exc}", exc_info=True)
        return {
            "error": True,
            "reason": "network_error",
            "hint": f"网络错误：{exc}",
        }
    except Exception as exc:
        log.error(f"SeedAudio 音频合成失败: {exc}", exc_info=True)
        return {
            "error": True,
            "reason": "unknown_error",
            "hint": f"生成失败：{exc}",
        }
