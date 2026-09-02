# -*- coding: utf-8 -*-
"""
Whisper 语音转录工具 - 通过 bufan.live API 转录音频/视频
"""

import logging
import io
import os
import aiohttp
from typing import Optional

log = logging.getLogger(__name__)

WHISPER_API_URL = os.environ.get(
    "WHISPER_API_URL",
    "https://bufan.live/v1/audio/transcriptions"
)
WHISPER_API_KEY = os.environ.get(
    "WHISPER_API_KEY",
    os.environ.get("GEMINI_IMAGEN_API_KEY",
     os.environ.get("IMAGEN_API_KEY", ""))
)
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "openai/whisper-large-v3-turbo")


async def transcribe_audio(
    audio_bytes: bytes,
    filename: str = "audio.mp3",
    language: Optional[str] = None,
    prompt: Optional[str] = None,
) -> Optional[str]:
    """
    用 Whisper API 转录音频/视频中的语音内容。

    Args:
        audio_bytes: 音频/视频二进制数据
        filename: 文件名（用于 MIME 推断）
        language: ISO 639-1 语言代码 (zh/en/ja/...), None=自动检测
        prompt: 可选引导文本

    Returns:
        转录文本，失败返回 None
    """
    if not audio_bytes:
        return None

    # 对于视频文件，需要先提取音频——但 whisper 可能也支持视频直接输入
    # 大多数 whisper 端点只接受音频格式，视频需要先用 ffmpeg 提取音频
    # 先尝试直接发送，如果失败再用 ffmpeg 提取

    try:
        form_data = aiohttp.FormData()
        form_data.add_field("file", audio_bytes, filename=filename)
        form_data.add_field("model", WHISPER_MODEL)
        form_data.add_field("response_format", "json")
        form_data.add_field("temperature", "0")
        form_data.add_field("stream", "false")

        if language:
            form_data.add_field("language", language)
        if prompt:
            form_data.add_field("prompt", prompt)

        timeout = aiohttp.ClientTimeout(total=120)
        headers = {"Authorization": f"Bearer {WHISPER_API_KEY}"}

        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                WHISPER_API_URL,
                data=form_data,
                headers=headers,
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    log.error(f"Whisper API 返回 {resp.status}: {error_text[:200]}")

                    # 如果是视频文件，尝试提取音频后重试
                    if resp.status == 400 and _is_video_file(filename):
                        log.info("检测到视频文件，尝试用 ffmpeg 提取音频后重试...")
                        audio_only = _extract_audio_from_video(audio_bytes)
                        if audio_only:
                            return await transcribe_audio(
                                audio_only, filename="extracted_audio.mp3",
                                language=language, prompt=prompt
                            )
                    return None

                response_text = await resp.text()
                # API 可能返回 application/json 或 text/plain
                import json as _json
                try:
                    data = _json.loads(response_text)
                    text = data.get("text", "").strip()
                except (_json.JSONDecodeError, TypeError):
                    # 如果不是 JSON，直接当纯文本处理
                    text = response_text.strip()
                
                if text:
                    log.info(f"Whisper 转录成功: {len(text)} 字符, 文件: {filename}")
                    return text
                else:
                    log.warning("Whisper 返回空文本")
                    return None

    except Exception as e:
        log.error(f"Whisper 转录异常: {e}", exc_info=True)
        return None


def _is_video_file(filename: str) -> bool:
    """判断文件名是否是视频格式"""
    video_exts = {".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv"}
    return any(filename.lower().endswith(ext) for ext in video_exts)


def _extract_audio_from_video(video_bytes: bytes) -> Optional[bytes]:
    """用 ffmpeg 从视频中提取音频"""
    import subprocess
    import tempfile

    try:
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_video:
            tmp_video.write(video_bytes)
            tmp_video_path = tmp_video.name

        tmp_audio_path = tmp_video_path.replace(".mp4", ".mp3")

        result = subprocess.run(
            [
                "ffmpeg", "-i", tmp_video_path,
                "-vn", "-acodec", "libmp3lame",
                "-ab", "128k", "-ar", "16000",
                "-y", tmp_audio_path
            ],
            capture_output=True,
            timeout=60,
        )

        if result.returncode != 0:
            log.error(f"ffmpeg 提取音频失败: {result.stderr.decode()[:200]}")
            return None

        with open(tmp_audio_path, "rb") as f:
            audio_data = f.read()

        os.unlink(tmp_video_path)
        os.unlink(tmp_audio_path)

        log.info(f"ffmpeg 提取音频成功: {len(audio_data)} bytes")
        return audio_data

    except FileNotFoundError:
        log.error("ffmpeg 未安装，无法提取视频音频")
        return None
    except Exception as e:
        log.error(f"提取视频音频异常: {e}")
        return None


__all__ = ["transcribe_audio"]
