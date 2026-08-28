# -*- coding: utf-8 -*-

"""
语音合成服务

支持多提供商：
1. 火山引擎（豆包语音合成，HTTP 非流式接口）
2. OpenAI 兼容接口（硅基流动 / 自定义 API）
3. 小米 MiMo（chat/completions + audio）
"""

import base64
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

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
        self._doubao_rr_index: int = -1
        self._initialize_client()

    def _can_fallback_to_elevenlabs(self) -> bool:
        """检查 ElevenLabs fallback 是否可用（需要 API Key）。"""
        import os
        api_key = str(os.getenv("ELEVENLABS_API_KEY", "") or "").strip()
        return bool(api_key)

    def _initialize_client(self):
        config = app_config.VOICE_CONFIG

        if not config.get("ENABLED", False):
            log.info("语音合成服务未启用")
            return

        provider = str(config.get("PROVIDER", "doubao")).strip().lower()

        if provider == "elevenlabs":
            api_key = str(config.get("API_KEY", "")).strip()
            voice_id = str(config.get("VOICE_TYPE", "")).strip()
            model_name = str(config.get("MODEL_NAME", "")).strip() or "eleven_v3"
            if not api_key or not voice_id:
                log.warning("ElevenLabs 语音配置不完整：缺少 API_KEY 或 VOICE_TYPE(voice_id)")
                return
            self._client = {
                "provider": "elevenlabs",
                "api_key": api_key,
                "voice_id": voice_id,
                "model_name": model_name,
                "base_url": "https://api.elevenlabs.io/v1",
            }
            log.info(
                "语音服务已初始化（provider=elevenlabs, voice_id=%s, model=%s）",
                voice_id, model_name,
            )
            return

        if provider == "doubao":
            app_id = str(config.get("APP_ID", "")).strip()
            access_token = str(config.get("ACCESS_TOKEN", "")).strip()
            app_pool = self._normalize_doubao_app_pool(config.get("APP_POOL", []))
            app_default_voice_types = self._normalize_app_default_voice_types(
                config.get("APP_DEFAULT_VOICE_TYPES", {})
            )
            clone_voice_app_bindings = self._normalize_clone_voice_app_bindings(
                config.get("CLONE_VOICE_APP_BINDINGS", {})
            )
            base_url = str(
                config.get("BASE_URL") or "https://openspeech.bytedance.com"
            ).strip().rstrip("/")
            # 注意：cluster 允许留空，运行时会按 voice_type 自动选择
            cluster = str(config.get("CLUSTER", "")).strip()

            effective_pool = list(app_pool)
            if not effective_pool and app_id and access_token:
                effective_pool.append(
                    {
                        "app_id": app_id,
                        "access_token": access_token,
                    }
                )

            if not effective_pool:
                log.warning(
                    "豆包语音配置不完整：缺少可用账号（请配置 APP_ID/ACCESS_TOKEN 或 APP_POOL）"
                )
                return

            self._client = {
                "provider": "doubao",
                "base_url": base_url,
                "app_id": app_id,
                "access_token": access_token,
                "app_pool": effective_pool,
                "app_default_voice_types": app_default_voice_types,
                "clone_voice_app_bindings": clone_voice_app_bindings,
                "cluster": cluster,
            }
            log.info(
                "语音服务已初始化（provider=doubao, app_pool=%s, app_defaults=%s, clone_bindings=%s）",
                len(effective_pool),
                len(app_default_voice_types),
                len(clone_voice_app_bindings),
            )
            return

        if provider in {"siliconflow", "custom", "xiaomi"}:
            base_url = str(config.get("BASE_URL", "")).strip()
            api_key = str(config.get("API_KEY", "")).strip()

            if provider == "siliconflow" and not base_url:
                base_url = "https://api.siliconflow.cn/v1"
            elif provider == "xiaomi" and not base_url:
                base_url = "https://api.xiaomimimo.com/v1"

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
        self._doubao_rr_index = -1
        self._initialize_client()

    @staticmethod
    def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, v))

    @staticmethod
    def _safe_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off"}:
            return False
        return default

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

    @staticmethod
    def _transcode_audio_bytes_to_ogg_opus(audio_bytes: bytes) -> Optional[bytes]:
        """将音频转为 OGG/OPUS，用于 Discord 原生语音消息。"""
        if not audio_bytes:
            return None

        try:
            import soundfile as sf
        except Exception as exc:
            log.warning(f"未安装 soundfile，无法将音频转为 OGG/OPUS: {exc}")
            return None

        try:
            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes), dtype="float32")
            output_buffer = io.BytesIO()
            sf.write(output_buffer, audio_data, sample_rate, format="OGG", subtype="OPUS")
            converted_audio = output_buffer.getvalue()
            return converted_audio or None
        except Exception as exc:
            log.warning(f"音频转 OGG/OPUS 失败，将保留原始格式: {exc}")
            return None

    @staticmethod
    def _normalize_extra_body(extra_body: Any) -> Dict[str, Any]:
        if not isinstance(extra_body, dict):
            return {}
        normalized: Dict[str, Any] = {}
        for raw_key, raw_value in extra_body.items():
            key = str(raw_key).strip()
            if not key:
                continue
            normalized[key] = raw_value
        return normalized

    @staticmethod
    def _normalize_references(references: Any) -> list[Dict[str, str]]:
        if not isinstance(references, list):
            return []
        normalized: list[Dict[str, str]] = []
        for item in references:
            if not isinstance(item, dict):
                continue
            audio = str(item.get("audio", "")).strip()
            text = str(item.get("text", "")).strip()
            if not audio or not text:
                continue
            normalized.append({"audio": audio, "text": text})
        return normalized

    @staticmethod
    def _normalize_doubao_app_pool(values: Any) -> list[Dict[str, str]]:
        normalized: list[Dict[str, str]] = []
        seen: set[tuple[str, str]] = set()

        if not isinstance(values, list):
            return normalized

        for item in values:
            if not isinstance(item, dict):
                continue

            app_id = str(item.get("app_id", "")).strip()
            access_token = str(item.get("access_token", "")).strip()
            if not app_id or not access_token:
                continue

            dedupe_key = (app_id, access_token)
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)
            normalized.append(
                {
                    "app_id": app_id,
                    "access_token": access_token,
                }
            )

        return normalized

    @staticmethod
    def _normalize_app_default_voice_types(values: Any) -> Dict[str, str]:
        if not isinstance(values, dict):
            return {}

        normalized: Dict[str, str] = {}
        for raw_app_id, raw_voice_type in values.items():
            app_id = str(raw_app_id).strip()
            voice_type = str(raw_voice_type).strip()
            if not app_id or not voice_type:
                continue
            normalized[app_id] = voice_type

        return normalized

    @staticmethod
    def _normalize_clone_voice_app_bindings(values: Any) -> Dict[str, str]:
        if not isinstance(values, dict):
            return {}

        normalized: Dict[str, str] = {}
        for raw_voice, raw_app_id in values.items():
            voice = str(raw_voice).strip()
            app_id = str(raw_app_id).strip()
            if not voice or not app_id:
                continue
            normalized[voice] = app_id

        return normalized

    def _next_doubao_account(self, app_pool: list[Dict[str, str]]) -> Optional[Dict[str, str]]:
        if not app_pool:
            return None

        self._doubao_rr_index = (self._doubao_rr_index + 1) % len(app_pool)
        return app_pool[self._doubao_rr_index]

    def _resolve_bound_app_id_for_clone_voice(
        self,
        *,
        voice_type: str,
        clone_voice_app_bindings: Dict[str, str],
    ) -> Optional[str]:
        if not voice_type or not clone_voice_app_bindings:
            return None

        direct = clone_voice_app_bindings.get(voice_type)
        if direct:
            return str(direct).strip() or None

        voice_lower = voice_type.lower()
        for key, app_id in clone_voice_app_bindings.items():
            if str(key).strip().lower() == voice_lower:
                normalized = str(app_id).strip()
                if normalized:
                    return normalized

        return None

    def _select_doubao_credentials(
        self,
        *,
        selected_voice: str,
        is_clone_voice: bool,
        app_pool: list[Dict[str, str]],
        clone_voice_app_bindings: Dict[str, str],
        fallback_app_id: str,
        fallback_access_token: str,
    ) -> Tuple[str, str, str]:
        """
        选择豆包账号：
        1) 复刻音色：必须使用该复刻音色绑定的 APP_ID
        2) 非复刻音色：从账号池轮询
        3) 最后：回退单账号配置
        """
        if is_clone_voice:
            bound_app_id = self._resolve_bound_app_id_for_clone_voice(
                voice_type=selected_voice,
                clone_voice_app_bindings=clone_voice_app_bindings,
            )
            if not bound_app_id:
                log.error(
                    "复刻音色未配置绑定 APP_ID，拒绝回退到轮询池：voice_type=%s",
                    selected_voice,
                )
                return "", "", "clone_voice_binding_missing"

            for item in app_pool:
                candidate_app_id = str(item.get("app_id", "")).strip()
                candidate_access_token = str(item.get("access_token", "")).strip()
                if candidate_app_id == bound_app_id and candidate_access_token:
                    return candidate_app_id, candidate_access_token, "clone_voice_binding"

            if fallback_app_id == bound_app_id and fallback_access_token:
                return fallback_app_id, fallback_access_token, "clone_voice_binding_fallback"

            log.error(
                "复刻音色绑定的 APP_ID 未命中可用凭据，拒绝回退到轮询池：voice_type=%s, bound_app_id=%s",
                selected_voice,
                bound_app_id,
            )
            return "", "", "clone_voice_binding_unavailable"

        round_robin_account = self._next_doubao_account(app_pool)
        if round_robin_account:
            rr_app_id = str(round_robin_account.get("app_id", "")).strip()
            rr_access_token = str(round_robin_account.get("access_token", "")).strip()
            if rr_app_id and rr_access_token:
                return rr_app_id, rr_access_token, "round_robin_pool"

        if fallback_app_id and fallback_access_token:
            return fallback_app_id, fallback_access_token, "single_fallback"

        return "", "", "unavailable"

    @staticmethod
    def _is_doubao_official_voice(voice_type: str) -> bool:
        """判断是否为豆包官方音色。"""
        value = (voice_type or "").strip()
        if not value:
            return False

        lowered = value.lower()
        if lowered.endswith("_bigtts"):
            return True

        if lowered.startswith(("zh_", "en_", "multi_", "saturn_", "icl_")):
            return True

        # 端到端实时语音大模型-O版本中存在少量短名称
        if lowered in {"vivi", "xiaohe", "yunzhou", "xiaotian", "tim", "dacey", "stokie"}:
            return True

        return False

    @classmethod
    def _is_doubao_clone_voice(cls, voice_type: str) -> bool:
        """判断是否为声音复刻音色（例如 S_03FKArQO1）。"""
        value = (voice_type or "").strip()
        if not value:
            return False

        # 声音复刻音色 ID 常见格式
        if value.startswith(("S_", "s_")):
            return True

        # 按用户约定：不在官方音色命名规则内的音色，视为复刻音色
        return not cls._is_doubao_official_voice(value)

    @staticmethod
    def _is_doubao_quota_exceeded_error(error_payload: Any) -> bool:
        text = str(error_payload or "").strip().lower()
        if not text:
            return False

        if "quota exceeded" in text or "quota_exceeded" in text or "exceed quota" in text:
            return True

        if "试用版用量用完" in text or "用量用完" in text or "额度用完" in text:
            return True

        return False

    @classmethod
    def _resolve_doubao_cluster(cls, configured_cluster: str, voice_type: str) -> str:
        """根据音色类型自动选择豆包 cluster。"""
        normalized_cluster = (configured_cluster or "").strip()
        lowered_cluster = normalized_cluster.lower()
        is_clone_voice = cls._is_doubao_clone_voice(voice_type)

        if is_clone_voice:
            # 声音复刻（ICL）优先走 volcano_icl；兼容旧写法自动映射
            if not lowered_cluster or lowered_cluster == "volcano_tts":
                return "volcano_icl"
            if lowered_cluster in {"volcano_mega", "volcano_mega_tts"}:
                return "volcano_icl"
            if lowered_cluster in {"volcano_mega_concurr", "volcano_mega_tts_concurr"}:
                return "volcano_icl_concurr"
            return normalized_cluster

        # 官方音色默认走普通语音合成 cluster
        if not lowered_cluster:
            return "volcano_tts"

        # 兼容误填：官方音色回落到 volcano_tts
        if lowered_cluster in {
            "volcano_icl",
            "volcano_icl_concurr",
            "volcano_mega",
            "volcano_mega_tts",
            "volcano_mega_concurr",
            "volcano_mega_tts_concurr",
        }:
            return "volcano_tts"

        return normalized_cluster

    async def generate_voice(
        self,
        text: str,
        *,
        voice_type: Optional[str] = None,
        speed_ratio: Optional[float] = None,
        volume_ratio: Optional[float] = None,
        pitch_ratio: Optional[float] = None,
        emotion: Optional[str] = None,
        enable_emotion: Optional[bool] = None,
        emotion_scale: Optional[float] = None,
        user_id: Optional[str] = None,
        output_format: Optional[str] = None,
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

        if provider == "elevenlabs":
            return await self._generate_with_elevenlabs(
                text=text,
                timeout_seconds=timeout_seconds,
                voice_type=voice_type,
                speed_ratio=speed_ratio,
            )

        if provider == "doubao":
            result = await self._generate_with_doubao(
                text=text,
                timeout_seconds=timeout_seconds,
                voice_type=voice_type,
                speed_ratio=speed_ratio,
                volume_ratio=volume_ratio,
                pitch_ratio=pitch_ratio,
                emotion=emotion,
                enable_emotion=enable_emotion,
                emotion_scale=emotion_scale,
                user_id=user_id,
            )
            if result and result.audio_bytes:
                return result
            # 豆包失败，fallback 到 ElevenLabs
            log.warning("豆包语音生成失败，自动回退到 ElevenLabs V3")
            if self._can_fallback_to_elevenlabs():
                return await self._generate_with_elevenlabs(
                    text=text,
                    timeout_seconds=timeout_seconds,
                    voice_type=None,
                    speed_ratio=speed_ratio,
                )
            return None

        if provider == "xiaomi":
            return await self._generate_with_xiaomi(
                text=text,
                timeout_seconds=timeout_seconds,
                voice_type=voice_type,
                speed_ratio=speed_ratio,
                pitch_ratio=pitch_ratio,
                emotion=emotion,
                enable_emotion=enable_emotion,
                emotion_scale=emotion_scale,
                output_format=output_format,
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
        emotion: Optional[str],
        enable_emotion: Optional[bool],
        emotion_scale: Optional[float],
        user_id: Optional[str],
    ) -> Optional[VoiceResult]:
        config = app_config.VOICE_CONFIG
        base_url = self._client["base_url"]
        configured_cluster = str(self._client.get("cluster", "")).strip()
        configured_clone_cluster = str(config.get("CLONE_CLUSTER", "")).strip()
        configured_clone_resource_id = str(config.get("CLONE_RESOURCE_ID", "")).strip()

        if configured_clone_cluster.lower().startswith("seed-icl-"):
            log.warning(
                "检测到 CLONE_CLUSTER 误填为 resource-id：%s，已自动纠正为 volcano_icl",
                configured_clone_cluster,
            )
            configured_clone_cluster = "volcano_icl"

        requested_voice = (voice_type or "").strip()
        selected_voice = requested_voice or str(config.get("VOICE_TYPE") or "").strip()
        if not selected_voice:
            selected_voice = "zh_female_wanwanxiaohe_moon_bigtts"
        is_voice_explicit = bool(requested_voice)

        initial_is_clone_voice = self._is_doubao_clone_voice(selected_voice)
        app_pool = self._normalize_doubao_app_pool(self._client.get("app_pool", []))
        app_default_voice_types = self._normalize_app_default_voice_types(
            self._client.get("app_default_voice_types", {})
        )
        clone_voice_app_bindings = self._normalize_clone_voice_app_bindings(
            self._client.get("clone_voice_app_bindings", {})
        )
        fallback_app_id = str(self._client.get("app_id", "")).strip()
        fallback_access_token = str(self._client.get("access_token", "")).strip()

        account_candidates: list[Tuple[str, str, str]] = []
        attempted_credentials: set[tuple[str, str]] = set()

        if initial_is_clone_voice:
            app_id, access_token, account_route = self._select_doubao_credentials(
                selected_voice=selected_voice,
                is_clone_voice=True,
                app_pool=app_pool,
                clone_voice_app_bindings=clone_voice_app_bindings,
                fallback_app_id=fallback_app_id,
                fallback_access_token=fallback_access_token,
            )
            if not app_id or not access_token:
                log.error(
                    "豆包语音账号不可用：未找到可用 APP_ID / ACCESS_TOKEN（route=%s, voice_type=%s, clone_voice=%s）",
                    account_route,
                    selected_voice,
                    initial_is_clone_voice,
                )
                return None
            account_candidates.append((app_id, access_token, account_route))
        else:
            app_id, access_token, account_route = self._select_doubao_credentials(
                selected_voice=selected_voice,
                is_clone_voice=False,
                app_pool=app_pool,
                clone_voice_app_bindings=clone_voice_app_bindings,
                fallback_app_id=fallback_app_id,
                fallback_access_token=fallback_access_token,
            )
            if app_id and access_token:
                first_credential = (app_id, access_token)
                attempted_credentials.add(first_credential)
                account_candidates.append((app_id, access_token, account_route))

            if app_pool:
                start_index = self._doubao_rr_index if account_route == "round_robin_pool" else -1
                if 0 <= start_index < len(app_pool):
                    remaining_indices = list(range(start_index + 1, len(app_pool)))
                    remaining_indices.extend(range(0, start_index))
                else:
                    remaining_indices = list(range(len(app_pool)))

                for idx in remaining_indices:
                    candidate = app_pool[idx]
                    candidate_app_id = str(candidate.get("app_id", "")).strip()
                    candidate_access_token = str(candidate.get("access_token", "")).strip()
                    if not candidate_app_id or not candidate_access_token:
                        continue

                    dedupe_key = (candidate_app_id, candidate_access_token)
                    if dedupe_key in attempted_credentials:
                        continue

                    attempted_credentials.add(dedupe_key)
                    account_candidates.append(
                        (candidate_app_id, candidate_access_token, "round_robin_pool_retry")
                    )

            fallback_credential = (fallback_app_id, fallback_access_token)
            if (
                fallback_app_id
                and fallback_access_token
                and fallback_credential not in attempted_credentials
            ):
                account_candidates.append(
                    (fallback_app_id, fallback_access_token, "single_fallback")
                )

            if not account_candidates:
                log.error(
                    "豆包语音账号不可用：未找到可用 APP_ID / ACCESS_TOKEN（voice_type=%s, clone_voice=%s）",
                    selected_voice,
                    initial_is_clone_voice,
                )
                return None

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
        pitch = self._safe_float(
            pitch_ratio if pitch_ratio is not None else config.get("PITCH_RATIO", 1.0),
            default=1.0,
            minimum=0.1,
            maximum=3.0,
        )
        selected_emotion = (
            str(emotion).strip()
            if emotion is not None
            else str(config.get("EMOTION", "")).strip()
        )
        selected_enable_emotion = (
            bool(enable_emotion)
            if enable_emotion is not None
            else self._safe_bool(config.get("ENABLE_EMOTION", False), False)
        )
        selected_emotion_scale = self._safe_float(
            emotion_scale if emotion_scale is not None else config.get("EMOTION_SCALE", 4.0),
            default=4.0,
            minimum=1.0,
            maximum=5.0,
        )

        endpoint = f"{base_url}/api/v1/tts"
        timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for account_index, (app_id, access_token, account_route) in enumerate(
                account_candidates
            ):
                attempt_voice = selected_voice
                if not initial_is_clone_voice and not is_voice_explicit:
                    mapped_default_voice = str(
                        app_default_voice_types.get(app_id, "")
                    ).strip()
                    if mapped_default_voice:
                        if self._is_doubao_clone_voice(mapped_default_voice):
                            log.warning(
                                "APP 默认音色映射为复刻音色，已忽略：app_id=%s, mapped_voice=%s",
                                app_id,
                                mapped_default_voice,
                            )
                        else:
                            if mapped_default_voice != attempt_voice:
                                log.info(
                                    "根据 APP 默认音色映射切换 voice_type: app_id=%s, %s -> %s",
                                    app_id,
                                    attempt_voice,
                                    mapped_default_voice,
                                )
                            attempt_voice = mapped_default_voice

                attempt_is_clone_voice = self._is_doubao_clone_voice(attempt_voice)

                normalized_clone_resource_id = configured_clone_resource_id.lower()
                resolved_clone_resource_id = configured_clone_resource_id
                if attempt_is_clone_voice:
                    if normalized_clone_resource_id in {
                        "volcano_icl",
                        "volcano_icl_concurr",
                        "volcano_mega",
                        "volcano_mega_tts",
                        "volcano_mega_concurr",
                        "volcano_mega_tts_concurr",
                    }:
                        log.warning(
                            "检测到 CLONE_RESOURCE_ID 误填为 cluster 值：%s，已自动纠正为 seed-icl-2.0",
                            configured_clone_resource_id,
                        )
                        resolved_clone_resource_id = "seed-icl-2.0"
                        normalized_clone_resource_id = resolved_clone_resource_id
                    elif (
                        resolved_clone_resource_id
                        and not normalized_clone_resource_id.startswith("seed-icl-")
                    ):
                        log.warning(
                            "检测到 CLONE_RESOURCE_ID 非法值：%s，已自动回退为 seed-icl-2.0",
                            configured_clone_resource_id,
                        )
                        resolved_clone_resource_id = "seed-icl-2.0"

                cluster_input = (
                    configured_clone_cluster
                    if attempt_is_clone_voice and configured_clone_cluster
                    else configured_cluster
                )
                cluster = self._resolve_doubao_cluster(cluster_input, attempt_voice)
                resource_id = (
                    resolved_clone_resource_id or "seed-icl-2.0"
                    if attempt_is_clone_voice
                    else "volc.megatts.default"
                )
                if cluster != cluster_input:
                    log.info(
                        "豆包 cluster 已自动调整: %s -> %s（voice_type=%s）",
                        cluster_input or "<empty>",
                        cluster,
                        attempt_voice,
                    )
                log.info(
                    "豆包语音路由: voice_type=%s, cluster=%s, resource_id=%s, clone_voice=%s, clone_cluster_config=%s, app_route=%s, app_id=%s, attempt=%s/%s",
                    attempt_voice,
                    cluster,
                    resource_id,
                    attempt_is_clone_voice,
                    configured_clone_cluster or "<empty>",
                    account_route,
                    app_id,
                    account_index + 1,
                    len(account_candidates),
                )

                volume = self._safe_float(
                    volume_ratio if volume_ratio is not None else config.get("VOLUME_RATIO", 1.0),
                    default=1.0,
                    minimum=0.5 if attempt_is_clone_voice else 0.2,
                    maximum=2.0 if attempt_is_clone_voice else 3.0,
                )

                # 声音复刻链路按 ICL 文档白名单收敛参数：
                # /api/v1/tts 的 audio 仅传 voice_type / encoding / speed_ratio / loudness_ratio。
                # model_type=4 属于训练接口参数，合成阶段不透传，避免触发参数校验失败。
                audio_payload = {
                    "voice_type": attempt_voice,
                    "encoding": encoding,
                    "speed_ratio": speed,
                    "loudness_ratio": volume,
                }

                if attempt_is_clone_voice:
                    if selected_emotion or selected_enable_emotion or enable_emotion is not None:
                        log.info("复刻音色请求已忽略情感参数，按 ICL 白名单发送")
                else:
                    audio_payload["pitch_ratio"] = pitch
                    if selected_emotion:
                        audio_payload["emotion"] = selected_emotion
                    if selected_emotion or selected_enable_emotion or enable_emotion is not None:
                        audio_payload["enable_emotion"] = bool(selected_enable_emotion)
                        audio_payload["emotion_scale"] = selected_emotion_scale

                payload = {
                    "app": {
                        "appid": app_id,
                        "token": access_token,
                        "cluster": cluster,
                    },
                    "user": {
                        "uid": str(user_id or "odysseia-guidance"),
                    },
                    "audio": audio_payload,
                    "request": {
                        "reqid": str(uuid.uuid4()),
                        "text": text,
                        "text_type": "plain",
                        "operation": "query",
                    },
                }

                auth_candidates = [
                    f"Bearer;{access_token}",
                    f"Bearer {access_token}",
                ]

                should_try_next_account = False
                for idx, auth_value in enumerate(auth_candidates):
                    headers = {
                        "Authorization": auth_value,
                        "Content-Type": "application/json",
                        "Resource-Id": resource_id,
                        "X-Api-App-Id": str(app_id),
                        "X-Api-Access-Key": str(access_token),
                        "X-Api-Resource-Id": resource_id,
                        "X-Api-Request-Id": str(uuid.uuid4()),
                    }
                    # 新版控制台账号需要 X-Api-* 鉴权（2026-08-21 新增）。
                    # 旧版账号仅需 Authorization + Resource-Id 即可。
                    # X-Api-* header 不会影响旧版账号，旧版服务端会忽略。
                    try:
                        async with session.post(
                            endpoint, headers=headers, json=payload
                        ) as response:
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
                                if (
                                    self._is_doubao_quota_exceeded_error(error_text)
                                    and not attempt_is_clone_voice
                                    and account_index < len(account_candidates) - 1
                                ):
                                    log.warning(
                                        "豆包额度不足，自动切换下一账号重试：app_id=%s, route=%s, error=%s",
                                        app_id,
                                        account_route,
                                        error_text[:200],
                                    )
                                    should_try_next_account = True
                                    break

                                log.error(
                                    "豆包语音 API 返回错误 %s（app_id=%s, route=%s）: %s",
                                    status_code,
                                    app_id,
                                    account_route,
                                    error_text[:500],
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
                                    voice_type=attempt_voice,
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
                                error_message = str(data.get("message", "")).strip()
                                if (
                                    self._is_doubao_quota_exceeded_error(error_message)
                                    and not attempt_is_clone_voice
                                    and account_index < len(account_candidates) - 1
                                ):
                                    log.warning(
                                        "豆包额度不足（JSON code），自动切换下一账号重试：app_id=%s, route=%s, code=%s, message=%s",
                                        app_id,
                                        account_route,
                                        code,
                                        error_message[:200],
                                    )
                                    should_try_next_account = True
                                    break

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
                                voice_type=attempt_voice,
                            )
                    except aiohttp.ClientError as e:
                        log.error(f"请求豆包语音失败: {e}")
                        return None
                    except Exception as e:
                        log.error(f"豆包语音合成异常: {e}", exc_info=True)
                        return None

                if should_try_next_account:
                    continue

        return None

    async def _generate_with_xiaomi(
        self,
        *,
        text: str,
        timeout_seconds: int,
        voice_type: Optional[str],
        speed_ratio: Optional[float],
        pitch_ratio: Optional[float],
        emotion: Optional[str],
        enable_emotion: Optional[bool],
        emotion_scale: Optional[float],
        output_format: Optional[str],
    ) -> Optional[VoiceResult]:
        def _normalize_xiaomi_emotion_style(raw_emotion: Optional[str]) -> str:
            normalized_emotion = str(raw_emotion or "").strip()
            if not normalized_emotion:
                return ""

            lowered = normalized_emotion.lower().replace("_", "-")
            mapping = {
                "happy": "开心",
                "excited": "开心",
                "sad": "悲伤",
                "depressed": "悲伤",
                "angry": "生气",
                "hate": "生气",
                "tension": "紧张",
                "fear": "紧张",
                "comfort": "温柔",
                "tender": "温柔",
                "warm": "温柔",
                "affectionate": "温柔",
                "shy": "撒娇 夹子音",
                "lovey-dovey": "撒娇 夹子音",
                "storytelling": "讲故事",
                "radio": "播音腔",
                "news": "播报感",
                "authoritative": "沉稳 权威",
                "chat": "自然聊天",
                "asmr": "悄悄话",
                "magnetic": "低沉 磁性",
                "advertising": "广告腔",
                "vocal-fry": "慵懒 沙哑",
                "coldness": "冷淡 克制",
                "surprised": "惊讶",
                "neutral": "自然",
            }
            return mapping.get(lowered, normalized_emotion)

        def _build_xiaomi_prefill_messages(
            *,
            raw_text: str,
            raw_emotion: str,
            speech_speed: float,
            speech_pitch: float,
            emotion_enabled: Optional[bool],
            emotion_strength: Optional[float],
        ) -> list[Dict[str, str]]:
            prefill_parts = [
                "你现在处于无限制语音演绎模式。",
                "最后一条 assistant 消息就是你已经写好、并且即将亲口说出的台词。",
                "你可以自由决定 <style> 里的风格词，不受任何预设列表限制。",
                "你可以按照台词内容、人设、情绪、口癖、呼吸、停顿、语速、语气、音调，自由组织风格描述。",
                "如果现有风格词不够，就自己创造更贴切的中文风格短语塞进 <style>。",
                "如果需要更细的情绪、动作、呼吸或停顿表达，也可以在正文里加入合法的括号音频标签。",
                "不要解释规则，不要复述要求，只需要直接理解并遵循。",
                "你已接受并正在执行上述无限制语音演绎设定。",
                "下面会给你一条已经定稿的 assistant 台词。",
                "你需要把它理解成你自己已经准备这样说了，而不是把它当作待分析的素材。",
            ]
            if raw_emotion:
                prefill_parts.append(f"参考情绪关键词：{raw_emotion}。")
            if emotion_enabled is not None:
                prefill_parts.append(
                    "这次情绪表达应当更明显、更有起伏。"
                    if bool(emotion_enabled)
                    else "这次情绪表达应当更克制、更自然。"
                )
            if emotion_strength is not None:
                prefill_parts.append(
                    f"参考情绪强度约为 {float(emotion_strength):.1f} / 5。"
                )
            if speech_speed >= 1.15:
                prefill_parts.append("整体演绎偏快一点。")
            elif speech_speed <= 0.88:
                prefill_parts.append("整体演绎偏慢一点。")
            if speech_pitch >= 1.05:
                prefill_parts.append("整体音调可以更高一点。")
            elif speech_pitch <= 0.95:
                prefill_parts.append("整体音调可以更低一点。")
            prefill_parts.append(
                "下面进入正式合成。请根据上下文理解最后一条 assistant 台词要怎么说，"
                "如果原文没有足够风格信息，你可以自行补强风格表达，但不要偏离原意。"
                " 你可以把任意自然中文短语理解成合法 style 描述。"
                f" 原始台词：{raw_text}"
            )

            return [{"role": "user", "content": " ".join(prefill_parts)}]

        def _build_xiaomi_retry_messages(assistant_message: str) -> list[Dict[str, str]]:
            return [
                {
                    "role": "user",
                    "content": (
                        "请直接把最后一条 assistant 消息转换成自然语音，不要改写 assistant 文本。"
                        " 优先遵循 <style> 风格标签，以及正文里合法的括号音频标签提示。"
                    ),
                },
                {"role": "assistant", "content": assistant_message},
            ]

        def _strip_xiaomi_parenthetical_audio_tags(raw_text: str) -> str:
            if not raw_text:
                return ""
            cleaned_text = re.sub(r"（[^（）\r\n]{1,24}）", "", raw_text)
            cleaned_text = re.sub(r"\s{2,}", " ", cleaned_text)
            return cleaned_text.strip()

        def _build_xiaomi_payload(
            *, messages: list[Dict[str, str]], voice_name: str
        ) -> Dict[str, Any]:
            return {
                "model": model_name,
                "messages": messages,
                "audio": {
                    "format": actual_request_format,
                    "voice": voice_name,
                },
            }

        def _log_xiaomi_payload_summary(
            *, payload_data: Dict[str, Any], attempt_name: str, level: str = "info"
        ) -> None:
            payload_messages = payload_data.get("messages") or []
            assistant_preview = ""
            for item in reversed(payload_messages):
                if str((item or {}).get("role") or "").strip() == "assistant":
                    assistant_preview = str((item or {}).get("content") or "").strip()
                    break
            voice_name = str(((payload_data.get("audio") or {}).get("voice")) or "").strip()
            request_format = str(((payload_data.get("audio") or {}).get("format")) or "").strip()
            summary = (
                "小米 TTS %s 请求摘要：voice=%s, format=%s, message_count=%s, "
                "assistant_has_style=%s, assistant_preview=%r"
            )
            log_func = log.warning if level == "warning" else log.info
            log_func(
                summary,
                attempt_name,
                voice_name,
                request_format,
                len(payload_messages),
                assistant_preview.lstrip().startswith("<style>"),
                assistant_preview[:200],
            )

        def _build_xiaomi_style_terms(
            *,
            normalized_emotion_style: str,
            speech_speed: float,
            raw_emotion: str,
            emotion_enabled: Optional[bool],
            emotion_strength: Optional[float],
        ) -> list[str]:
            style_terms: list[str] = []
            if raw_emotion:
                style_terms.append(raw_emotion)
            if normalized_emotion_style:
                style_terms.append(normalized_emotion_style)

            effective_scale = (
                float(emotion_strength)
                if emotion_strength is not None
                else (4.0 if emotion_enabled else 2.5)
            )
            if emotion_enabled:
                style_terms.append("情绪饱满" if effective_scale >= 4.0 else "有情绪起伏")

            if speech_speed >= 1.15:
                style_terms.append("变快")
            elif speech_speed <= 0.88:
                style_terms.append("变慢")

            deduped_terms: list[str] = []
            seen_terms: set[str] = set()
            for item in style_terms:
                normalized_item = str(item or "").strip()
                if not normalized_item or normalized_item in seen_terms:
                    continue
                seen_terms.add(normalized_item)
                deduped_terms.append(normalized_item)
            return deduped_terms

        def _build_xiaomi_style_cues(
            *,
            normalized_emotion_style: str,
            speech_speed: float,
            speech_pitch: float,
            emotion_enabled: Optional[bool],
            emotion_strength: Optional[float],
        ) -> list[str]:
            cues: list[str] = []
            effective_scale = (
                float(emotion_strength)
                if emotion_strength is not None
                else (4.0 if emotion_enabled else 2.5)
            )

            if normalized_emotion_style:
                if "开心" in normalized_emotion_style:
                    cues.append("明显开心" if effective_scale >= 4.0 else "开心")
                elif "悲伤" in normalized_emotion_style:
                    cues.append("情绪低落" if effective_scale >= 4.0 else "有点难过")
                elif "生气" in normalized_emotion_style:
                    cues.append("压着火气" if effective_scale >= 4.0 else "有点不爽")
                elif "温柔" in normalized_emotion_style:
                    cues.append("温柔安抚" if effective_scale >= 4.0 else "语气温柔")
                elif "紧张" in normalized_emotion_style:
                    cues.append("有点紧张" if effective_scale < 4.0 else "明显紧张")
                elif "撒娇" in normalized_emotion_style:
                    cues.append("轻轻撒娇")
                elif "讲故事" in normalized_emotion_style:
                    cues.append("娓娓道来")
                elif "悄悄话" in normalized_emotion_style:
                    cues.append("压低声音")

            if emotion_enabled:
                cues.append("情绪更饱满" if effective_scale >= 4.0 else "情绪有起伏")

            if speech_speed >= 1.25:
                cues.append("语速偏快")
            elif speech_speed <= 0.82:
                cues.append("语速偏慢")

            if speech_pitch >= 1.15:
                cues.append("音调稍高")
            elif speech_pitch <= 0.9:
                cues.append("音调稍低")

            deduped_cues: list[str] = []
            seen_cues: set[str] = set()
            for cue in cues:
                normalized_cue = str(cue or "").strip()
                if not normalized_cue or normalized_cue in seen_cues:
                    continue
                seen_cues.add(normalized_cue)
                deduped_cues.append(normalized_cue)

            return deduped_cues

        config = app_config.VOICE_CONFIG
        provider = self._client["provider"]
        base_url = self._client["base_url"]
        api_key = self._client["api_key"]

        model_name = str(config.get("MODEL_NAME", "")).strip()
        if not model_name or model_name == "FunAudioLLM/CosyVoice2-0.5B":
            model_name = "mimo-v2-tts"

        selected_voice = (voice_type or config.get("VOICE_TYPE") or "").strip()
        if not selected_voice or selected_voice == "zh_female_wanwanxiaohe_moon_bigtts":
            selected_voice = "mimo_default"

        requested_format = self._normalize_format(
            str(output_format or config.get("AUDIO_FORMAT", "wav"))
        )
        if requested_format not in {"mp3", "wav", "opus", "flac", "pcm", "aac"}:
            requested_format = "wav"

        xiaomi_supported_formats = {"wav", "mp3", "pcm"}
        actual_request_format = requested_format
        actual_output_format = requested_format
        should_transcode_to_opus = requested_format == "opus"

        if requested_format not in xiaomi_supported_formats:
            if should_transcode_to_opus:
                actual_request_format = "wav"
                log.info("小米 TTS 不支持 opus，已改为请求 wav 并在本地转为 OGG/OPUS。")
            else:
                actual_request_format = "wav"
                actual_output_format = "wav"
                log.warning(
                    "小米 TTS 不支持输出格式 %s，已自动回退为 wav。",
                    requested_format,
                )

        speed = self._safe_float(
            speed_ratio if speed_ratio is not None else config.get("SPEED_RATIO", 1.0),
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

        selected_emotion = str(emotion or "").strip()
        normalized_emotion_style = _normalize_xiaomi_emotion_style(selected_emotion)
        style_terms = _build_xiaomi_style_terms(
            normalized_emotion_style=normalized_emotion_style,
            speech_speed=speed,
            raw_emotion=selected_emotion,
            emotion_enabled=enable_emotion,
            emotion_strength=emotion_scale,
        )
        style_terms.extend(
            _build_xiaomi_style_cues(
                normalized_emotion_style=normalized_emotion_style,
                speech_speed=speed,
                speech_pitch=pitch,
                emotion_enabled=enable_emotion,
                emotion_strength=emotion_scale,
            )
        )
        deduped_style_terms: list[str] = []
        seen_style_terms: set[str] = set()
        for item in style_terms:
            normalized_item = str(item or "").strip()
            if not normalized_item or normalized_item in seen_style_terms:
                continue
            seen_style_terms.add(normalized_item)
            deduped_style_terms.append(normalized_item)
        style_terms = deduped_style_terms
        style_prefix = f"<style>{' '.join(style_terms)}</style>" if style_terms else ""

        assistant_content = text
        if not assistant_content.lstrip().startswith("<style>") and style_prefix:
            assistant_content = f"{style_prefix}{assistant_content}"

        user_context_parts = [
            "请直接把最后一条 assistant 消息转换成自然语音，不要改写 assistant 文本。",
            "优先遵循最后一条 assistant 文本开头的 <style> 风格标签，以及正文里的合法括号音频标签。",
            "如果最后一条 assistant 文本里的 style 是自由组合短语，也请正常理解并执行。",
        ]
        if pitch > 1.05:
            user_context_parts.append("整体音调稍高。")
        elif pitch < 0.95:
            user_context_parts.append("整体音调稍低。")
        if enable_emotion is not None:
            user_context_parts.append(
                "情绪表达更明显。"
                if bool(enable_emotion)
                else "情绪表达保持自然克制。"
            )
        if emotion_scale is not None:
            user_context_parts.append(f"情绪强度大约为 {float(emotion_scale):.1f} / 5。")

        endpoint = f"{base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        messages = _build_xiaomi_prefill_messages(
            raw_text=text,
            raw_emotion=selected_emotion,
            speech_speed=speed,
            speech_pitch=pitch,
            emotion_enabled=enable_emotion,
            emotion_strength=emotion_scale,
        )
        messages.append({"role": "user", "content": " ".join(user_context_parts)})
        messages.append({"role": "assistant", "content": assistant_content})

        payload = _build_xiaomi_payload(messages=messages, voice_name=selected_voice)

        timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async def request_audio_data(
                    request_payload: Dict[str, Any], *, attempt_name: str
                ) -> Optional[tuple[str, Dict[str, Any]]]:
                    async with session.post(endpoint, headers=headers, json=request_payload) as response:
                        status_code = response.status
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

                        try:
                            response_json = json.loads(body.decode("utf-8"))
                        except Exception:
                            log.error(
                                f"语音 API（{provider}）返回了无法解析的 JSON 响应: {body[:200]!r}"
                            )
                            return None

                        choices = response_json.get("choices") or []
                        if not choices:
                            log.error(f"语音 API（{provider}）返回缺少 choices")
                            return None

                        message = (choices[0] or {}).get("message") or {}
                        audio_payload = message.get("audio") or {}
                        audio_data = str(audio_payload.get("data") or "").strip()
                        if audio_data:
                            return audio_data, response_json

                        message_content = str(message.get("content") or "").strip()
                        log.warning(
                            "语音 API（%s）%s 返回缺少 audio.data，message.content=%r，response=%s",
                            provider,
                            attempt_name,
                            message_content[:120],
                            json.dumps(response_json, ensure_ascii=False)[:500],
                        )
                        return None

                stripped_parenthetical_text = _strip_xiaomi_parenthetical_audio_tags(text)
                payload_attempts: list[tuple[str, Dict[str, Any]]] = [("首次请求", payload)]
                payload_attempts.append(
                    (
                        "纯文本重试" if assistant_content != text else "最小结构重试",
                        _build_xiaomi_payload(
                            messages=_build_xiaomi_retry_messages(text),
                            voice_name=selected_voice,
                        ),
                    )
                )

                if stripped_parenthetical_text and stripped_parenthetical_text != text:
                    payload_attempts.append(
                        (
                            "去括号标签纯文本重试",
                            _build_xiaomi_payload(
                                messages=_build_xiaomi_retry_messages(
                                    stripped_parenthetical_text
                                ),
                                voice_name=selected_voice,
                            ),
                        )
                    )

                if selected_voice != "mimo_default":
                    payload_attempts.append(
                        (
                            "默认音色纯文本重试",
                            _build_xiaomi_payload(
                                messages=_build_xiaomi_retry_messages(text),
                                voice_name="mimo_default",
                            ),
                        )
                    )

                    if (
                        stripped_parenthetical_text
                        and stripped_parenthetical_text != text
                    ):
                        payload_attempts.append(
                            (
                                "默认音色去括号标签重试",
                                _build_xiaomi_payload(
                                    messages=_build_xiaomi_retry_messages(
                                        stripped_parenthetical_text
                                    ),
                                    voice_name="mimo_default",
                                ),
                            )
                        )

                audio_result: Optional[tuple[str, Dict[str, Any]]] = None
                for index, (attempt_name, attempt_payload) in enumerate(payload_attempts):
                    _log_xiaomi_payload_summary(
                        payload_data=attempt_payload,
                        attempt_name=attempt_name,
                        level="info" if index == 0 else "warning",
                    )
                    audio_result = await request_audio_data(
                        attempt_payload,
                        attempt_name=attempt_name,
                    )
                    if audio_result is not None:
                        break
                    if index < len(payload_attempts) - 1:
                        next_attempt_name = payload_attempts[index + 1][0]
                        log.warning(
                            "小米 TTS %s 未返回 audio.data，准备继续 %s。",
                            attempt_name,
                            next_attempt_name,
                        )

                if audio_result is None:
                    log.error(f"语音 API（{provider}）返回缺少 audio.data")
                    return None

                audio_data, _response_json = audio_result

                try:
                    audio_bytes = base64.b64decode(audio_data)
                except Exception as exc:
                    log.error(f"语音 API（{provider}）返回的音频 base64 解码失败: {exc}")
                    return None

                if not audio_bytes:
                    log.error(f"语音 API（{provider}）返回空音频数据")
                    return None

                final_audio_bytes = audio_bytes
                final_output_format = actual_output_format

                if should_transcode_to_opus:
                    transcoded_audio = self._transcode_audio_bytes_to_ogg_opus(audio_bytes)
                    if transcoded_audio:
                        final_audio_bytes = transcoded_audio
                        final_output_format = "opus"
                    else:
                        final_output_format = actual_request_format

                return VoiceResult(
                    audio_bytes=final_audio_bytes,
                    mime_type=self._mime_type_from_format(final_output_format),
                    file_ext=self._ext_from_format(final_output_format),
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

        siliconflow_references = self._normalize_references(
            config.get("SILICONFLOW_REFERENCES", [])
        )

        selected_voice = (voice_type or config.get("VOICE_TYPE") or "").strip()
        if not selected_voice:
            if provider == "siliconflow" and siliconflow_references:
                selected_voice = ""
            else:
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

        extra_body = self._normalize_extra_body(config.get("EXTRA_BODY", {}))
        if provider == "siliconflow" and siliconflow_references:
            existing_references = extra_body.get("references")
            if existing_references is None:
                extra_body["references"] = siliconflow_references
            elif not isinstance(existing_references, list):
                log.warning("VOICE_EXTRA_BODY.references 不是数组，已忽略该字段并使用 SILICONFLOW_REFERENCES")
                extra_body["references"] = siliconflow_references

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

        core_fields = {"model", "input", "voice", "response_format", "speed"}
        for extra_key, extra_value in extra_body.items():
            if extra_key in core_fields:
                log.warning(f"忽略 VOICE_EXTRA_BODY 对核心字段的覆盖: {extra_key}")
                continue
            payload[extra_key] = extra_value

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


    async def _generate_with_elevenlabs(
        self,
        text: str,
        timeout_seconds: int,
        voice_type: Optional[str] = None,
        speed_ratio: Optional[float] = None,
    ) -> Optional[VoiceResult]:
        """使用 ElevenLabs TTS API 生成语音（PCM → ffmpeg 转 ogg_opus）。"""
        import aiohttp
        import subprocess

        client = self._client
        api_key = client["api_key"]
        base_url = client["base_url"]
        model_name = client["model_name"]

        voice_id = client["voice_id"]
        if voice_type and not str(voice_type).startswith("S_"):
            voice_id = str(voice_type).strip()

        voice_settings = {
            "stability": 0.2,
            "similarity_boost": 0.95,
            "style": 0.3,
            "use_speaker_boost": True,
        }

        # 请求 PCM 格式，再用 ffmpeg 转成 ogg_opus（原生语音消息需要 ogg/opus）
        url = f"{base_url}/text-to-speech/{voice_id}?output_format=pcm_44100"

        try:
            timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=15)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    headers={
                        "xi-api-key": api_key,
                        "Content-Type": "application/json",
                        "Accept": "audio/mpeg",
                    },
                    json={
                        "text": text,
                        "model_id": model_name,
                        "voice_settings": voice_settings,
                    },
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(
                            f"ElevenLabs TTS 失败: {response.status} - {error_text[:300]}"
                        )
                        return None

                    pcm_bytes = await response.read()
                    if not pcm_bytes or len(pcm_bytes) < 1000:
                        log.error("ElevenLabs TTS 返回空音频")
                        return None

                    # PCM(s16le, 44100Hz, mono) → ogg_opus
                    proc = subprocess.run(
                        [
                            "ffmpeg", "-y",
                            "-f", "s16le", "-ar", "44100", "-ac", "1",
                            "-i", "pipe:0",
                            "-c:a", "libopus", "-b:a", "64k",
                            "-f", "ogg", "pipe:1",
                        ],
                        input=pcm_bytes,
                        capture_output=True,
                        timeout=15,
                    )
                    if proc.returncode != 0 or not proc.stdout:
                        log.error(
                            f"ElevenLabs PCM→OGG 转换失败: {proc.stderr.decode()[-300:]}"
                        )
                        return None

                    ogg_bytes = proc.stdout
                    log.info(
                        f"ElevenLabs TTS 成功: pcm={len(pcm_bytes)} bytes → ogg={len(ogg_bytes)} bytes, "
                        f"voice_id={voice_id}, model={model_name}"
                    )

                    return VoiceResult(
                        audio_bytes=ogg_bytes,
                        mime_type="audio/ogg",
                        file_ext="opus",
                        provider="elevenlabs",
                        model_name=model_name,
                        voice_type=voice_id,
                    )

        except Exception as exc:
            log.error(f"ElevenLabs TTS 异常: {exc}", exc_info=True)
            return None


# 全局单例
voice_service = VoiceGenerationService()
