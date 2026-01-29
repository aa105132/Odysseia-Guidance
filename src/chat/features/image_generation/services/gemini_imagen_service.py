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
        self.executor = ThreadPoolExecutor(max_workers=5)
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
    ) -> Optional[List[bytes]]:
        """
        使用 Gemini Imagen 生成图像

        Args:
            prompt: 正面提示词（支持中文自然语言）
            negative_prompt: 负面提示词（可选，支持中文）
            aspect_ratio: 宽高比，支持 "1:1", "3:4", "4:3", "9:16", "16:9"
            number_of_images: 生成图片数量（1-4）

        Returns:
            成功时返回图像字节数据列表，失败时返回 None
        """
        if not self.is_available():
            log.error("Gemini Imagen 服务不可用")
            return None

        config = app_config.GEMINI_IMAGEN_CONFIG
        model_name = config.get("MODEL_NAME", "imagen-3.0-generate-002")

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

            log.info(f"[Gemini Chat格式] 正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

            # 使用 generate_content 多模态接口
            def _sync_generate():
                # 配置生成参数，请求返回图像
                config = types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],  # 请求返回图像
                )
                
                response = self._client.models.generate_content(
                    model=model_name,
                    contents=full_prompt,
                    config=config,
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
                    images = []
                    if "choices" in data:
                        for choice in data["choices"]:
                            message = choice.get("message", {})
                            content = message.get("content")
                            
                            # 检查是否有 inline_data（Gemini 格式的图像）
                            if isinstance(content, list):
                                for part in content:
                                    if isinstance(part, dict):
                                        # 检查 inline_data 格式
                                        inline_data = part.get("inline_data") or part.get("inlineData")
                                        if inline_data:
                                            image_b64 = inline_data.get("data")
                                            if image_b64:
                                                images.append(base64.b64decode(image_b64))
                                        # 检查 image_url 格式
                                        elif "image_url" in part:
                                            url_data = part["image_url"]
                                            if isinstance(url_data, dict) and "url" in url_data:
                                                url = url_data["url"]
                                                # 如果是 data URL
                                                if url.startswith("data:image"):
                                                    b64_data = url.split(",", 1)[1]
                                                    images.append(base64.b64decode(b64_data))
                            
                            # 检查 parts 字段（某些代理的格式）
                            parts = message.get("parts", [])
                            for part in parts:
                                if isinstance(part, dict):
                                    inline_data = part.get("inline_data") or part.get("inlineData")
                                    if inline_data:
                                        image_b64 = inline_data.get("data")
                                        if image_b64:
                                            images.append(base64.b64decode(image_b64))
                    
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

    async def generate_single_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        aspect_ratio: str = "1:1",
    ) -> Optional[bytes]:
        """
        生成单张图像的便捷方法

        Args:
            prompt: 正面提示词
            negative_prompt: 负面提示词（可选）
            aspect_ratio: 宽高比

        Returns:
            成功时返回图像字节数据，失败时返回 None
        """
        images = await self.generate_image(
            prompt=prompt,
            negative_prompt=negative_prompt,
            aspect_ratio=aspect_ratio,
            number_of_images=1,
        )
        
        if images and len(images) > 0:
            # 返回最后一张图片（通常是完整图，第一张可能是缩略图）
            return images[-1]
        return None


# 全局单例实例
gemini_imagen_service = GeminiImagenService()