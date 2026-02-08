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
from typing import Optional, List
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
            app_config.GEMINI_IMAGEN_CONFIG["ENABLED"] = os.getenv("GEMINI_IMAGEN_ENABLED", "False").lower() == "true"
            app_config.GEMINI_IMAGEN_CONFIG["API_KEY"] = os.getenv("GEMINI_IMAGEN_API_KEY")
            app_config.GEMINI_IMAGEN_CONFIG["BASE_URL"] = os.getenv("GEMINI_IMAGEN_BASE_URL")
            app_config.GEMINI_IMAGEN_CONFIG["MODEL_NAME"] = os.getenv("GEMINI_IMAGEN_MODEL", "imagen-3.0-generate-002")
            
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
    
    def update_config(self, enabled: bool = None, api_key: str = None, base_url: str = None, model_name: str = None) -> dict:
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
        model_name = self._get_model_for_resolution(resolution=resolution, is_edit=False, content_rating=content_rating)
        log.info(f"使用模型 {model_name} 生成图像 (分辨率: {resolution}, 内容分级: {content_rating})")

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
            )
        elif self._api_format == "gemini_chat":
            # 使用 Gemini 多模态聊天接口生成图像
            return await self._generate_image_gemini_chat_format(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
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

            # 使用 generate_content 多模态接口
            def _sync_generate():
                # 配置生成参数，请求返回图像
                # 设置宽松的安全过滤级别
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
                    response_modalities=["IMAGE", "TEXT"],  # 请求返回图像
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
                log.info(f"成功生成 {len(images)} 张图像")
                return images
            else:
                # 打印响应结构以便调试
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
    ) -> Optional[List[bytes]]:
        """
        使用 Gemini SDK 的流式 generate_content 接口生成图像
        通过流式传输可以更快地获取响应
        
        修复: 完整收集所有 chunk 后再解析图像，避免分片数据问题
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
                        contents=full_prompt,
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
    ) -> Optional[List[bytes]]:
        """
        使用 OpenAI 兼容的 chat/completions API 生成图像
        适用于支持图像生成的聊天模型（如 Gemini 2.0 通过代理）
        支持流式请求 (SSE) 以更快获取响应
        """
        config = app_config.GEMINI_IMAGEN_CONFIG
        streaming_enabled = config.get("STREAMING_ENABLED", False)
        
        if streaming_enabled:
            return await self._generate_image_openai_format_streaming(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                number_of_images=number_of_images,
                model_name=model_name,
            )
        
        # 非流式请求的原有逻辑
        try:
            base_url = self._client["base_url"].rstrip("/")
            api_key = self._client["api_key"]
            
            # 构建提示词
            full_prompt = f"请生成一张图片：{prompt}"
            if negative_prompt:
                full_prompt += f"\n请避免：{negative_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n宽高比：{aspect_ratio}"
            
            log.info(f"[OpenAI格式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")
            
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
                        "content": full_prompt
                    }
                ],
                "max_tokens": 4096,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(f"OpenAI API 返回错误 {response.status}: {error_text[:500]}")
                        return None
                    
                    data = await response.json()
                    
                    # 解析响应，提取图像
                    images = await self._extract_images_from_openai_response(data)
                    
                    if images:
                        log.info(f"成功生成 {len(images)} 张图像")
                        return images
                    else:
                        log.warning("API 返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None

        except asyncio.TimeoutError:
            log.error("OpenAI API 请求超时")
            return None
        except Exception as e:
            log.error(f"OpenAI 格式生成图像时发生错误: {e}", exc_info=True)
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
                    timeout=aiohttp.ClientTimeout(total=180)  # 流式请求可能需要更长时间
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

    async def _extract_and_download_urls_from_text(self, text: str) -> List[bytes]:
        """
        从文本内容中提取图片 URL（包括 markdown 格式和纯 URL）并下载
        
        支持的格式:
        - Markdown 图片: ![alt](url)
        - 纯 HTTP/HTTPS URL（以常见图片扩展名结尾或包含图片相关路径）
        
        Args:
            text: 可能包含图片 URL 的文本内容
            
        Returns:
            下载成功的图片字节数据列表
        """
        images = []
        urls = set()  # 用 set 去重
        
        # 提取 markdown 图片链接: ![alt](url)
        md_pattern = r'!\[[^\]]*\]\((https?://[^\)]+)\)'
        for match in re.finditer(md_pattern, text):
            urls.add(match.group(1))
        
        # 提取纯 URL（以常见图片扩展名结尾）
        url_pattern = r'(https?://[^\s\)\]\"\'<>]+\.(?:png|jpg|jpeg|gif|webp|bmp|svg|tiff)(?:\?[^\s\)\]\"\'<>]*)?)'
        for match in re.finditer(url_pattern, text, re.IGNORECASE):
            urls.add(match.group(1))
        
        # 提取可能的通用图片 URL（包含 /image 或 /img 路径的 URL）
        generic_url_pattern = r'(https?://[^\s\)\]\"\'<>]+(?:/image[s]?/|/img/|/photo/|/pic/)[^\s\)\]\"\'<>]*)'
        for match in re.finditer(generic_url_pattern, text, re.IGNORECASE):
            urls.add(match.group(1))
        
        if urls:
            log.info(f"从文本中提取到 {len(urls)} 个图片 URL")
            # 并发下载所有 URL
            download_tasks = [self._download_image_from_url(url) for url in urls]
            results = await asyncio.gather(*download_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, bytes) and result:
                    images.append(result)
                elif isinstance(result, Exception):
                    log.warning(f"下载图片时异常: {result}")
        
        return images

    async def _extract_images_from_openai_response(self, data: dict) -> List[bytes]:
        """
        从 OpenAI 格式的响应中提取图像数据
        根据 IMAGE_RESPONSE_FORMAT 配置决定处理策略:
        - "auto": 优先 base64，同时也处理 URL（默认行为）
        - "base64": 仅接受 base64 内联数据，忽略 URL
        - "url": 优先从 URL 下载图片，忽略 base64 数据
        """
        config = app_config.GEMINI_IMAGEN_CONFIG
        response_format = config.get("IMAGE_RESPONSE_FORMAT", "auto")
        
        images = []
        url_images_pending = []  # 待下载的 URL 列表
        text_contents = []  # 收集文本内容，用于后续提取 URL
        
        accept_base64 = response_format in ("auto", "base64")
        accept_url = response_format in ("auto", "url")
        
        log.debug(f"图片响应格式策略: {response_format} (base64={accept_base64}, url={accept_url})")
        
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
                                    # 如果是 data URL
                                    if url.startswith("data:image") and accept_base64:
                                        try:
                                            b64_data = url.split(",", 1)[1]
                                            images.append(base64.b64decode(b64_data))
                                        except Exception as e:
                                            log.warning(f"解码 data URL 失败: {e}")
                                    # 如果是 HTTP URL，收集待下载
                                    elif (url.startswith("http://") or url.startswith("https://")) and accept_url:
                                        url_images_pending.append(url)
                            # 收集文本部分
                            elif part_type == "text" or "text" in part:
                                text_val = part.get("text", "")
                                if text_val:
                                    text_contents.append(text_val)
                        elif isinstance(part, str):
                            text_contents.append(part)
                
                # content 为纯字符串时，收集用于后续 URL 提取
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
                        # 检查 parts 中的 image_url
                        elif "image_url" in part:
                            url_data = part["image_url"]
                            if isinstance(url_data, dict) and "url" in url_data:
                                url = url_data["url"]
                                if url.startswith("data:image") and accept_base64:
                                    try:
                                        b64_data = url.split(",", 1)[1]
                                        images.append(base64.b64decode(b64_data))
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
        
        # 如果没有从结构化数据中找到图片，尝试从文本内容中提取 URL 并下载
        if not images and text_contents and accept_url:
            full_text = '\n'.join(text_contents)
            log.debug(f"尝试从响应文本中提取图片 URL, 文本长度: {len(full_text)}")
            url_images = await self._extract_and_download_urls_from_text(full_text)
            images.extend(url_images)
        
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
    ) -> Optional[bytes]:
        """
        生成单张图像的便捷方法

        Args:
            prompt: 正面提示词
            negative_prompt: 负面提示词（可选）
            aspect_ratio: 宽高比
            resolution: 分辨率 ("default", "2k", "4k")
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)

        Returns:
            成功时返回图像字节数据，失败时返回 None
        """
        images = await self.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
            number_of_images=1,
            resolution=resolution,
            content_rating=content_rating,
        )
        
        if images and len(images) > 0:
            # 返回最后一张图片（通常是完整图，第一张可能是缩略图）
            return images[-1]
        return None

    async def edit_image(
        self,
        reference_image: bytes,
        edit_prompt: str,
        reference_mime_type: str = "image/png",
        aspect_ratio: str = "1:1",
        resolution: str = "default",
        content_rating: str = "sfw",
    ) -> Optional[bytes]:
        """
        使用 Gemini 多模态接口进行图生图（图像编辑）
        
        Args:
            reference_image: 参考图像的字节数据
            edit_prompt: 编辑指令，描述希望如何修改图像
            reference_mime_type: 参考图像的 MIME 类型
            aspect_ratio: 输出图像的宽高比
            resolution: 分辨率 ("default", "2k", "4k")
            content_rating: 内容分级 ("sfw" 安全内容, "nsfw" 成人内容)
            
        Returns:
            成功时返回生成的图像字节数据，失败时返回 None
        """
        if not self.is_available():
            log.error("Gemini Imagen 服务不可用")
            return None
        
        # 根据分辨率和内容分级选择编辑模型
        model_name = self._get_model_for_resolution(resolution=resolution, is_edit=True, content_rating=content_rating)
        log.info(f"图生图使用模型: {model_name} (分辨率: {resolution}, 内容分级: {content_rating})")
        
        # 根据 API 格式选择不同的编辑方法
        if self._api_format == "openai":
            return await self._edit_image_openai_format(
                reference_image=reference_image,
                edit_prompt=edit_prompt,
                reference_mime_type=reference_mime_type,
                aspect_ratio=aspect_ratio,
                model_name=model_name,
            )
        else:
            # 使用 Gemini 多模态聊天接口（gemini 或 gemini_chat 格式都使用这个）
            return await self._edit_image_gemini_chat_format(
                reference_image=reference_image,
                edit_prompt=edit_prompt,
                reference_mime_type=reference_mime_type,
                aspect_ratio=aspect_ratio,
                model_name=model_name,
            )
    
    async def _edit_image_gemini_chat_format(
        self,
        reference_image: bytes,
        edit_prompt: str,
        reference_mime_type: str,
        aspect_ratio: str,
        model_name: str,
    ) -> Optional[bytes]:
        """
        使用 Gemini SDK 的 generate_content 多模态聊天接口进行图像编辑
        """
        try:
            from google.genai import types
            
            loop = asyncio.get_event_loop()
            
            # 构建编辑提示词
            full_prompt = f"请根据以下指令修改这张图片：{edit_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n\n输出图片的宽高比应为：{aspect_ratio}"
            
            log.info(f"[Gemini Chat 图生图] 正在使用 {model_name} 编辑图像, 指令: {edit_prompt[:100]}...")
            
            # 构建多模态请求内容
            contents = [
                types.Part(
                    inline_data=types.Blob(
                        mime_type=reference_mime_type,
                        data=reference_image
                    )
                ),
                types.Part(text=full_prompt),
            ]
            
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
        reference_image: bytes,
        edit_prompt: str,
        reference_mime_type: str,
        aspect_ratio: str,
        model_name: str,
    ) -> Optional[bytes]:
        """
        使用 OpenAI 兼容的 chat/completions API 进行图像编辑
        """
        try:
            base_url = self._client["base_url"].rstrip("/")
            api_key = self._client["api_key"]
            
            # 将参考图像转换为 base64
            image_b64 = base64.b64encode(reference_image).decode('utf-8')
            
            # 构建提示词
            full_prompt = f"请根据以下指令修改这张图片：{edit_prompt}"
            if aspect_ratio != "1:1":
                full_prompt += f"\n输出图片的宽高比应为：{aspect_ratio}"
            
            log.info(f"[OpenAI格式 图生图] 正在使用 {model_name} 编辑图像, 指令: {edit_prompt[:100]}...")
            
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
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{reference_mime_type};base64,{image_b64}"
                                }
                            },
                            {
                                "type": "text",
                                "text": full_prompt
                            }
                        ]
                    }
                ],
                "max_tokens": 4096,
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=180)
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(f"OpenAI 图生图 API 返回错误 {response.status}: {error_text[:500]}")
                        return None
                    
                    data = await response.json()
                    
                    # 解析响应，提取图像（复用通用提取方法）
                    images = await self._extract_images_from_openai_response(data)
                    
                    if images:
                        log.info(f"图生图成功，生成了 {len(images)} 张图像")
                        return images[-1]
                    else:
                        log.warning("OpenAI 图生图 API 返回成功但没有找到图像数据")
                        log.debug(f"响应内容: {json.dumps(data, ensure_ascii=False)[:1000]}")
                        return None
        
        except asyncio.TimeoutError:
            log.error("OpenAI 图生图 API 请求超时")
            return None
        except Exception as e:
            log.error(f"OpenAI 格式图生图时发生错误: {e}", exc_info=True)
            return None


# 全局单例实例
gemini_imagen_service = GeminiImagenService()