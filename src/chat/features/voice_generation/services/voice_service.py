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

    def _initialize_client(self):
        config = app_config.VOICE_CONFIG

        if not config.get("ENABLED", False):
            log.info("语音合成服务未启用")
            return

        provider = str(config.get("PROVIDER", "doubao")).strip().lower()
        if provider == "doubao":
            app_id = str(config.get("APP_ID", "")).strip()
            access_token = str(config.get("ACCESS_TOKEN", "")).strip()
            app_pool = self._normalize_doubao_app_pool(config.get("APP_POOL", []))
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
                "clone_voice_app_bindings": clone_voice_app_bindings,
                "cluster": cluster,
            }
            log.info(
                "语音服务已初始化（provider=doubao, app_pool=%s, clone_bindings=%s）",
                len(effective_pool),
                len(clone_voice_app_bindings),
            )
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
                emotion=emotion,
                enable_emotion=enable_emotion,
                emotion_scale=emotion_scale,
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

        selected_voice = (voice_type or config.get("VOICE_TYPE") or "").strip()
        if not selected_voice:
            selected_voice = "zh_female_wanwanxiaohe_moon_bigtts"

        is_clone_voice = self._is_doubao_clone_voice(selected_voice)
        app_pool = self._normalize_doubao_app_pool(self._client.get("app_pool", []))
        clone_voice_app_bindings = self._normalize_clone_voice_app_bindings(
            self._client.get("clone_voice_app_bindings", {})
        )
        fallback_app_id = str(self._client.get("app_id", "")).strip()
        fallback_access_token = str(self._client.get("access_token", "")).strip()
        app_id, access_token, account_route = self._select_doubao_credentials(
            selected_voice=selected_voice,
            is_clone_voice=is_clone_voice,
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
                is_clone_voice,
            )
            return None

        normalized_clone_resource_id = configured_clone_resource_id.lower()
        if is_clone_voice:
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
                configured_clone_resource_id = "seed-icl-2.0"
                normalized_clone_resource_id = configured_clone_resource_id
            elif (
                configured_clone_resource_id
                and not normalized_clone_resource_id.startswith("seed-icl-")
            ):
                log.warning(
                    "检测到 CLONE_RESOURCE_ID 非法值：%s，已自动回退为 seed-icl-2.0",
                    configured_clone_resource_id,
                )
                configured_clone_resource_id = "seed-icl-2.0"

        cluster_input = (
            configured_clone_cluster if is_clone_voice and configured_clone_cluster else configured_cluster
        )
        cluster = self._resolve_doubao_cluster(cluster_input, selected_voice)
        resource_id = (
            configured_clone_resource_id or "seed-icl-2.0"
            if is_clone_voice
            else "volc.megatts.default"
        )
        if cluster != cluster_input:
            log.info(
                "豆包 cluster 已自动调整: %s -> %s（voice_type=%s）",
                cluster_input or "<empty>",
                cluster,
                selected_voice,
            )
        log.info(
            "豆包语音路由: voice_type=%s, cluster=%s, resource_id=%s, clone_voice=%s, clone_cluster_config=%s, app_route=%s, app_id=%s",
            selected_voice,
            cluster,
            resource_id,
            is_clone_voice,
            configured_clone_cluster or "<empty>",
            account_route,
            app_id,
        )

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
            minimum=0.5 if is_clone_voice else 0.2,
            maximum=2.0 if is_clone_voice else 3.0,
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

        # 声音复刻链路按 ICL 文档白名单收敛参数：
        # /api/v1/tts 的 audio 仅传 voice_type / encoding / speed_ratio / loudness_ratio。
        # model_type=4 属于训练接口参数，合成阶段不透传，避免触发参数校验失败。
        audio_payload = {
            "voice_type": selected_voice,
            "encoding": encoding,
            "speed_ratio": speed,
            "loudness_ratio": volume,
        }

        if is_clone_voice:
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
                    "Resource-Id": resource_id,
                }
                # 关键：HTTP /api/v1/tts 复刻链路不携带 X-Api-Resource-Id。
                # 实测该头会导致 403(resource not granted)；最小必填请求仅需 Resource-Id。
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


# 全局单例
voice_service = VoiceGenerationService()