# -*- coding: utf-8 -*-

"""
Gemini Imagen 图像生成服务
使用 Google Gemini API 的 Imagen 模型生成图像
"""

import logging
import asyncio
from typing import Optional, List
from concurrent.futures import ThreadPoolExecutor
import base64

from google import genai
from google.genai import types

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)


class GeminiImagenService:
    """
    封装 Gemini Imagen 图像生成功能的服务类
    """

    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=5)
        self._client = None
        self._initialize_client()

    def _initialize_client(self):
        """初始化 Gemini 客户端"""
        config = app_config.GEMINI_IMAGEN_CONFIG
        
        if not config.get("ENABLED", False):
            log.warning("Gemini Imagen 服务已禁用")
            return
            
        api_key = config.get("API_KEY")
        if not api_key:
            log.error("未配置 Gemini Imagen API 密钥")
            return
            
        try:
            base_url = config.get("BASE_URL")
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
            prompt: 正面提示词
            negative_prompt: 负面提示词（可选）
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

        try:
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

            log.info(f"正在使用 {model_name} 生成图像, 提示词: {prompt[:100]}...")

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
            return images[0]
        return None


# 全局单例实例
gemini_imagen_service = GeminiImagenService()