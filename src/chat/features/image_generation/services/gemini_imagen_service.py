# -*- coding: utf-8 -*-

"""
Gemini Imagen 图像生成服务
使用 Google Gemini API 的 Imagen 模型生成图像

支持两种模式：
1. Gemini 原生 generateImages API（用于官方 API 或支持此接口的代理）
2. OpenAI 兼容的 chat/completions API（用于通过聊天接口生成图像的代理）
"""

import logging
import asyncio
import aiohttp
import json
import re
from typing import Any, Dict, Optional, List
from concurrent.futures import ThreadPoolExecutor
import base64

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)


class GeminiImagenService:
    """
    封装 Gemini Imagen 图像生成功能的服务类
    
    支持两种 API 格式:
    - "gemini": 使用 Google genai SDK 的 generateImages API
    - "openai": 使用 OpenAI 兼容的 chat/completions API（支持图像生成的模型）
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=20)
        self._client = None
        self._api_format = "gemini"  # 默认使用 gemini 格式
        self._initialize_client()

    def _initialize_client(self):
        """初始化客户端"""
        config = app_config.GEMINI_IMAGEN_CONFIG
        
        if not config.get("ENABLED", False):
            log.warning("Gemini Imagen 服务已禁用")
            return
            
        api_key = config.get("API_KEY")
        if not api_key:
            log.error("未配置 Gemini Imagen API 密钥")
            return
        
        # 获取 API 格式配置
        self._api_format = config.get("API_FORMAT", "gemini").lower()
        base_url = config.get("BASE_URL")
        
        if self._api_format == "openai":
            # OpenAI 兼容模式：不需要 Google SDK，使用 aiohttp 直接调用
            self._client = {
                "api_key": api_key,
                "base_url": base_url or "https://api.openai.com/v1"
            }
            log.info(f"Imagen 服务已初始化 (OpenAI 兼容模式, 端点: {self._client['base_url']})")
        else:
            # Gemini 原生模式：使用 Google SDK
            try:
                from google import genai
                from google.genai import types
                
                if base_url:
                    http_options = types.HttpOptions(base_url=base_url)
                    self._client = genai.Client(api_key=api_key, http_options=http_options)
                    log.info(f"Gemini Imagen 客户端已初始化 (自定义端点: {base_url})")
                else:
                    self._client = genai.Client(api_key=api_key)
                    log.info("Gemini Imagen 客户端已初始化 (默认端点)")
            except Exception as e:
                log.error(f"初始化 Gemini Imagen 客户端失败: {e}")
                self._client = None

    def is_available(self) -> bool:
        """检查服务是否可用"""
        return (
            self._client is not None
            and app_config.GEMINI_IMAGEN_CONFIG.get("ENABLED", False)
        )

    def _build_openai_timeout(self, streaming: bool = False) -> aiohttp.ClientTimeout:
        """构建 OpenAI 兼容接口的超时配置。"""
        config = app_config.GEMINI_IMAGEN_CONFIG
        total_default = 180 if streaming else 120
        total_timeout = max(
            30,
            int(
                config.get(
                    "STREAMING_TIMEOUT_SECONDS" if streaming else "REQUEST_TIMEOUT_SECONDS",
                    total_default,
                )
            ),
        )
        connect_timeout = max(3, int(config.get("CONNECT_TIMEOUT_SECONDS", 15)))
        return aiohttp.ClientTimeout(
            total=total_timeout,
            connect=connect_timeout,
            sock_connect=connect_timeout,
            sock_read=total_timeout,
        )

    def _get_transient_retry_policy(self) -> tuple[int, float]:
        """获取瞬态错误重试策略。"""
        config = app_config.GEMINI_IMAGEN_CONFIG
        max_attempts = max(1, int(config.get("TRANSIENT_MAX_RETRIES", 2)))
        base_delay = max(
            0.2, float(config.get("TRANSIENT_RETRY_BASE_DELAY_SECONDS", 1.0))
        )
        return max_attempts, base_delay

    @staticmethod
    def _normalize_openai_image_api_mode(raw_mode: Optional[str]) -> str:
        """规范化 OpenAI 兼容图片接口模式。"""
        mode = str(raw_mode or "").strip().lower()
        if mode in {"images", "image", "images_api", "image_api"}:
            return "images_api"
        if mode in {"chat", "chat_completion", "chat_completions"}:
            return "chat_completions"
        return "auto"

    @staticmethod
    def _looks_like_images_api_model(model_name: str) -> bool:
        """判断模型是否更适合走 /images/* 接口。

        注意：`gpt-image-*` 虽然是 OpenAI 官方图片接口专用模型，但很多
        OpenAI 兼容代理（含部分自建网关）会同时暴露 chat/completions 与
        /images/* 路由。为避免把所有走 `gpt-image` 的代理都锁死在
        `/images/generations`，此处默认不再把 `gpt-image` 当作强制 images_api
        的信号；需要时可以通过 `OPENAI_IMAGE_API_MODE=images_api` 显式指定。
        """
        normalized = str(model_name or "").strip().lower()
        if not normalized:
            return False
        return normalized.startswith("grok-imagine")

    def _resolve_openai_image_api_mode(
        self,
        model_name: str,
        mode_override: Optional[str] = None,
    ) -> str:
        """根据模型和覆盖参数决定 OpenAI 兼容图片路由。"""
        configured_mode = self._normalize_openai_image_api_mode(
            mode_override or app_config.GEMINI_IMAGEN_CONFIG.get("OPENAI_IMAGE_API_MODE")
        )
        if configured_mode != "auto":
            return configured_mode
        if self._looks_like_images_api_model(model_name):
            return "images_api"
        return "chat_completions"

    def _should_keep_images_api_route(
        self,
        model_name: str,
        mode_override: Optional[str] = None,
    ) -> bool:
        """判断当前模型是否应固定停留在 `/images/*` 路由。"""
        resolved_mode = self._resolve_openai_image_api_mode(
            model_name=model_name,
            mode_override=mode_override,
        )
        if resolved_mode != "images_api":
            return False

        normalized_override = self._normalize_openai_image_api_mode(mode_override)
        if normalized_override == "images_api":
            return True

        return self._looks_like_images_api_model(model_name)

    @staticmethod
    def _coerce_optional_bool(value: Any) -> Optional[bool]:
        """把 bool / 文本 / 数字统一转成可选布尔值。"""
        if value is None or value == "":
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    @staticmethod
    def _resolve_image_size_for_resolution(
        resolution: str,
        openai_image_size: Optional[str] = None,
    ) -> Optional[str]:
        """当用户未手动指定 openai_image_size 时，根据 resolution 自动映射。"""
        if openai_image_size:
            return openai_image_size
        config = app_config.GEMINI_IMAGEN_CONFIG
        if resolution == "2k":
            return config.get("RESOLUTION_SIZE_2K") or "2048x2048"
        if resolution == "4k":
            return config.get("RESOLUTION_SIZE_4K") or "4096x4096"
        return None

    @staticmethod
    def _build_openai_image_prompt(
        prompt: str,
        negative_prompt: Optional[str] = None,
        aspect_ratio: Optional[str] = None,
    ) -> str:
        """把现有提示词语义压到图片接口支持的 prompt 文本里。"""
        lines = [str(prompt or "").strip()]
        if negative_prompt:
            lines.append(f"请避免：{str(negative_prompt).strip()}")
        if aspect_ratio and aspect_ratio != "1:1":
            lines.append(f"画面宽高比偏好：{aspect_ratio}")
        return "\n".join(line for line in lines if line)

    @staticmethod
    def _resolve_request_response_format(
        response_format: Optional[str],
    ) -> Optional[str]:
        """把内部响应格式映射成上游图片接口参数。"""
        normalized = str(response_format or "").strip().lower()
        if not normalized or normalized == "auto":
            return None
        if normalized == "base64":
            return "b64_json"
        if normalized in {"b64_json", "base64", "url"}:
            return normalized
        return None

    async def _parse_json_or_sse_payload(self, response: aiohttp.ClientResponse) -> Optional[dict]:
        """兼容普通 JSON 和 SSE 文本响应。"""
        response_text = await response.text()
        if not response_text:
            return None

        content_type = str(response.headers.get("Content-Type", "")).lower()
        stripped_text = response_text.lstrip()
        if "text/event-stream" in content_type or stripped_text.startswith("data: "):
            return await self._parse_sse_payload_text(response_text)

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as error:
            log.warning(
                f"解析 OpenAI 兼容图片响应失败: {error}; body_preview={response_text[:500]}"
            )
            return None

    async def _parse_sse_payload_text(self, raw_text: str) -> Optional[dict]:
        """解析 SSE 文本，尽量还原为可提图的统一结构。"""
        collected_parts: List[Dict[str, Any]] = []
        collected_content: List[str] = []
        collected_data_items: List[Dict[str, Any]] = []
        last_payload: Optional[dict] = None

        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.strip()
            if not line or not line.startswith("data: "):
                continue

            data_str = line[6:].strip()
            if not data_str or data_str == "[DONE]":
                continue

            try:
                payload = json.loads(data_str)
            except json.JSONDecodeError:
                log.debug(f"SSE 片段不是合法 JSON，已跳过: {data_str[:200]}")
                continue

            if not isinstance(payload, dict):
                continue

            last_payload = payload

            if isinstance(payload.get("data"), list):
                for item in payload["data"]:
                    if isinstance(item, dict):
                        collected_data_items.append(item)

            for choice in payload.get("choices", []):
                if not isinstance(choice, dict):
                    continue
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    continue

                content = delta.get("content")
                if isinstance(content, str):
                    collected_content.append(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            collected_parts.append(part)

                if isinstance(delta.get("parts"), list):
                    for part in delta["parts"]:
                        if isinstance(part, dict):
                            collected_parts.append(part)

        if collected_data_items:
            return {"data": collected_data_items}

        if collected_parts or collected_content:
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                collected_parts
                                if collected_parts
                                else "".join(collected_content)
                            )
                        }
                    }
                ]
            }

        return last_payload

    @staticmethod
    def _is_retryable_status(status_code: int) -> bool:
        """判断 HTTP 状态码是否属于可重试的瞬态错误。"""
        return status_code in {408, 425, 429, 500, 502, 503, 504, 520, 522, 524}
    
    def reload_config(self) -> dict:
        """
        热重载配置并重新初始化客户端
        
        Returns:
            包含重载状态的字典
        """
        try:
            # 重新读取环境变量
            from dotenv import load_dotenv
            load_dotenv(override=True)
            
            # 更新配置
            import os

            enabled_raw = (
                str(os.getenv("GEMINI_IMAGEN_ENABLED", "False"))
                .strip()
                .strip('"')
                .strip("'")
                .lower()
            )
            app_config.GEMINI_IMAGEN_CONFIG["ENABLED"] = enabled_raw == "true"
            app_config.GEMINI_IMAGEN_CONFIG["API_KEY"] = os.getenv("GEMINI_IMAGEN_API_KEY")
            app_config.GEMINI_IMAGEN_CONFIG["BASE_URL"] = os.getenv("GEMINI_IMAGEN_BASE_URL")
            app_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME"] = os.getenv("GEMINI_IMAGEN_MODEL", "imagen-3.0-generate-002")
            app_config.GEMINI_IMAGEN_CONFIG["OPENAI_IMAGE_API_MODE"] = os.getenv(
                "GEMINI_IMAGEN_OPENAI_IMAGE_API_MODE", "auto"
            )
            
            # 重新初始化客户端
            self._client = None
            self._initialize_client()
            
            if self.is_available():
                log.info("✅ Gemini Imagen 服务配置已热重载")
                return {"success": True, "message": "Imagen 服务已重新初始化", "available": True}
            else:
                return {"success": True, "message": "配置已更新但服务未启用", "available": False}
                
        except Exception as e:
            log.error(f"热重载 Imagen 配置失败: {e}")
            return {"success": False, "error": str(e)}
    
    def update_config(
        self,
        enabled: bool = None,
        api_key: str = None,
        base_url: str = None,
        model_name: str = None,
        openai_image_api_mode: str = None,
    ) -> dict:
        """
        更新配置并重新初始化
        
        Args:
            enabled: 是否启用服务
            api_key: API 密钥
            base_url: 自定义端点 URL
            model_name: 模型名称
            
        Returns:
            包含更新状态的字典
        """
        try:
            import os
            
            if enabled is not None:
                app_config.GEMINI_IMAGEN_CONFIG["ENABLED"] = enabled
                os.environ["GEMINI_IMAGEN_ENABLED"] = str(enabled).lower()
            
            if api_key is not None:
                app_config.GEMINI_IMAGEN_CONFIG["API_KEY"] = api_key
                os.environ["GEMINI_IMAGEN_API_KEY"] = api_key
            
            if base_url is not None:
                app_config.GEMINI_IMAGEN_CONFIG["BASE_URL"] = base_url
                os.environ["GEMINI_IMAGEN_BASE_URL"] = base_url
            
            if model_name is not None:
                app_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME"] = model_name
                os.environ["GEMINI_IMAGEN_MODEL"] = model_name

            if openai_image_api_mode is not None:
                normalized_mode = self._normalize_openai_image_api_mode(openai_image_api_mode)
                app_config.GEMINI_IMAGEN_CONFIG["OPENAI_IMAGE_API_MODE"] = normalized_mode
                os.environ["GEMINI_IMAGEN_OPENAI_IMAGE_API_MODE"] = normalized_mode
            
            # 重新初始化客户端
            self._client = None
            self._initialize_client()
            
            if self.is_available():
                log.info("✅ Gemini Imagen 配置已更新并重新初始化")
                return {"success": True, "message": "Imagen 服务已更新并启用", "available": True}
            elif app_config.GEMINI_IMAGEN_CONFIG.get("ENABLED"):
                return {"success": False, "message": "配置已更新但客户端初始化失败", "available": False}
            else:
                return {"success": True, "message": "配置已更新，服务已禁用", "available": False}
                
        except Exception as e:
            log.error(f"更新 Imagen 配置失败: {e}")
            return {"success": False, "error": str(e)}

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        aspect_ratio: str = "1:1",
        number_of_images: int = 1,
        resolution: str = "default",
        content_rating: str = "sfw",
        model_name_override: Optional[str] = None,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
    ) -> Optional[List[bytes]]:
        """
        使用 Gemini Imagen 生成图像

        Args:
            prompt: 正面提示词（支持中文自然语言）
            negative_prompt: 负面提示词（可选，支持中文）
            aspect_ratio: 宽高比，支持 "1:1", "3:4", "4:3", "9:16", "16:9"
            number_of_images: 生成图片数量（1-4）
            resolution: 分辨率 ("default", "2k", "4k")
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)

        Returns:
            成功时返回图像字节数据列表，失败时返回 None
        """
        if not self.is_available():
            log.error("Gemini Imagen 服务不可用")
            return None

        config = app_config.GEMINI_IMAGEN_CONFIG
        # 根据分辨率和内容分级选择模型
        model_name = (
            str(model_name_override).strip()
            if model_name_override is not None and str(model_name_override).strip()
            else self._get_model_for_resolution(
                resolution=resolution,
                is_edit=False,
                content_rating=content_rating,
            )
        )
        log.info(f"使用模型 {model_name} 生成图像 (分辨率: {resolution}, 内容分级: {content_rating})")

        # 分辨率自动映射 openai_image_size
        resolved_image_size = self._resolve_image_size_for_resolution(
            resolution=resolution,
            openai_image_size=openai_image_size,
        )

        # 根据 API 格式选择不同的生成方法
        # gemini_chat: 使用 Gemini SDK 的 generate_content 多模态聊天接口
        # gemini: 使用 Gemini SDK 的 generate_images 专用接口
        # openai: 使用 OpenAI 兼容的 chat/completions 接口
        if self._api_format == "openai":
            return await self._generate_image_openai_format(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
                reference_image_bytes=reference_image_bytes,
                reference_image_mime=reference_image_mime,
                openai_image_size=resolved_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
                openai_image_api_mode=openai_image_api_mode,
            )
        elif self._api_format == "gemini_chat":
            # 使用 Gemini 多模态聊天接口生成图像
            return await self._generate_image_gemini_chat_format(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
                reference_image_bytes=reference_image_bytes,
                reference_image_mime=reference_image_mime,
            )
        else:
            # 默认使用 Gemini generateImages 专用接口
            return await self._generate_image_gemini_format(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
                config=config,
            )
    
    async def _generate_image_gemini_format(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        config: dict,
    ) -> Optional[List[bytes]]:
        """使用 Gemini 原生 generateImages API 生成图像"""
        try:
            from google.genai import types
            
            loop = asyncio.get_event_loop()
            
            # 构建生成配置
            generate_config = {
                "number_of_images": min(max(1, number_of_images), 4),
                "aspect_ratio": aspect_ratio,
                "safety_filter_level": config.get("SAFETY_FILTER_LEVEL", "BLOCK_LOW_AND_ABOVE"),
                "person_generation": config.get("PERSON_GENERATION", "ALLOW_ADULT"),
            }
            
            if negative_prompt:
                generate_config["negative_prompt"] = negative_prompt

            log.info(f"[Gemini格式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

            # 在线程池中执行同步 API 调用
            response = await loop.run_in_executor(
                self.executor,
                lambda: self._client.models.generate_images(
                    model=model_name,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(**generate_config),
                ),
            )

            if response and response.generated_images:
                images = []
                for generated_image in response.generated_images:
                    # 图像数据通常是 base64 编码的
                    if hasattr(generated_image, 'image') and generated_image.image:
                        if hasattr(generated_image.image, 'image_bytes'):
                            images.append(generated_image.image.image_bytes)
                        elif hasattr(generated_image.image, 'data'):
                            # 如果是 base64 编码
                            image_data = base64.b64decode(generated_image.image.data)
                            images.append(image_data)
                
                if images:
                    log.info(f"成功生成 {len(images)} 张图像")
                    return images
                else:
                    log.warning("API 返回成功但没有可用的图像数据")
                    return None
            else:
                log.warning("图像生成失败: API 返回空响应")
                return None

        except Exception as e:
            log.error(f"Gemini Imagen 生成图像时发生错误: {e}", exc_info=True)
            return None
    
    async def _generate_image_gemini_chat_format(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
    ) -> Optional[List[bytes]]:
        """
        使用 Gemini SDK 的 generate_content 多模态聊天接口生成图像
        适用于支持图像生成的 Gemini 模型（如 gemini-2.0-flash-exp, gemini-2.5-flash 等）
        支持流式请求以更快获取响应
        """
        config = app_config.GEMINI_IMAGEN_CONFIG
        streaming_enabled = config.get("STREAMING_ENABLED", False)

        if streaming_enabled:
            return await self._generate_image_gemini_chat_format_streaming(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
                reference_image_bytes=reference_image_bytes,
                reference_image_mime=reference_image_mime,
            )

        # 非流式请求的原有逻辑
        try:
            from google.genai import types

            loop = asyncio.get_event_loop()

            # 构建提示词
            full_prompt = f"请生成一张图片：{prompt}"
            if negative_prompt:
                full_prompt += f"\n\n请避免包含以下元素：{negative_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n\n图片宽高比：{aspect_ratio}"

            log.info(f"[Gemini Chat格式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

            # 构建消息内容（支持多模态：文本 + 可选参考图）
            contents = [full_prompt]
            if reference_image_bytes:
                from google.genai import types as genai_types
                contents = [
                    genai_types.Part.from_bytes(data=reference_image_bytes, mime_type=reference_image_mime),
                    full_prompt,
                ]

            # 使用 generate_content 多模态接口
            def _sync_generate():
                safety_settings = [
                    types.SafetySetting(
                        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HATE_SPEECH",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                ]

                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    safety_settings=safety_settings,
                )

                response = self._client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=config,
                )
                return response

            response = await loop.run_in_executor(self.executor, _sync_generate)

            images = self._extract_images_from_gemini_response(response)

            if images:
                log.info(f"成功生成 {len(images)} 张图像")
                return images
            else:
                log.warning("API 返回成功但没有找到图像数据")
                if response:
                    log.debug(f"响应类型: {type(response)}")
                    if hasattr(response, 'text'):
                        log.debug(f"响应文本: {response.text[:500] if response.text else 'None'}")
                return None

        except Exception as e:
            log.error(f"Gemini Chat 格式生成图像时发生错误: {e}", exc_info=True)
            return None

    async def _generate_image_gemini_chat_format_streaming(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
    ) -> Optional[List[bytes]]:
        """
        使用 Gemini SDK 的流式 generate_content 接口生成图像
        通过流式传输可以更快地获取响应

        修复: 完整收集所有 chunk 后再解析图像，避免分片数据问题
        """
        try:
            from google.genai import types

            loop = asyncio.get_event_loop()

            # 构建消息内容（支持多模态）
            full_prompt = f"请生成一张图片：{prompt}"
            if negative_prompt:
                full_prompt += f"\n\n请避免包含以下元素：{negative_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n\n图片宽高比：{aspect_ratio}"

            contents = [full_prompt]
            if reference_image_bytes:
                from google.genai import types as genai_types
                contents = [
                    genai_types.Part.from_bytes(data=reference_image_bytes, mime_type=reference_image_mime),
                    full_prompt,
                ]

            log.info(f"[Gemini Chat格式-流式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

            # 使用流式 generate_content 接口
            def _sync_generate_stream():
                # 配置生成参数，请求返回图像
                safety_settings = [
                    types.SafetySetting(
                        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HATE_SPEECH",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                ]
                
                gen_config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],  # 请求返回图像
                    safety_settings=safety_settings,
                )
                
                # 使用流式生成，但收集完整响应后再处理
                collected_images = []
                chunk_count = 0
                all_parts_data = []  # 收集所有 parts 用于后续聚合
                
                try:
                    stream_response = self._client.models.generate_content_stream(
                        model=model_name,
                        contents=contents,
                        config=gen_config,
                    )
                    
                    for chunk in stream_response:
                        chunk_count += 1
                        
                        # 从每个 chunk 中提取图像数据
                        if chunk and hasattr(chunk, 'candidates'):
                            for candidate in chunk.candidates:
                                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                                    for part in candidate.content.parts:
                                        # 记录 part 信息用于调试
                                        part_info = {
                                            'has_inline_data': hasattr(part, 'inline_data') and part.inline_data is not None,
                                            'has_text': hasattr(part, 'text') and part.text is not None,
                                        }
                                        all_parts_data.append(part_info)
                                        
                                        if hasattr(part, 'inline_data') and part.inline_data:
                                            inline_data = part.inline_data
                                            if hasattr(inline_data, 'data') and inline_data.data:
                                                if isinstance(inline_data.data, str):
                                                    try:
                                                        collected_images.append(base64.b64decode(inline_data.data))
                                                        log.debug(f"从 chunk {chunk_count} 解码 base64 图像成功")
                                                    except Exception as decode_err:
                                                        log.warning(f"解码 base64 图像失败: {decode_err}")
                                                elif isinstance(inline_data.data, bytes):
                                                    collected_images.append(inline_data.data)
                                                    log.debug(f"从 chunk {chunk_count} 获取 bytes 图像成功")
                        
                        # 也检查 chunk.parts（某些 SDK 版本）
                        if hasattr(chunk, 'parts'):
                            for part in chunk.parts:
                                if hasattr(part, 'inline_data') and part.inline_data:
                                    inline_data = part.inline_data
                                    if hasattr(inline_data, 'data') and inline_data.data:
                                        if isinstance(inline_data.data, str):
                                            try:
                                                collected_images.append(base64.b64decode(inline_data.data))
                                            except Exception as decode_err:
                                                log.warning(f"解码 chunk.parts 中的 base64 图像失败: {decode_err}")
                                        elif isinstance(inline_data.data, bytes):
                                            collected_images.append(inline_data.data)
                    
                    log.debug(f"流式接收完成: 共 {chunk_count} 个 chunk, parts 信息: {all_parts_data[:5]}")
                    
                except Exception as stream_error:
                    log.warning(f"流式生成过程中发生错误: {stream_error}", exc_info=True)
                
                return collected_images
            
            images = await loop.run_in_executor(self.executor, _sync_generate_stream)
            
            if images:
                log.info(f"[流式] 成功生成 {len(images)} 张图像")
                return images
            else:
                log.warning("[流式] API 返回成功但没有找到图像数据")
                # 尝试回退到非流式模式
                log.info("[流式] 尝试回退到非流式模式...")
                return await self._generate_image_gemini_chat_format_non_streaming_fallback(
                    prompt=prompt,
                    negative_prompt=negative_prompt,
                    aspect_ratio=aspect_ratio,
                    number_of_images=number_of_images,
                    model_name=model_name,
                )

        except Exception as e:
            log.error(f"Gemini Chat 格式流式生成图像时发生错误: {e}", exc_info=True)
            return None

    async def _generate_image_gemini_chat_format_non_streaming_fallback(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
    ) -> Optional[List[bytes]]:
        """
        非流式模式的回退方法，当流式模式失败时使用
        """
        try:
            from google.genai import types
            
            loop = asyncio.get_event_loop()
            
            # 构建提示词
            full_prompt = f"请生成一张图片：{prompt}"
            if negative_prompt:
                full_prompt += f"\n\n请避免包含以下元素：{negative_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n\n图片宽高比：{aspect_ratio}"

            log.info(f"[Gemini Chat格式-非流式回退] 正在使用 {model_name} 生成图像...")

            def _sync_generate():
                safety_settings = [
                    types.SafetySetting(
                        category="HARM_CATEGORY_SEXUALLY_EXPLICIT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HATE_SPEECH",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_HARASSMENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                    types.SafetySetting(
                        category="HARM_CATEGORY_DANGEROUS_CONTENT",
                        threshold="BLOCK_ONLY_HIGH"
                    ),
                ]
                
                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                    safety_settings=safety_settings,
                )
                
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=config,
                )
                return response
            
            response = await loop.run_in_executor(self.executor, _sync_generate)
            
            # 解析响应，提取图像
            images = self._extract_images_from_gemini_response(response)
            
            if images:
                log.info(f"[非流式回退] 成功生成 {len(images)} 张图像")
                return images
            else:
                log.warning("[非流式回退] 也没有找到图像数据")
                if response:
                    log.debug(f"响应类型: {type(response)}")
                    if hasattr(response, 'text'):
                        log.debug(f"响应文本: {response.text[:500] if response.text else 'None'}")
                return None

        except Exception as e:
            log.error(f"Gemini Chat 非流式回退生成图像时发生错误: {e}", exc_info=True)
            return None

    def _extract_images_from_gemini_response(self, response) -> List[bytes]:
        """
        从 Gemini SDK 响应中提取图像数据
        """
        images = []
        if response and hasattr(response, 'candidates'):
            for candidate in response.candidates:
                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                    for part in candidate.content.parts:
                        # 检查是否是图像数据
                        if hasattr(part, 'inline_data') and part.inline_data:
                            inline_data = part.inline_data
                            if hasattr(inline_data, 'data') and inline_data.data:
                                # 如果是 base64 编码的字符串
                                if isinstance(inline_data.data, str):
                                    images.append(base64.b64decode(inline_data.data))
                                else:
                                    # 如果已经是字节数据
                                    images.append(inline_data.data)
        
        # 也检查 response.parts（某些版本的 SDK）
        if not images and hasattr(response, 'parts'):
            for part in response.parts:
                if hasattr(part, 'inline_data') and part.inline_data:
                    inline_data = part.inline_data
                    if hasattr(inline_data, 'data') and inline_data.data:
                        if isinstance(inline_data.data, str):
                            images.append(base64.b64decode(inline_data.data))
                        else:
                            images.append(inline_data.data)
        
        return images
    
    async def _generate_image_openai_format(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
    ) -> Optional[List[bytes]]:
        """
        根据模型和配置决定走 OpenAI 兼容的 chat/completions 还是 /images/generations。
        """
        resolved_mode = self._resolve_openai_image_api_mode(
            model_name=model_name,
            mode_override=openai_image_api_mode,
        )
        if reference_image_bytes and resolved_mode == "images_api":
            base_edit_prompt = self._build_openai_image_prompt(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=None,
            )
            edit_prompt = (
                "必须以参考图里的真实食物为准，保留食物的主要外观、颜色、摆盘和份量；"
                "不要凭空改成空盘、甜点或其他食物。\n"
                f"{base_edit_prompt}"
            )
            try:
                edited_image = await self._edit_image_openai_images_api_format(
                    reference_images=[
                        {
                            "data": reference_image_bytes,
                            "mime_type": reference_image_mime,
                        }
                    ],
                    edit_prompt=edit_prompt,
                    aspect_ratio=aspect_ratio,
                    model_name=model_name,
                    openai_image_size=openai_image_size,
                    openai_response_format=openai_response_format,
                    # 参考图必须真实进入 multipart image 字段，先禁用流式避免网关丢图。
                    openai_stream=False,
                    openai_quality=openai_quality,
                    openai_style=openai_style,
                )
            except Exception as exc:
                log.warning(
                    "OpenAI /images/edits 参考图路由异常，将按配置决定是否回退: %s",
                    exc,
                    exc_info=True,
                )
                edited_image = None
            if edited_image:
                return [edited_image]
            if self._normalize_openai_image_api_mode(openai_image_api_mode) != "auto":
                return None
            log.warning(
                "OpenAI /images/edits 参考图路由未拿到结果，"
                "回退到 chat/completions 多模态路由再试一次。"
            )
            resolved_mode = "chat_completions"

        if resolved_mode == "images_api":
            images = await self._generate_image_openai_images_api_format(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
                openai_image_size=openai_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
            )
            if images or self._normalize_openai_image_api_mode(openai_image_api_mode) != "auto":
                return images
            if self._should_keep_images_api_route(
                model_name=model_name,
                mode_override=openai_image_api_mode,
            ):
                log.warning(
                    "检测到 Grok Imagine 模型，文生图将固定使用 /images/generations；"
                    "本次未回收到图像结果，已跳过 chat/completions 回退。"
                )
                return None
            log.warning(
                "OpenAI 图片接口 auto 路由未拿到结果，回退到 chat/completions 再试一次。"
            )

        return await self._generate_image_openai_chat_completions_format(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
            number_of_images=number_of_images,
            model_name=model_name,
            reference_image_bytes=reference_image_bytes,
            reference_image_mime=reference_image_mime,
            openai_response_format=openai_response_format,
            openai_stream=openai_stream,
        )

    async def _generate_image_openai_chat_completions_format(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
    ) -> Optional[List[bytes]]:
        """使用 chat/completions 兼容接口生成图像。"""
        base_url = self._client["base_url"].rstrip("/")
        api_key = self._client["api_key"]
        config = app_config.GEMINI_IMAGEN_CONFIG
        stream_override = self._coerce_optional_bool(openai_stream)
        streaming_enabled = (
            config.get("STREAMING_ENABLED", False)
            if stream_override is None
            else stream_override
        )

        # 构建提示词
        full_prompt = f"请生成一张图片：{prompt}"
        if negative_prompt:
            full_prompt += f"\n请避免：{negative_prompt}"
        if aspect_ratio != "1:1":
            full_prompt += f"\n宽高比：{aspect_ratio}"

        # 构建消息内容（支持多模态：文本 + 可选参考图）
        user_content = None
        if reference_image_bytes:
            import base64
            user_content = []
            user_content.append({"type": "text", "text": full_prompt})
            image_b64 = base64.b64encode(reference_image_bytes).decode("utf-8")
            user_content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{reference_image_mime};base64,{image_b64}"}
            })
            log.info("[OpenAI格式] 已附加参考图作为视觉参考")

        log.info(f"[OpenAI格式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

        if reference_image_bytes and streaming_enabled:
            log.info(
                "检测到参考图，关闭 OpenAI 图片流式路由以保留多模态 image_url 输入。"
            )
            streaming_enabled = False

        if streaming_enabled:
            return await self._generate_image_openai_format_streaming(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
            )

        # 构建请求
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": user_content if user_content else full_prompt
                }
            ],
            "max_tokens": 4096,
        }

        retry_max_attempts, retry_base_delay = self._get_transient_retry_policy()
        timeout = self._build_openai_timeout(streaming=False)

        for attempt in range(1, retry_max_attempts + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=timeout
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            if self._is_retryable_status(response.status) and attempt < retry_max_attempts:
                                delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                                log.warning(
                                    f"OpenAI API 返回可重试错误 {response.status}，将在 {delay:.1f}s 后重试 "
                                    f"({attempt}/{retry_max_attempts})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            log.error(f"OpenAI API 返回错误 {response.status}: {error_text[:500]}")
                            return None

                        data = await self._parse_json_or_sse_payload(response)
                        if not data:
                            log.warning("OpenAI chat/completions 返回空响应")
                            return None

                        # 解析响应，提取图像
                        images = await self._extract_images_from_openai_response(
                            data,
                            response_format_override=openai_response_format,
                        )

                        if images:
                            log.info(f"成功生成 {len(images)} 张图像")
                            return images

                        log.warning("API 返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None

            except asyncio.TimeoutError:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI API 请求超时，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error("OpenAI API 请求超时")
                return None
            except aiohttp.ClientError as e:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI API 网络错误: {e}，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(f"OpenAI API 网络错误: {e}")
                return None
            except Exception as e:
                log.error(f"OpenAI 格式生成图像时发生错误: {e}", exc_info=True)
                return None

        return None

    async def _generate_image_openai_images_api_format(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
    ) -> Optional[List[bytes]]:
        """使用 /images/generations 接口生成图像。"""
        base_url = self._client["base_url"].rstrip("/")
        api_key = self._client["api_key"]
        config = app_config.GEMINI_IMAGEN_CONFIG
        stream_override = self._coerce_optional_bool(openai_stream)
        streaming_enabled = (
            config.get("STREAMING_ENABLED", False)
            if stream_override is None
            else stream_override
        )
        request_response_format = self._resolve_request_response_format(
            openai_response_format or config.get("IMAGE_RESPONSE_FORMAT")
        )

        payload: Dict[str, Any] = {
            "model": model_name,
            "prompt": self._build_openai_image_prompt(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
            ),
            "n": min(max(1, int(number_of_images or 1)), 10),
        }

        if openai_image_size:
            payload["size"] = str(openai_image_size).strip()
        if request_response_format:
            payload["response_format"] = request_response_format
        if streaming_enabled:
            payload["stream"] = True
        if openai_quality:
            payload["quality"] = str(openai_quality).strip()
        if openai_style:
            payload["style"] = str(openai_style).strip()

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if streaming_enabled:
            headers["Accept"] = "text/event-stream"

        retry_max_attempts, retry_base_delay = self._get_transient_retry_policy()
        timeout = self._build_openai_timeout(streaming=streaming_enabled)

        for attempt in range(1, retry_max_attempts + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{base_url}/images/generations",
                        headers=headers,
                        json=payload,
                        timeout=timeout,
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            if self._is_retryable_status(response.status) and attempt < retry_max_attempts:
                                delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                                log.warning(
                                    f"OpenAI 图片接口返回可重试错误 {response.status}，将在 {delay:.1f}s 后重试 "
                                    f"({attempt}/{retry_max_attempts})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            log.error(f"OpenAI 图片接口返回错误 {response.status}: {error_text[:500]}")
                            return None

                        data = await self._parse_json_or_sse_payload(response)
                        if not data:
                            log.warning("OpenAI 图片接口返回空响应")
                            return None

                        images = await self._extract_images_from_openai_response(
                            data,
                            response_format_override=openai_response_format,
                        )
                        if images:
                            log.info(f"通过 /images/generations 成功生成 {len(images)} 张图像")
                            return images

                        log.warning("OpenAI 图片接口返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None

            except asyncio.TimeoutError:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图片接口请求超时，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error("OpenAI 图片接口请求超时")
                return None
            except aiohttp.ClientError as error:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图片接口网络错误: {error}，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(f"OpenAI 图片接口网络错误: {error}")
                return None
            except Exception as error:
                log.error(f"调用 /images/generations 时发生错误: {error}", exc_info=True)
                return None

        return None

    async def _generate_image_openai_format_streaming(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        aspect_ratio: str,
        number_of_images: int,
        model_name: str,
    ) -> Optional[List[bytes]]:
        """
        使用 OpenAI 兼容的 chat/completions API 以流式方式生成图像
        通过 SSE (Server-Sent Events) 接收数据
        
        修复: 正确使用 readline() 按行读取 SSE 数据，避免分片问题
        """
        try:
            base_url = self._client["base_url"].rstrip("/")
            api_key = self._client["api_key"]
            
            # 构建提示词
            full_prompt = f"请生成一张图片：{prompt}"
            if negative_prompt:
                full_prompt += f"\n请避免：{negative_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n宽高比：{aspect_ratio}"
            
            log.info(f"[OpenAI格式-流式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")
            
            # 构建请求
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            }
            
            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": full_prompt
                    }
                ],
                "max_tokens": 4096,
                "stream": True,  # 启用流式传输
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=self._build_openai_timeout(streaming=True)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(f"OpenAI API 流式请求返回错误 {response.status}: {error_text[:500]}")
                        return None
                    
                    # 收集流式响应数据
                    collected_content = []
                    collected_parts = []
                    # 用于累积 inline_data 中可能分片的 base64 数据
                    partial_inline_data = {}
                    
                    # 使用缓冲区正确处理 SSE 按行读取
                    buffer = ""
                    chunk_count = 0
                    
                    async for raw_chunk in response.content.iter_any():
                        chunk_count += 1
                        try:
                            chunk_text = raw_chunk.decode('utf-8')
                        except UnicodeDecodeError:
                            # 可能是二进制数据块，跳过
                            continue
                        
                        buffer += chunk_text
                        
                        # 按行处理缓冲区中的完整行
                        while '\n' in buffer:
                            line, buffer = buffer.split('\n', 1)
                            line = line.strip()
                            
                            # 跳过空行和注释
                            if not line or line.startswith(':'):
                                continue
                            
                            # 处理 SSE 数据格式
                            if line.startswith('data: '):
                                data_str = line[6:]  # 移除 'data: ' 前缀
                                
                                # 检查是否是结束标记
                                if data_str == '[DONE]':
                                    log.debug("收到流式响应结束标记 [DONE]")
                                    break
                                
                                try:
                                    chunk = json.loads(data_str)
                                    
                                    # 处理流式响应块
                                    if "choices" in chunk:
                                        for choice in chunk["choices"]:
                                            delta = choice.get("delta", {})
                                            
                                            # 收集文本内容
                                            if "content" in delta:
                                                content = delta["content"]
                                                if isinstance(content, str):
                                                    collected_content.append(content)
                                                elif isinstance(content, list):
                                                    # 处理多模态内容（可能包含图像）
                                                    for part in content:
                                                        if isinstance(part, dict):
                                                            collected_parts.append(part)
                                            
                                            # 某些实现可能在 delta 中直接包含 parts
                                            if "parts" in delta:
                                                for part in delta["parts"]:
                                                    if isinstance(part, dict):
                                                        collected_parts.append(part)
                                            
                                            # 检查完成原因
                                            finish_reason = choice.get("finish_reason")
                                            if finish_reason:
                                                log.debug(f"流式响应完成，原因: {finish_reason}")
                                    
                                except json.JSONDecodeError as e:
                                    log.warning(f"解析流式响应块失败: {e}, 数据长度: {len(data_str)}")
                                    continue
                    
                    # 处理缓冲区中剩余的数据
                    if buffer.strip():
                        line = buffer.strip()
                        if line.startswith('data: '):
                            data_str = line[6:]
                            if data_str != '[DONE]':
                                try:
                                    chunk = json.loads(data_str)
                                    if "choices" in chunk:
                                        for choice in chunk["choices"]:
                                            delta = choice.get("delta", {})
                                            if "content" in delta:
                                                content = delta["content"]
                                                if isinstance(content, str):
                                                    collected_content.append(content)
                                                elif isinstance(content, list):
                                                    for part in content:
                                                        if isinstance(part, dict):
                                                            collected_parts.append(part)
                                except json.JSONDecodeError:
                                    pass
                    
                    log.info(f"流式接收完成: 共 {chunk_count} 个数据块, {len(collected_parts)} 个 parts, {len(collected_content)} 个内容片段")
                    
                    # 尝试从收集的数据中提取图像
                    images = []
                    
                    # 从收集的 parts 中提取图像
                    for part in collected_parts:
                        inline_data = part.get("inline_data") or part.get("inlineData")
                        if inline_data:
                            image_b64 = inline_data.get("data")
                            if image_b64:
                                try:
                                    images.append(base64.b64decode(image_b64))
                                except Exception as e:
                                    log.warning(f"解码图像数据失败: {e}")
                        
                        # 检查 image_url 格式
                        if "image_url" in part:
                            url_data = part["image_url"]
                            if isinstance(url_data, dict) and "url" in url_data:
                                url = url_data["url"]
                                if url.startswith("data:image"):
                                    try:
                                        b64_data = url.split(",", 1)[1]
                                        images.append(base64.b64decode(b64_data))
                                    except Exception as e:
                                        log.warning(f"解码 image_url 数据失败: {e}")
                                elif url.startswith("http://") or url.startswith("https://"):
                                    # 下载 HTTP URL 图片
                                    downloaded = await self._download_image_from_url(url)
                                    if downloaded:
                                        images.append(downloaded)
                    
                    # 如果 parts 中没找到图像，尝试从完整的文本内容中提取
                    if not images and collected_content:
                        full_content = ''.join(collected_content)
                        log.debug(f"尝试从文本内容中提取图像, 内容长度: {len(full_content)}")
                        
                        # 尝试解析可能的 JSON 响应（某些模型可能在文本中返回 base64）
                        try:
                            content_data = json.loads(full_content)
                            if isinstance(content_data, list):
                                for item in content_data:
                                    if isinstance(item, dict):
                                        inline_data = item.get("inline_data") or item.get("inlineData")
                                        if inline_data and inline_data.get("data"):
                                            images.append(base64.b64decode(inline_data["data"]))
                        except (json.JSONDecodeError, TypeError):
                            pass  # 内容不是 JSON，忽略
                    
                    # 如果仍没找到图像，尝试从文本中提取 URL 并下载
                    if not images and collected_content:
                        full_content = ''.join(collected_content)
                        url_images = await self._extract_and_download_urls_from_text(full_content)
                        images.extend(url_images)
                    
                    if images:
                        log.info(f"[流式] 成功生成 {len(images)} 张图像")
                        return images
                    else:
                        log.warning("流式 API 返回成功但没有找到图像数据")
                        if collected_content:
                            full_text = ''.join(collected_content)
                            log.warning(f"收集的文本内容 (前500字符): {full_text[:500]}")
                        else:
                            log.warning("没有收集到任何文本内容")
                        if collected_parts:
                            log.warning(f"收集的 parts (前3个): {collected_parts[:3]}")
                        else:
                            log.warning("没有收集到任何 parts")
                        return None

        except asyncio.TimeoutError:
            log.error("OpenAI API 流式请求超时")
            return None
        except Exception as e:
            log.error(f"OpenAI 格式流式生成图像时发生错误: {e}", exc_info=True)
            return None

    async def _download_image_from_url(self, url: str) -> Optional[bytes]:
        """
        从 HTTP/HTTPS URL 下载图片并返回字节数据
        
        Args:
            url: 图片的 HTTP/HTTPS URL
            
        Returns:
            成功时返回图片字节数据，失败时返回 None
        """
        try:
            log.info(f"正在从 URL 下载图片: {url[:100]}...")
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=60),
                    headers={"User-Agent": "Mozilla/5.0"}
                ) as response:
                    if response.status != 200:
                        log.warning(f"下载图片失败，HTTP 状态码: {response.status}, URL: {url[:100]}")
                        return None
                    
                    # 检查 Content-Type 是否为图片
                    content_type = response.headers.get("Content-Type", "")
                    if not content_type.startswith("image/") and "octet-stream" not in content_type:
                        log.warning(f"URL 返回的不是图片格式: {content_type}, URL: {url[:100]}")
                        # 不严格限制，某些 CDN 可能返回非标准 Content-Type
                    
                    # 限制最大下载大小为 50MB
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > 50 * 1024 * 1024:
                        log.warning(f"图片太大 ({content_length} bytes), 跳过下载")
                        return None
                    
                    image_data = await response.read()
                    if len(image_data) > 50 * 1024 * 1024:
                        log.warning(f"下载的图片太大 ({len(image_data)} bytes)")
                        return None
                    
                    log.info(f"成功下载图片, 大小: {len(image_data)} bytes")
                    return image_data
                    
        except asyncio.TimeoutError:
            log.warning(f"下载图片超时: {url[:100]}")
            return None
        except Exception as e:
            log.warning(f"下载图片失败: {e}, URL: {url[:100]}")
            return None

    async def _extract_and_download_urls_from_text(
        self,
        text: str,
        accept_base64: bool = True,
        accept_url: bool = True,
    ) -> List[bytes]:
        """
        从文本内容中提取图片数据（包括 markdown/data URL/HTTP URL）

        支持的格式:
        - Markdown 图片: ![alt](url)
        - Markdown 图片 data URL: ![alt](data:image/...;base64,...)
        - 纯 data:image;base64 URL
        - 纯 HTTP/HTTPS URL（以常见图片扩展名结尾或包含图片相关路径）

        Args:
            text: 可能包含图片数据的文本内容

        Returns:
            提取或下载成功的图片字节数据列表
        """
        images = []
        urls = set()
        data_urls = set()

        # 提取 markdown 图片链接: ![alt](url)
        md_pattern = r'!\[[^\]]*\]\(([^\)]+)\)'
        for match in re.finditer(md_pattern, text):
            candidate = match.group(1).strip()
            if candidate.startswith('data:image'):
                if accept_base64:
                    data_urls.add(candidate)
            elif candidate.startswith('http://') or candidate.startswith('https://'):
                if accept_url:
                    urls.add(candidate)

        # 提取纯 data URL
        if accept_base64:
            data_url_pattern = r'(data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=\s]+)'
            for match in re.finditer(data_url_pattern, text):
                data_urls.add(match.group(1).strip())

        for data_url in data_urls:
            try:
                b64_data = data_url.split(',', 1)[1]
                normalized_b64 = re.sub(r'\s+', '', b64_data)
                images.append(base64.b64decode(normalized_b64))
            except Exception as e:
                log.warning(f'解码文本中的 data URL 失败: {e}')

        # 提取纯 URL（以常见图片扩展名结尾）
        if accept_url:
            url_pattern = r'(https?://[^\s\)\]\"\'<>]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg|tiff)(?:\?[^\s\)\]\"\'<>]*)?)'
            for match in re.finditer(url_pattern, text, re.IGNORECASE):
                urls.add(match.group(1))

        # 提取可能的通用图片 URL（包含 /image 或 /img 路径的 URL）
        if accept_url:
            generic_url_pattern = r'(https?://[^\s\)\]\"\'<>]+(?:/image[s]?/|/img/|/photo/|/pic/)[^\s\)\]\"\'<>]*)'
            for match in re.finditer(generic_url_pattern, text, re.IGNORECASE):
                urls.add(match.group(1))

        if urls:
            log.info(f'从文本中提取到 {len(urls)} 个图片 URL')
            download_tasks = [self._download_image_from_url(url) for url in urls]
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, bytes) and result:
                    images.append(result)
                elif isinstance(result, Exception):
                    log.warning(f'下载图片时异常: {result}')

        return images

    async def _extract_images_from_openai_response(
        self,
        data: dict,
        response_format_override: Optional[str] = None,
    ) -> List[bytes]:
        """
        从 OpenAI 格式的响应中提取图像数据
        根据 IMAGE_RESPONSE_FORMAT 配置决定处理策略:
        - "auto": 优先 base64，同时也处理 URL（默认行为）
        - "base64": 仅接受 base64 内联数据，忽略 URL
        - "url": 优先从 URL 下载图片，忽略 base64 数据
        """
        config = app_config.GEMINI_IMAGEN_CONFIG
        response_format = (
            str(response_format_override).strip().lower()
            if response_format_override is not None and str(response_format_override).strip()
            else config.get("IMAGE_RESPONSE_FORMAT", "auto")
        )

        images = []
        url_images_pending = []
        text_contents = []

        accept_base64 = response_format in ("auto", "base64")
        accept_url = response_format in ("auto", "url")

        log.debug(f"图片响应格式策略: {response_format} (base64={accept_base64}, url={accept_url})")

        if isinstance(data.get("data"), list):
            for item in data["data"]:
                if not isinstance(item, dict):
                    continue

                image_b64 = item.get("b64_json") or item.get("base64")
                if image_b64 and accept_base64:
                    try:
                        normalized_b64 = re.sub(r"\s+", "", str(image_b64))
                        images.append(base64.b64decode(normalized_b64))
                    except Exception as e:
                        log.warning(f"解码 images API base64 失败: {e}")

                image_url = str(item.get("url") or "").strip()
                if image_url:
                    if image_url.startswith("data:image") and accept_base64:
                        try:
                            b64_data = image_url.split(",", 1)[1]
                            normalized_b64 = re.sub(r"\s+", "", b64_data)
                            images.append(base64.b64decode(normalized_b64))
                        except Exception as e:
                            log.warning(f"解码 images API data URL 失败: {e}")
                    elif (
                        image_url.startswith("http://") or image_url.startswith("https://")
                    ) and accept_url:
                        url_images_pending.append(image_url)

                revised_prompt = item.get("revised_prompt")
                if revised_prompt:
                    text_contents.append(str(revised_prompt))

        if "choices" in data:
            for choice in data["choices"]:
                message = choice.get("message", {})
                content = message.get("content")

                # 检查是否有 inline_data（Gemini 格式的图像）
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            part_type = part.get("type", "")

                            # 检查 inline_data 格式
                            inline_data = part.get("inline_data") or part.get("inlineData")
                            if inline_data:
                                image_b64 = inline_data.get("data")
                                if image_b64 and accept_base64:
                                    try:
                                        images.append(base64.b64decode(image_b64))
                                    except Exception as e:
                                        log.warning(f"解码 inline_data 失败: {e}")

                            # 检查 image_url 格式
                            elif "image_url" in part or part_type == "image_url":
                                url_data = part.get("image_url", part)
                                if isinstance(url_data, dict) and "url" in url_data:
                                    url = url_data["url"]
                                    if url.startswith("data:image") and accept_base64:
                                        try:
                                            b64_data = url.split(",", 1)[1]
                                            normalized_b64 = re.sub(r"\s+", "", b64_data)
                                            images.append(base64.b64decode(normalized_b64))
                                        except Exception as e:
                                            log.warning(f"解码 data URL 失败: {e}")
                                    elif (url.startswith("http://") or url.startswith("https://")) and accept_url:
                                        url_images_pending.append(url)

                            # 收集文本部分
                            elif part_type == "text" or "text" in part:
                                text_val = part.get("text", "")
                                if text_val:
                                    text_contents.append(text_val)
                        elif isinstance(part, str):
                            text_contents.append(part)

                # content 为纯字符串时，收集用于后续 URL / data URL 提取
                elif isinstance(content, str) and content:
                    text_contents.append(content)

                # 检查 parts 字段（某些代理的格式）
                parts = message.get("parts", [])
                for part in parts:
                    if isinstance(part, dict):
                        inline_data = part.get("inline_data") or part.get("inlineData")
                        if inline_data:
                            image_b64 = inline_data.get("data")
                            if image_b64 and accept_base64:
                                try:
                                    images.append(base64.b64decode(image_b64))
                                except Exception as e:
                                    log.warning(f"解码 parts inline_data 失败: {e}")
                        elif "image_url" in part:
                            url_data = part["image_url"]
                            if isinstance(url_data, dict) and "url" in url_data:
                                url = url_data["url"]
                                if url.startswith("data:image") and accept_base64:
                                    try:
                                        b64_data = url.split(",", 1)[1]
                                        normalized_b64 = re.sub(r"\s+", "", b64_data)
                                        images.append(base64.b64decode(normalized_b64))
                                    except Exception as e:
                                        log.warning(f"解码 parts data URL 失败: {e}")
                                elif (url.startswith("http://") or url.startswith("https://")) and accept_url:
                                    url_images_pending.append(url)

        # 下载所有收集到的 URL 图片
        if url_images_pending:
            log.info(f"从响应中收集到 {len(url_images_pending)} 个图片 URL，开始下载...")
            download_tasks = [self._download_image_from_url(url) for url in url_images_pending]
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, bytes) and result:
                    images.append(result)
                elif isinstance(result, Exception):
                    log.warning(f"下载 URL 图片异常: {result}")

        # 如果没有从结构化数据中找到图片，尝试从文本内容中提取 data URL / 图片 URL
        if not images and text_contents and (accept_base64 or accept_url):
            full_text = "\n".join(text_contents)
            log.debug(f"尝试从响应文本中提取图片数据, 文本长度: {len(full_text)}")
            text_images = await self._extract_and_download_urls_from_text(
                full_text,
                accept_base64=accept_base64,
                accept_url=accept_url,
            )
            images.extend(text_images)

        return images

    def _get_model_for_resolution(self, resolution: str = "default", is_edit: bool = False, content_rating: str = "sfw") -> str:
        """
        根据分辨率和内容分级选择合适的模型
        
        模型选择优先级：
        1. 内容分级+分辨率+生成类型 对应的专用模型（如 SFW_MODEL_NAME_2K）
        2. 内容分级+生成类型 对应的默认模型（如 SFW_MODEL_NAME）
        3. 分辨率+生成类型 对应的通用模型（如 MODEL_NAME_2K）
        4. 生成类型对应的默认模型（如 EDIT_MODEL_NAME 或 MODEL_NAME）
        
        Args:
            resolution: 分辨率选项 ("default", "2k", "4k")
            is_edit: 是否为图像编辑（图生图）
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)
            
        Returns:
            选定的模型名称
        """
        config = app_config.GEMINI_IMAGEN_CONFIG
        
        # 构建内容分级前缀（大写）
        rating_prefix = content_rating.upper()  # "SFW" 或 "NSFW"
        
        # 根据生成类型选择模型键的基础名称
        if is_edit:
            # 图生图模型
            if resolution == "2k":
                # 优先级: SFW/NSFW_EDIT_MODEL_NAME_2K -> SFW/NSFW_EDIT_MODEL_NAME -> EDIT_MODEL_NAME_2K -> EDIT_MODEL_NAME -> MODEL_NAME
                rated_2k_key = f"{rating_prefix}_EDIT_MODEL_NAME_2K"
                rated_default_key = f"{rating_prefix}_EDIT_MODEL_NAME"
                
                if config.get(rated_2k_key):
                    log.info(f"使用 {content_rating.upper()} 2K 图生图模型: {config[rated_2k_key]}")
                    return config[rated_2k_key]
                elif config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认图生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
                elif config.get("EDIT_MODEL_NAME_2K"):
                    return config["EDIT_MODEL_NAME_2K"]
                    
            elif resolution == "4k":
                # 优先级: SFW/NSFW_EDIT_MODEL_NAME_4K -> SFW/NSFW_EDIT_MODEL_NAME -> EDIT_MODEL_NAME_4K -> EDIT_MODEL_NAME -> MODEL_NAME
                rated_4k_key = f"{rating_prefix}_EDIT_MODEL_NAME_4K"
                rated_default_key = f"{rating_prefix}_EDIT_MODEL_NAME"
                
                if config.get(rated_4k_key):
                    log.info(f"使用 {content_rating.upper()} 4K 图生图模型: {config[rated_4k_key]}")
                    return config[rated_4k_key]
                elif config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认图生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
                elif config.get("EDIT_MODEL_NAME_4K"):
                    return config["EDIT_MODEL_NAME_4K"]
                    
            else:  # default 分辨率
                # 优先级: SFW/NSFW_EDIT_MODEL_NAME -> EDIT_MODEL_NAME -> MODEL_NAME
                rated_default_key = f"{rating_prefix}_EDIT_MODEL_NAME"
                
                if config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认图生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
            
            # 回退到通用图生图模型
            return config.get("EDIT_MODEL_NAME") or config.get("MODEL_NAME", "agy-gemini-3-pro-image")
            
        else:
            # 文生图模型
            if resolution == "2k":
                # 优先级: SFW/NSFW_MODEL_NAME_2K -> SFW/NSFW_MODEL_NAME -> MODEL_NAME_2K -> MODEL_NAME
                rated_2k_key = f"{rating_prefix}_MODEL_NAME_2K"
                rated_default_key = f"{rating_prefix}_MODEL_NAME"
                
                if config.get(rated_2k_key):
                    log.info(f"使用 {content_rating.upper()} 2K 文生图模型: {config[rated_2k_key]}")
                    return config[rated_2k_key]
                elif config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认文生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
                elif config.get("MODEL_NAME_2K"):
                    return config["MODEL_NAME_2K"]
                    
            elif resolution == "4k":
                # 优先级: SFW/NSFW_MODEL_NAME_4K -> SFW/NSFW_MODEL_NAME -> MODEL_NAME_4K -> MODEL_NAME
                rated_4k_key = f"{rating_prefix}_MODEL_NAME_4K"
                rated_default_key = f"{rating_prefix}_MODEL_NAME"
                
                if config.get(rated_4k_key):
                    log.info(f"使用 {content_rating.upper()} 4K 文生图模型: {config[rated_4k_key]}")
                    return config[rated_4k_key]
                elif config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认文生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
                elif config.get("MODEL_NAME_4K"):
                    return config["MODEL_NAME_4K"]
                    
            else:  # default 分辨率
                # 优先级: SFW/NSFW_MODEL_NAME -> MODEL_NAME
                rated_default_key = f"{rating_prefix}_MODEL_NAME"
                
                if config.get(rated_default_key):
                    log.info(f"使用 {content_rating.upper()} 默认文生图模型: {config[rated_default_key]}")
                    return config[rated_default_key]
            
            # 回退到通用文生图模型
            return config.get("MODEL_NAME", "agy-gemini-3-pro-image")

    async def generate_single_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        aspect_ratio: str = "1:1",
        resolution: str = "default",
        content_rating: str = "sfw",
        model_name_override: Optional[str] = None,
        reference_image_bytes: Optional[bytes] = None,
        reference_image_mime: str = "image/png",
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        生成单张图像的便捷方法（内置空回自动重试）

        Args:
            prompt: 正面提示词
            negative_prompt: 负面提示词（可选）
            aspect_ratio: 宽高比
            resolution: 分辨率 ("default", "2k", "4k")
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)
            reference_image_bytes: 参考图片的字节数据（可选，如投喂的食物图）
            reference_image_mime: 参考图片的MIME类型

        Returns:
            成功时返回图像字节数据，失败时返回 None
        """
        retry_max_attempts = max(
            1, int(app_config.GEMINI_IMAGEN_CONFIG.get("EMPTY_RESULT_MAX_RETRIES", 3))
        )

        for attempt in range(1, retry_max_attempts + 1):
            images = await self.generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=1,
                resolution=resolution,
                content_rating=content_rating,
                model_name_override=model_name_override,
                reference_image_bytes=reference_image_bytes,
                reference_image_mime=reference_image_mime,
                openai_image_size=openai_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
                openai_image_api_mode=openai_image_api_mode,
            )

            if images and len(images) > 0:
                if attempt > 1:
                    log.info(
                        f"图片空回重试成功（第 {attempt}/{retry_max_attempts} 次）"
                    )
                return images[-1]

            if attempt < retry_max_attempts:
                log.warning(
                    f"图片生成返回空结果，准备重试（第 {attempt}/{retry_max_attempts} 次）"
                )
                await asyncio.sleep(min(1.0 * attempt, 3.0))

        log.warning(f"图片生成空回，已达到最大重试次数（{retry_max_attempts}）")
        return None

    async def edit_image(
        self,
        reference_image: bytes = None,
        edit_prompt: str = "",
        reference_mime_type: str = "image/png",
        aspect_ratio: str = "1:1",
        resolution: str = "default",
        content_rating: str = "sfw",
        reference_images: Optional[List[dict]] = None,
        model_name_override: Optional[str] = None,
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        使用 Gemini 多模态接口进行图生图（图像编辑）
        
        支持单张或多张参考图。当传入多张参考图时，所有图片都会作为上下文传给模型。
        
        Args:
            reference_image: 单张参考图像的字节数据（向后兼容）
            edit_prompt: 编辑指令，描述希望如何修改图像
            reference_mime_type: 单张参考图像的 MIME 类型（向后兼容）
            aspect_ratio: 输出图像的宽高比
            resolution: 分辨率 ("default", "2k", "4k")
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)
            reference_images: 多张参考图列表，每项为 {"data": bytes, "mime_type": str}
                             如果提供此参数，reference_image 参数会被忽略。
            
        Returns:
            成功时返回生成的图像字节数据，失败时返回 None
        """
        if not self.is_available():
            log.error("Gemini Imagen 服务不可用")
            return None
        
        # 统一处理：将单张图和多张图合并为统一的列表格式
        images_list = []
        if reference_images and isinstance(reference_images, list):
            images_list = reference_images
        elif reference_image:
            images_list = [{"data": reference_image, "mime_type": reference_mime_type}]
        
        if not images_list:
            log.error("图生图需要至少一张参考图")
            return None
        
        log.info(f"图生图参考图数量: {len(images_list)}")
        
        # 根据分辨率和内容分级选择编辑模型
        model_name = (
            str(model_name_override).strip()
            if model_name_override is not None and str(model_name_override).strip()
            else self._get_model_for_resolution(
                resolution=resolution,
                is_edit=True,
                content_rating=content_rating,
            )
        )
        log.info(f"图生图使用模型: {model_name} (分辨率: {resolution}, 内容分级: {content_rating})")

        # 分辨率自动映射 openai_image_size
        resolved_image_size = self._resolve_image_size_for_resolution(
            resolution=resolution,
            openai_image_size=openai_image_size,
        )

        retry_max_attempts = max(
            1, int(app_config.GEMINI_IMAGEN_CONFIG.get("EMPTY_RESULT_MAX_RETRIES", 3))
        )

        for attempt in range(1, retry_max_attempts + 1):
            # 根据 API 格式选择不同的编辑方法
            if self._api_format == "openai":
                edited_image = await self._edit_image_openai_format(
                    reference_images=images_list,
                    edit_prompt=edit_prompt,
                    aspect_ratio=aspect_ratio,
                    model_name=model_name,
                    openai_image_size=resolved_image_size,
                    openai_response_format=openai_response_format,
                    openai_stream=openai_stream,
                    openai_quality=openai_quality,
                    openai_style=openai_style,
                    openai_image_api_mode=openai_image_api_mode,
                )
            else:
                # 使用 Gemini 多模态聊天接口（gemini 或 gemini_chat 格式都使用这个）
                edited_image = await self._edit_image_gemini_chat_format(
                    reference_images=images_list,
                    edit_prompt=edit_prompt,
                    aspect_ratio=aspect_ratio,
                    model_name=model_name,
                )

            if edited_image:
                if attempt > 1:
                    log.info(
                        f"图生图空回重试成功（第 {attempt}/{retry_max_attempts} 次）"
                    )
                return edited_image

            if attempt < retry_max_attempts:
                log.warning(
                    f"图生图返回空结果，准备重试（第 {attempt}/{retry_max_attempts} 次）"
                )
                await asyncio.sleep(min(1.0 * attempt, 3.0))

        log.warning(f"图生图空回，已达到最大重试次数（{retry_max_attempts}）")
        return None
    
    async def _edit_image_gemini_chat_format(
        self,
        reference_images: List[dict],
        edit_prompt: str,
        aspect_ratio: str,
        model_name: str,
    ) -> Optional[bytes]:
        """
        使用 Gemini SDK 的 generate_content 多模态聊天接口进行图像编辑
        支持多张参考图。
        """
        try:
            from google.genai import types
            
            loop = asyncio.get_event_loop()
            
            # 构建编辑提示词
            img_count = len(reference_images)
            if img_count == 1:
                full_prompt = f"请根据以下指令修改这张图片：{edit_prompt}"
            else:
                full_prompt = f"以下是{img_count}张参考图片，请根据指令进行创作：{edit_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n\n输出图片的宽高比应为：{aspect_ratio}"
            
            log.info(f"[Gemini Chat 图生图] 正在使用 {model_name} 编辑图像 ({img_count}张参考图), 指令: {edit_prompt[:100]}...")
            
            # 构建多模态请求内容：先放所有参考图，最后放文字提示
            contents = []
            for img_info in reference_images:
                contents.append(
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=img_info.get("mime_type", "image/png"),
                            data=img_info["data"]
                        )
                    )
                )
            contents.append(types.Part(text=full_prompt))
            
            # 使用 generate_content 多模态接口
            def _sync_generate():
                # 配置生成参数，请求返回图像
                gen_config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],  # 请求返回图像
                )
                
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=contents,
                    config=gen_config,
                )
                return response
            
            response = await loop.run_in_executor(self.executor, _sync_generate)
            
            # 解析响应，提取图像
            images = []
            if response and hasattr(response, 'candidates'):
                for candidate in response.candidates:
                    if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                        for part in candidate.content.parts:
                            # 检查是否是图像数据
                            if hasattr(part, 'inline_data') and part.inline_data:
                                inline_data = part.inline_data
                                if hasattr(inline_data, 'data') and inline_data.data:
                                    # 如果是 base64 编码的字符串
                                    if isinstance(inline_data.data, str):
                                        images.append(base64.b64decode(inline_data.data))
                                    else:
                                        # 如果已经是字节数据
                                        images.append(inline_data.data)
            
            # 也检查 response.parts（某些版本的 SDK）
            if not images and hasattr(response, 'parts'):
                for part in response.parts:
                    if hasattr(part, 'inline_data') and part.inline_data:
                        inline_data = part.inline_data
                        if hasattr(inline_data, 'data') and inline_data.data:
                            if isinstance(inline_data.data, str):
                                images.append(base64.b64decode(inline_data.data))
                            else:
                                images.append(inline_data.data)
            
            if images:
                log.info(f"图生图成功，生成了 {len(images)} 张图像")
                return images[-1]  # 返回最后一张图片
            else:
                log.warning("图生图 API 返回成功但没有找到图像数据")
                if response:
                    log.debug(f"响应类型: {type(response)}")
                    if hasattr(response, 'text'):
                        log.debug(f"响应文本: {response.text[:500] if response.text else 'None'}")
                return None
                
        except Exception as e:
            log.error(f"Gemini Chat 图生图时发生错误: {e}", exc_info=True)
            return None
    
    async def _edit_image_openai_format(
        self,
        reference_images: List[dict],
        edit_prompt: str,
        aspect_ratio: str,
        model_name: str,
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
        openai_image_api_mode: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        根据模型和配置决定走 OpenAI 兼容的 chat/completions 还是 /images/edits。
        多参考图（>1）时强制走 chat/completions，因为 /images/edits 只支持单张 image 字段。
        """
        resolved_mode = self._resolve_openai_image_api_mode(
            model_name=model_name,
            mode_override=openai_image_api_mode,
        )
        if resolved_mode == "images_api":
            edited_image = await self._edit_image_openai_images_api_format(
                reference_images=reference_images,
                edit_prompt=edit_prompt,
                aspect_ratio=aspect_ratio,
                model_name=model_name,
                openai_image_size=openai_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
            )
            if edited_image or self._normalize_openai_image_api_mode(openai_image_api_mode) != "auto":
                return edited_image
            if self._should_keep_images_api_route(
                model_name=model_name,
                mode_override=openai_image_api_mode,
            ):
                log.warning(
                    "检测到 Grok Imagine 编辑模型，图生图将固定使用 /images/edits；"
                    "本次未回收到图像结果，已跳过 chat/completions 回退。"
                )
                return None
            log.warning(
                "OpenAI 图生图 images_api auto 路由未拿到结果，回退到 chat/completions 再试一次。"
            )

        return await self._edit_image_openai_chat_completions_format(
            reference_images=reference_images,
            edit_prompt=edit_prompt,
            aspect_ratio=aspect_ratio,
            model_name=model_name,
            openai_response_format=openai_response_format,
        )

    async def _edit_image_openai_chat_completions_format(
        self,
        reference_images: List[dict],
        edit_prompt: str,
        aspect_ratio: str,
        model_name: str,
        openai_response_format: Optional[str] = None,
    ) -> Optional[bytes]:
        """使用 chat/completions 接口做图生图。"""
        base_url = self._client["base_url"].rstrip("/")
        api_key = self._client["api_key"]
        
        # 构建提示词
        img_count = len(reference_images)
        if img_count == 1:
            full_prompt = f"请根据以下指令修改这张图片：{edit_prompt}"
        else:
            full_prompt = f"以下是{img_count}张参考图片，请根据指令进行创作：{edit_prompt}"
        if aspect_ratio != "1:1":
            full_prompt += f"\n输出图片的宽高比应为：{aspect_ratio}"
        
        log.info(f"[OpenAI格式 图生图] 正在使用 {model_name} 编辑图像 ({img_count}张参考图), 指令: {edit_prompt[:100]}...")
        
        # 构建请求
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        
        # 构建 content 数组：先放所有参考图，最后放文字提示
        content_parts = []
        for img_info in reference_images:
            image_b64 = base64.b64encode(img_info["data"]).decode('utf-8')
            mime_type = img_info.get("mime_type", "image/png")
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{mime_type};base64,{image_b64}"
                }
            })
        content_parts.append({
            "type": "text",
            "text": full_prompt
        })
        
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "user",
                    "content": content_parts
                }
            ],
            "max_tokens": 4096,
        }

        retry_max_attempts, retry_base_delay = self._get_transient_retry_policy()
        timeout = self._build_openai_timeout(streaming=True)

        for attempt in range(1, retry_max_attempts + 1):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=timeout
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            if self._is_retryable_status(response.status) and attempt < retry_max_attempts:
                                delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                                log.warning(
                                    f"OpenAI 图生图 API 返回可重试错误 {response.status}，将在 {delay:.1f}s 后重试 "
                                    f"({attempt}/{retry_max_attempts})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            log.error(f"OpenAI 图生图 API 返回错误 {response.status}: {error_text[:500]}")
                            return None
                        
                        data = await self._parse_json_or_sse_payload(response)
                        if not data:
                            log.warning("OpenAI 图生图 chat/completions 返回空响应")
                            return None
                        
                        # 解析响应，提取图像（复用通用提取方法）
                        images = await self._extract_images_from_openai_response(
                            data,
                            response_format_override=openai_response_format,
                        )
                        
                        if images:
                            log.info(f"图生图成功，生成了 {len(images)} 张图像")
                            return images[-1]

                        log.warning("OpenAI 图生图 API 返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None
            
            except asyncio.TimeoutError:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图生图 API 请求超时，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error("OpenAI 图生图 API 请求超时")
                return None
            except aiohttp.ClientError as e:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图生图 API 网络错误: {e}，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(f"OpenAI 图生图 API 网络错误: {e}")
                return None
            except Exception as e:
                log.error(f"OpenAI 格式图生图时发生错误: {e}", exc_info=True)
                return None

        return None

    async def _edit_image_openai_images_api_format(
        self,
        reference_images: List[dict],
        edit_prompt: str,
        aspect_ratio: str,
        model_name: str,
        openai_image_size: Optional[str] = None,
        openai_response_format: Optional[str] = None,
        openai_stream: Optional[bool] = None,
        openai_quality: Optional[str] = None,
        openai_style: Optional[str] = None,
    ) -> Optional[bytes]:
        """使用 multipart /images/edits 接口做图生图。"""
        base_url = self._client["base_url"].rstrip("/")
        api_key = self._client["api_key"]
        config = app_config.GEMINI_IMAGEN_CONFIG
        stream_override = self._coerce_optional_bool(openai_stream)
        streaming_enabled = (
            config.get("STREAMING_ENABLED", False)
            if stream_override is None
            else stream_override
        )
        request_response_format = self._resolve_request_response_format(
            openai_response_format or config.get("IMAGE_RESPONSE_FORMAT")
        )

        headers = {
            "Authorization": f"Bearer {api_key}",
        }
        if streaming_enabled:
            headers["Accept"] = "text/event-stream"

        retry_max_attempts, retry_base_delay = self._get_transient_retry_policy()
        timeout = self._build_openai_timeout(streaming=streaming_enabled)

        for attempt in range(1, retry_max_attempts + 1):
            try:
                form = aiohttp.FormData()
                form.add_field("model", model_name)
                form.add_field(
                    "prompt",
                    self._build_openai_image_prompt(
                        prompt=edit_prompt,
                        negative_prompt=None,
                        aspect_ratio=aspect_ratio,
                    ),
                )
                if openai_image_size:
                    form.add_field("size", str(openai_image_size).strip())
                if request_response_format:
                    form.add_field("response_format", request_response_format)
                if streaming_enabled:
                    form.add_field("stream", "true")
                if openai_quality:
                    form.add_field("quality", str(openai_quality).strip())
                if openai_style:
                    form.add_field("style", str(openai_style).strip())

                # 多参考图时第 1 张通常是“待编辑底图”，后续才是头像/风格参考。
                # 上游最多支持 9 张参考图：保留原始顺序并取前 9 张有效图片，
                # 不能取最后几张，否则多人头像场景会把底图挤掉，导致结果和原图无关。
                valid_reference_images = [
                    item
                    for item in (reference_images or [])
                    if isinstance(item, dict) and item.get("data")
                ]
                if len(valid_reference_images) > 9:
                    log.warning(
                        "OpenAI 图生图图片接口最多支持 9 张参考图，已按原始顺序截断: %s -> 9",
                        len(valid_reference_images),
                    )

                for index, image_info in enumerate(valid_reference_images[:9], start=1):
                    image_bytes = image_info.get("data")
                    mime_type = str(image_info.get("mime_type") or "image/png").strip() or "image/png"
                    extension = mime_type.split("/")[-1] if "/" in mime_type else "png"
                    form.add_field(
                        "image",
                        image_bytes,
                        filename=f"reference_{index}.{extension}",
                        content_type=mime_type,
                    )

                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{base_url}/images/edits",
                        headers=headers,
                        data=form,
                        timeout=timeout,
                    ) as response:
                        if response.status != 200:
                            error_text = await response.text()
                            if self._is_retryable_status(response.status) and attempt < retry_max_attempts:
                                delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                                log.warning(
                                    f"OpenAI 图生图图片接口返回可重试错误 {response.status}，将在 {delay:.1f}s 后重试 "
                                    f"({attempt}/{retry_max_attempts})"
                                )
                                await asyncio.sleep(delay)
                                continue
                            log.error(f"OpenAI 图生图图片接口返回错误 {response.status}: {error_text[:500]}")
                            return None

                        data = await self._parse_json_or_sse_payload(response)
                        if not data:
                            log.warning("OpenAI 图生图图片接口返回空响应")
                            return None

                        images = await self._extract_images_from_openai_response(
                            data,
                            response_format_override=openai_response_format,
                        )
                        if images:
                            log.info(f"通过 /images/edits 成功生成 {len(images)} 张图像")
                            return images[-1]

                        log.warning("OpenAI 图生图图片接口返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None

            except asyncio.TimeoutError:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图生图图片接口请求超时，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error("OpenAI 图生图图片接口请求超时")
                return None
            except aiohttp.ClientError as error:
                if attempt < retry_max_attempts:
                    delay = min(retry_base_delay * (2 ** (attempt - 1)), 8.0)
                    log.warning(
                        f"OpenAI 图生图图片接口网络错误: {error}，{delay:.1f}s 后重试 ({attempt}/{retry_max_attempts})"
                    )
                    await asyncio.sleep(delay)
                    continue
                log.error(f"OpenAI 图生图图片接口网络错误: {error}")
                return None
            except Exception as error:
                log.error(f"调用 /images/edits 时发生错误: {error}", exc_info=True)
                return None

        return None


# 全局单例实例
gemini_imagen_service = GeminiImagenService()
