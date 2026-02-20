# -*- coding: utf-8 -*-

"""
语音合成服务

支持多提供商：
1. 火山引擎（豆包语音合成，HTTP 非流式接口）
2. OpenAI 兼容接口（硅基流动 / 自定义 API）
"""

import base64
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import aiohttp

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)


@dataclass
class VoiceResult:
    """语音合成结果"""
    audio_bytes: bytes
    mime_type: str
    file_ext: str
    provider: str
    model_name: str
    voice_type: str


class VoiceGenerationService:
    """统一语音合成服务"""

    def __init__(self):
        self._client: Optional[Dict[str, Any]] = None
        self._initialize_client()

    def _initialize_client(self):
        config = app_config.VOICE_CONFIG

        if not config.get("ENABLED", False):
            log.info("语音合成服务未启用")
            return

        provider = str(config.get("PROVIDER", "doubao")).strip().lower()
        if provider == "doubao":
            app_id = str(config.get("APP_ID", "")).strip()
            access_token = str(config.get("ACCESS_TOKEN", "")).strip()
            base_url = str(
                config.get("BASE_URL") or "https://openspeech.bytedance.com"
            ).strip().rstrip("/")
            cluster = str(config.get("CLUSTER", "volcano_tts")).strip()

            if not app_id or not access_token:
                log.warning("豆包语音配置不完整：缺少 APP_ID 或 ACCESS_TOKEN")
                return

            self._client = {
                "provider": "doubao",
                "base_url": base_url,
                "app_id": app_id,
                "access_token": access_token,
                "cluster": cluster,
            }
            log.info("语音服务已初始化（provider=doubao）")
            return

        if provider in {"siliconflow", "custom"}:
            base_url = str(config.get("BASE_URL", "")).strip()
            api_key = str(config.get("API_KEY", "")).strip()

            if provider == "siliconflow" and not base_url:
                base_url = "https://api.siliconflow.cn/v1"

            if not base_url or not api_key:
                log.warning(f"语音服务配置不完整（provider={provider}）：缺少 BASE_URL 或 API_KEY")
                return

            self._client = {
                "provider": provider,
                "base_url": base_url.rstrip("/"),
                "api_key": api_key,
            }
            log.info(f"语音服务已初始化（provider={provider}）")
            return

        log.warning(f"未知语音 provider: {provider}")

    def is_available(self) -> bool:
        return self._client is not None and bool(app_config.VOICE_CONFIG.get("ENABLED", False))

    def reinitialize(self):
        self._client = None
        self._initialize_client()

    @staticmethod
    def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, v))

    @staticmethod
    def _normalize_format(audio_format: str) -> str:
        fmt = (audio_format or "mp3").strip().lower()
        mapping = {
            "ogg": "opus",
            "oga": "opus",
        }
        return mapping.get(fmt, fmt)

    @staticmethod
    def _mime_type_from_format(audio_format: str) -> str:
        fmt = (audio_format or "mp3").strip().lower()
        mapping = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "opus": "audio/ogg",
            "ogg": "audio/ogg",
            "flac": "audio/flac",
            "aac": "audio/aac",
            "pcm": "application/octet-stream",
        }
        return mapping.get(fmt, "application/octet-stream")

    @staticmethod
    def _ext_from_format(audio_format: str) -> str:
        fmt = (audio_format or "mp3").strip().lower()
        mapping = {
            "mpeg": "mp3",
            "oga": "ogg",
        }
        return mapping.get(fmt, fmt if fmt else "mp3")

    @staticmethod
    def _ext_from_mime(mime_type: str, fallback: str) -> str:
        mime = (mime_type or "").split(";")[0].strip().lower()
        mapping = {
            "audio/mpeg": "mp3",
            "audio/mp3": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/ogg": "ogg",
            "audio/opus": "opus",
            "audio/flac": "flac",
            "audio/aac": "aac",
        }
        if mime in mapping:
            return mapping[mime]
        return VoiceGenerationService._ext_from_format(fallback)

    async def generate_voice(
        self,
        text: str,
        *,
        voice_type: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        volume_ratio: Optional[float] = None,
        pitch_ratio: Optional[float] = None,
        user_id: Optional[str] = None,
    ) -> Optional[VoiceResult]:
        if not self.is_available():
            log.warning("语音服务不可用")
            return None

        text = (text or "").strip()
        if not text:
            return None

        config = app_config.VOICE_CONFIG
        max_text_length = max(20, int(config.get("MAX_TEXT_LENGTH", 500)))
        if len(text) > max_text_length:
            text = text[:max_text_length]

        timeout_seconds = max(10, int(config.get("REQUEST_TIMEOUT_SECONDS", 120)))
        provider = self._client.get("provider")

        if provider == "doubao":
            return await self._generate_with_doubao(
                text=text,
                timeout_seconds=timeout_seconds,
                voice_type=voice_type,
                speed_ratio=speed_ratio,
                volume_ratio=volume_ratio,
                pitch_ratio=pitch_ratio,
                user_id=user_id,
            )

        return await self._generate_with_openai_compatible(
            text=text,
            timeout_seconds=timeout_seconds,
            voice_type=voice_type,
            speed_ratio=speed_ratio,
        )

    async def _generate_with_doubao(
        self,
        *,
        text: str,
        timeout_seconds: int,
        voice_type: Optional[str],
        speed_ratio: Optional[float],
        volume_ratio: Optional[float],
        pitch_ratio: Optional[float],
        user_id: Optional[str],
    ) -> Optional[VoiceResult]:
        config = app_config.VOICE_CONFIG
        base_url = self._client["base_url"]
        app_id = self._client["app_id"]
        access_token = self._client["access_token"]
        cluster = self._client["cluster"]
        selected_voice = (voice_type or config.get("VOICE_TYPE") or "").strip()
        if not selected_voice:
            selected_voice = "zh_female_wanwanxiaohe_moon_bigtts"

        requested_format = str(config.get("AUDIO_FORMAT", "mp3")).strip().lower()
        doubao_encoding_map = {
            "mp3": "mp3",
            "wav": "wav",
            "pcm": "pcm",
            "flac": "flac",
            "opus": "ogg_opus",
            "ogg": "ogg_opus",
        }
        encoding = doubao_encoding_map.get(requested_format, "mp3")

        speed = self._safe_float(
            speed_ratio if speed_ratio is not None else config.get("SPEED_RATIO", 1.0),
            default=1.0,
            minimum=0.2,
            maximum=3.0,
        )
        volume = self._safe_float(
            volume_ratio if volume_ratio is not None else config.get("VOLUME_RATIO", 1.0),
            default=1.0,
            minimum=0.2,
            maximum=3.0,
        )
        pitch = self._safe_float(
            pitch_ratio if pitch_ratio is not None else config.get("PITCH_RATIO", 1.0),
            default=1.0,
            minimum=0.1,
            maximum=3.0,
        )

        payload = {
            "app": {
                "appid": app_id,
                "token": access_token,
                "cluster": cluster,
            },
            "user": {
                "uid": str(user_id or "odysseia-guidance"),
            },
            "audio": {
                "voice_type": selected_voice,
                "encoding": encoding,
                "speed_ratio": speed,
                "volume_ratio": volume,
                "pitch_ratio": pitch,
            },
            "request": {
                "reqid": str(uuid.uuid4()),
                "text": text,
                "text_type": "plain",
                "operation": "query",
            },
        }

        endpoint = f"{base_url}/api/v1/tts"
        auth_candidates = [
            f"Bearer;{access_token}",
            f"Bearer {access_token}",
        ]

        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for idx, auth_value in enumerate(auth_candidates):
                headers = {
                    "Authorization": auth_value,
                    "Content-Type": "application/json",
                }
                try:
                    async with session.post(endpoint, headers=headers, json=payload) as response:
                        status_code = response.status
                        content_type = (
                            response.headers.get("Content-Type", "")
                            .split(";")[0]
                            .strip()
                            .lower()
                        )

                        if status_code == 401 and idx < len(auth_candidates) - 1:
                            log.debug("豆包鉴权格式重试中（Bearer/Bearer;）")
                            continue

                        if status_code != 200:
                            error_text = await response.text()
                            log.error(
                                f"豆包语音 API 返回错误 {status_code}: {error_text[:500]}"
                            )
                            return None

                        # 部分网关可能直接返回音频流
                        if content_type.startswith("audio/"):
                            audio_bytes = await response.read()
                            if not audio_bytes:
                                log.error("豆包语音返回了空音频流")
                                return None
                            return VoiceResult(
                                audio_bytes=audio_bytes,
                                mime_type=content_type,
                                file_ext=self._ext_from_mime(content_type, requested_format),
                                provider="doubao",
                                model_name="doubao-tts",
                                voice_type=selected_voice,
                            )

                        # 常规返回 JSON，音频通常在 data(base64) 字段
                        try:
                            data = await response.json(content_type=None)
                        except Exception:
                            raw_text = await response.text()
                            log.error(f"豆包语音响应无法解析为 JSON: {raw_text[:500]}")
                            return None

                        if not isinstance(data, dict):
                            log.error(f"豆包语音返回结构异常: {type(data)}")
                            return None

                        code = data.get("code")
                        if code not in (0, 3000, "0", "3000", None):
                            log.error(
                                f"豆包语音返回失败 code={code}, message={data.get('message')}"
                            )
                            return None

                        audio_b64 = data.get("data")
                        if isinstance(audio_b64, dict):
                            audio_b64 = (
                                audio_b64.get("audio")
                                or audio_b64.get("data")
                                or audio_b64.get("audio_data")
                            )

                        if not isinstance(audio_b64, str) or not audio_b64.strip():
                            log.error("豆包语音返回中未找到有效的 base64 音频数据")
                            return None

                        try:
                            audio_bytes = base64.b64decode(audio_b64)
                        except Exception as decode_error:
                            log.error(f"豆包语音 base64 解码失败: {decode_error}")
                            return None

                        if not audio_bytes:
                            log.error("豆包语音解码后音频为空")
                            return None

                        result_format = "opus" if encoding == "ogg_opus" else requested_format
                        return VoiceResult(
                            audio_bytes=audio_bytes,
                            mime_type=self._mime_type_from_format(result_format),
                            file_ext=self._ext_from_format(result_format),
                            provider="doubao",
                            model_name="doubao-tts",
                            voice_type=selected_voice,
                        )
                except aiohttp.ClientError as e:
                    log.error(f"请求豆包语音失败: {e}")
                    return None
                except Exception as e:
                    log.error(f"豆包语音合成异常: {e}", exc_info=True)
                    return None

        return None

    async def _generate_with_openai_compatible(
        self,
        *,
        text: str,
        timeout_seconds: int,
        voice_type: Optional[str],
        speed_ratio: Optional[float],
    ) -> Optional[VoiceResult]:
        config = app_config.VOICE_CONFIG
        provider = self._client["provider"]
        base_url = self._client["base_url"]
        api_key = self._client["api_key"]

        model_name = str(config.get("MODEL_NAME", "")).strip()
        if not model_name:
            model_name = "FunAudioLLM/CosyVoice2-0.5B"

        selected_voice = (voice_type or config.get("VOICE_TYPE") or "").strip()
        if not selected_voice:
            selected_voice = "default"

        requested_format = self._normalize_format(str(config.get("AUDIO_FORMAT", "mp3")))
        if requested_format not in {"mp3", "wav", "opus", "flac", "pcm", "aac"}:
            requested_format = "mp3"

        speed = self._safe_float(
            speed_ratio if speed_ratio is not None else config.get("SPEED_RATIO", 1.0),
            default=1.0,
            minimum=0.2,
            maximum=3.0,
        )

        endpoint = f"{base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "input": text,
            "voice": selected_voice,
            "response_format": requested_format,
            "speed": speed,
        }

        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(endpoint, headers=headers, json=payload) as response:
                    status_code = response.status
                    content_type = (
                        response.headers.get("Content-Type", "")
                        .split(";")[0]
                        .strip()
                        .lower()
                    )
                    body = await response.read()

                    if status_code != 200:
                        error_text = body.decode("utf-8", errors="ignore")
                        log.error(
                            f"语音 API（{provider}）返回错误 {status_code}: {error_text[:500]}"
                        )
                        return None

                    if not body:
                        log.error(f"语音 API（{provider}）返回空响应")
                        return None

                    if not content_type or content_type == "application/octet-stream":
                        content_type = self._mime_type_from_format(requested_format)

                    return VoiceResult(
                        audio_bytes=body,
                        mime_type=content_type,
                        file_ext=self._ext_from_mime(content_type, requested_format),
                        provider=provider,
                        model_name=model_name,
                        voice_type=selected_voice,
                    )
        except aiohttp.ClientError as e:
            log.error(f"请求语音 API（{provider}）失败: {e}")
            return None
        except Exception as e:
            log.error(f"语音合成异常（{provider}）: {e}", exc_info=True)
            return None


# 全局单例
voice_service = VoiceGenerationService()