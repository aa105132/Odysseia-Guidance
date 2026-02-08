# -*- coding: utf-8 -*-

"""
视频生成服务
使用 OpenAI 兼容的 chat/completions API 生成视频

支持两种视频格式:
1. URL: 直接从 API 响应中提取视频 URL
2. HTML: 从 API 响应中提取 HTML 页面中的视频链接
"""

import logging
import asyncio
import aiohttp
import json
import re
from typing import Optional, Dict, Any
from dataclasses import dataclass

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)


@dataclass
class VideoResult:
    """视频生成结果"""
    url: Optional[str] = None  # 视频直接 URL
    html_content: Optional[str] = None  # HTML 页面内容（包含视频）
    text_response: Optional[str] = None  # AI 的文本回复
    format_type: str = "url"  # 结果格式类型: "url" 或 "html"


class VideoGenerationService:
    """
    视频生成服务类
    
    通过 OpenAI 兼容的 chat/completions API 生成视频
    """

    def __init__(self):
        self._client = None
        self._initialize_client()

    def _initialize_client(self):
        """初始化客户端"""
        config = app_config.VIDEO_GEN_CONFIG

        if not config.get("ENABLED"):
            log.info("视频生成服务未启用")
            return

        api_key = config.get("API_KEY")
        base_url = config.get("BASE_URL")

        # 如果没有专用 API Key，尝试使用 Imagen 的
        if not api_key:
            api_key = app_config.GEMINI_IMAGEN_CONFIG.get("API_KEY")
        if not base_url:
            base_url = app_config.GEMINI_IMAGEN_CONFIG.get("BASE_URL")

        if not api_key or not base_url:
            log.warning("视频生成服务缺少 API Key 或 Base URL")
            return

        self._client = {
            "api_key": api_key,
            "base_url": base_url,
        }
        log.info(f"视频生成服务已初始化, Base URL: {base_url[:30]}...")

    def is_available(self) -> bool:
        """检查服务是否可用"""
        return (
            self._client is not None
            and app_config.VIDEO_GEN_CONFIG.get("ENABLED", False)
        )

    def reinitialize(self):
        """重新初始化客户端"""
        self._client = None
        self._initialize_client()

    async def generate_video(
        self,
        prompt: str,
        duration: int = 5,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
    ) -> Optional[VideoResult]:
        """
        生成视频（支持文生视频和图生视频）

        Args:
            prompt: 视频描述提示词
            duration: 视频时长（秒）
            image_data: 可选的参考图片字节数据（图生视频模式）
            image_mime_type: 图片 MIME 类型（如 "image/png"）

        Returns:
            成功时返回 VideoResult，失败时回 None
        """
        if not self.is_available():
            log.error("视频生成服务不可用")
            return None

        config = app_config.VIDEO_GEN_CONFIG
        model_name = config.get("MODEL_NAME", "veo-2.0-generate-001")
        video_format = config.get("VIDEO_FORMAT", "url")
        max_duration = config.get("MAX_DURATION", 8)

        # 限制时长
        duration = min(max(1, duration), max_duration)

        is_image_to_video = image_data is not None
        mode_str = "图生视频" if is_image_to_video else "文生视频"
        log.info(f"使用模型 {model_name} 生成视频 ({mode_str}), 时长: {duration}s, 格式: {video_format}")

        try:
            base_url = self._client["base_url"].rstrip("/")
            api_key = self._client["api_key"]

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            # 构建消息内容
            if is_image_to_video:
                # 图生视频：构建多模态消息（图片 + 文本）
                import base64 as b64_module
                image_b64 = b64_module.b64encode(image_data).decode("utf-8")
                mime = image_mime_type or "image/png"
                
                content_parts = [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{image_b64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"请根据这张图片生成一个视频：{prompt}\n视频时长：约{duration}秒"
                    }
                ]
                user_content = content_parts
            else:
                # 文生视频：纯文本消息
                user_content = f"请生成一个视频：{prompt}\n视频时长：约{duration}秒"

            payload = {
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": user_content
                    }
                ],
                "max_tokens": 4096,
            }

            log.info(f"[视频生成-{mode_str}] 正在使用 {model_name} 生成视频, 提示词: {prompt[:100]}...")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300)  # 视频生成可能需要更长时间
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        log.error(f"视频生成 API 返回错误 {response.status}: {error_text[:500]}")
                        return None

                    data = await response.json()
                    return self._extract_video_from_response(data, video_format)

        except asyncio.TimeoutError:
            log.error("视频生成 API 请求超时")
            return None
        except Exception as e:
            log.error(f"视频生成时发生错误: {e}", exc_info=True)
            return None

    def _extract_video_from_response(self, data: dict, video_format: str) -> Optional[VideoResult]:
        """
        从 API 响应中提取视频数据

        Args:
            data: API 响应 JSON
            video_format: 期望的视频格式 ("url" 或 "html")

        Returns:
            VideoResult 或 None
        """
        text_content = ""
        video_urls = []

        if "choices" in data:
            for choice in data["choices"]:
                message = choice.get("message", {})
                content = message.get("content")

                if isinstance(content, str):
                    text_content = content
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict):
                            if part.get("type") == "text" or "text" in part:
                                text_content += part.get("text", "")
                            # 检查是否有视频 URL 类型的 part
                            elif part.get("type") == "video_url":
                                url_data = part.get("video_url", {})
                                if isinstance(url_data, dict) and "url" in url_data:
                                    video_urls.append(url_data["url"])

        if not text_content and not video_urls:
            log.warning("视频生成 API 返回空内容")
            log.debug(f"完整响应: {json.dumps(data, ensure_ascii=False)[:1000]}")
            return None

        # 根据格式提取视频
        if video_format == "html":
            return self._extract_video_html(text_content, video_urls)
        else:
            return self._extract_video_url(text_content, video_urls)

    def _extract_video_url(self, text_content: str, video_urls: list) -> Optional[VideoResult]:
        """从响应中提取视频 URL"""
        # 优先使用结构化的视频 URL
        if video_urls:
            return VideoResult(
                url=video_urls[0],
                text_response=text_content if text_content else None,
                format_type="url"
            )

        # 从文本中提取视频 URL
        # 常见视频扩展名
        video_url_pattern = r'(https?://[^\s\)\]\"\'<>]+\.(?:mp4|webm|mov|avi|mkv|m3u8)(?:\?[^\s\)\]\"\'<>]*)?)'
        matches = re.findall(video_url_pattern, text_content, re.IGNORECASE)
        if matches:
            return VideoResult(
                url=matches[0],
                text_response=text_content,
                format_type="url"
            )

        # 提取 Markdown 链接中的视频
        md_video_pattern = r'\[(?:[^\]]*(?:video|视频|播放)[^\]]*)\]\((https?://[^\)]+)\)'
        md_matches = re.findall(md_video_pattern, text_content, re.IGNORECASE)
        if md_matches:
            return VideoResult(
                url=md_matches[0],
                text_response=text_content,
                format_type="url"
            )

        # 提取任何 URL（可能是视频托管服务的链接）
        generic_url_pattern = r'(https?://[^\s\)\]\"\'<>]+(?:/video[s]?/|/media/|/stream/)[^\s\)\]\"\'<>]*)'
        generic_matches = re.findall(generic_url_pattern, text_content, re.IGNORECASE)
        if generic_matches:
            return VideoResult(
                url=generic_matches[0],
                text_response=text_content,
                format_type="url"
            )

        # 如果没有找到视频 URL，但有文本内容，尝试提取任何 URL
        any_url_pattern = r'(https?://[^\s\)\]\"\'<>]+)'
        any_matches = re.findall(any_url_pattern, text_content)
        if any_matches:
            # 过滤掉明显不是视频的 URL
            for url in any_matches:
                if not any(ext in url.lower() for ext in ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.css', '.js']):
                    return VideoResult(
                        url=url,
                        text_response=text_content,
                        format_type="url"
                    )

        log.warning("未能从响应中提取到视频 URL")
        log.debug(f"响应文本: {text_content[:500]}")

        # 返回纯文本响应
        if text_content:
            return VideoResult(
                text_response=text_content,
                format_type="url"
            )
        return None

    def _extract_video_html(self, text_content: str, video_urls: list) -> Optional[VideoResult]:
        """从响应中提取 HTML 视频内容"""
        # 检查文本中是否包含 HTML 标签
        if '<video' in text_content or '<iframe' in text_content or '<!DOCTYPE' in text_content.upper():
            return VideoResult(
                html_content=text_content,
                format_type="html"
            )

        # 检查是否有 HTML 代码块
        html_block_pattern = r'```html\s*([\s\S]*?)\s*```'
        html_matches = re.findall(html_block_pattern, text_content, re.IGNORECASE)
        if html_matches:
            return VideoResult(
                html_content=html_matches[0],
                text_response=text_content,
                format_type="html"
            )

        # 如果有视频 URL，构建简单的 HTML 播放器
        url = None
        if video_urls:
            url = video_urls[0]
        else:
            # 尝试从文本提取 URL
            video_url_pattern = r'(https?://[^\s\)\]\"\'<>]+\.(?:mp4|webm|mov)(?:\?[^\s\)\]\"\'<>]*)?)'
            matches = re.findall(video_url_pattern, text_content, re.IGNORECASE)
            if matches:
                url = matches[0]

        if url:
            html = f'''<!DOCTYPE html>
<html><head><style>
body {{ margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #000; }}
video {{ max-width: 100%; max-height: 100vh; }}
</style></head><body>
<video controls autoplay loop>
<source src="{url}" type="video/mp4">
Your browser does not support the video tag.
</video>
</body></html>'''
            return VideoResult(
                url=url,
                html_content=html,
                text_response=text_content,
                format_type="html"
            )

        # 回退到 URL 模式
        return self._extract_video_url(text_content, video_urls)


# 全局单例实例
video_service = VideoGenerationService()