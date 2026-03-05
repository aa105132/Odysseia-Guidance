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
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)


@dataclass
class VideoResult:
    """视频生成结果"""
    url: Optional[str] = None  # 视频直接 URL
    html_content: Optional[str] = None  # HTML 页面内容（包含视频）
    text_response: Optional[str] = None  # AI 的文本回复
    post_id: Optional[str] = None  # 上游返回的视频 post_id（可用于视频延长）
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
        reference_images: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
    ) -> Optional[VideoResult]:
        """
        生成视频（支持文生视频和图生视频）

        Args:
            prompt: 视频描述提示词
            duration: 视频时长（秒）
            image_data: 可选的单张参考图片字节数据（向后兼容）
            image_mime_type: 单张图片 MIME 类型（如 "image/png"，向后兼容）
            reference_images: 可选的多张参考图片列表，每项形如：
                {"data": bytes, "mime_type": "image/png"}
                若提供该参数，将优先使用它；否则回退使用 image_data。

        Returns:
            成功时返回 VideoResult，失败时回 None
        """
        if not self.is_available():
            log.error("视频生成服务不可用")
            return None

        config = app_config.VIDEO_GEN_CONFIG
        video_format = config.get("VIDEO_FORMAT", "url")
        max_duration = config.get("MAX_DURATION", 8)

        # 限制时长
        duration = min(max(1, duration), max_duration)

        # 统一处理单图/多图参考输入
        normalized_reference_images: List[Dict[str, Any]] = []
        if reference_images and isinstance(reference_images, list):
            for img in reference_images:
                if isinstance(img, dict) and img.get("data"):
                    normalized_reference_images.append(
                        {
                            "data": img["data"],
                            "mime_type": img.get("mime_type", "image/png"),
                        }
                    )
        if not normalized_reference_images and image_data is not None:
            normalized_reference_images.append(
                {
                    "data": image_data,
                    "mime_type": image_mime_type or "image/png",
                }
            )

        is_image_to_video = len(normalized_reference_images) > 0
        ref_count = len(normalized_reference_images)
        mode_str = "图生视频" if is_image_to_video else "文生视频"

        # 根据模式选择模型：图生视频优先使用 I2V 专用模型，未配置时回退到通用模型
        default_model = config.get("MODEL_NAME", "veo-2.0-generate-001")
        if is_image_to_video:
            i2v_model = config.get("I2V_MODEL_NAME", "")
            model_name = i2v_model if i2v_model else default_model
        else:
            model_name = default_model
        if model_override and str(model_override).strip():
            model_name = str(model_override).strip()

        log.info(
            f"使用模型 {model_name} 生成视频 ({mode_str}), "
            f"时长: {duration}s, 格式: {video_format}, 参考图数量: {ref_count}"
        )

        try:
            base_url = self._client["base_url"].rstrip("/")
            api_key = self._client["api_key"]

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }

            # 构建消息内容
            if is_image_to_video:
                # 图生视频：构建多模态消息（多图 + 文本）
                import base64 as b64_module

                content_parts = []
                for ref_img in normalized_reference_images:
                    image_b64 = b64_module.b64encode(ref_img["data"]).decode("utf-8")
                    mime = ref_img.get("mime_type", "image/png")
                    content_parts.append(
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{mime};base64,{image_b64}"
                            },
                        }
                    )

                if ref_count == 1:
                    text_prompt = f"请根据这张参考图片生成一个视频：{prompt}\n视频时长：约{duration}秒"
                else:
                    text_prompt = (
                        f"请根据这{ref_count}张参考图片生成一个视频：{prompt}\n视频时长：约{duration}秒\n"
                        f"要求：综合所有参考图的主体特征与风格，输出统一且连贯的动态画面。"
                    )

                content_parts.append(
                    {
                        "type": "text",
                        "text": text_prompt,
                    }
                )
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

            retry_502_max_attempts = max(
                1, int(config.get("RETRY_502_MAX_ATTEMPTS", 3))
            )
            retry_502_delay_seconds = max(
                0.0, float(config.get("RETRY_502_DELAY_SECONDS", 2))
            )
            empty_result_max_attempts = max(
                1, int(config.get("EMPTY_RESULT_MAX_RETRIES", 3))
            )

            log.info(
                f"[视频生成-{mode_str}] 正在使用 {model_name} 生成视频, 提示词: {prompt[:100]}..."
            )

            async with aiohttp.ClientSession() as session:
                for empty_attempt in range(empty_result_max_attempts):
                    should_retry_empty_result = False

                    for attempt in range(retry_502_max_attempts):
                        async with session.post(
                            f"{base_url}/chat/completions",
                            headers=headers,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=300),  # 视频生成可能需要更长时间
                        ) as response:
                            status_code = response.status

                            if status_code == 200:
                                data = await response.json()
                                video_result = self._extract_video_from_response(
                                    data, video_format
                                )
                                if video_result is not None:
                                    if empty_attempt > 0:
                                        log.info(
                                            f"[视频生成-{mode_str}] 空回重试成功（第 {empty_attempt + 1}/{empty_result_max_attempts} 次）"
                                        )
                                    return video_result

                                if empty_attempt < empty_result_max_attempts - 1:
                                    current_empty_attempt = empty_attempt + 1
                                    log.warning(
                                        f"[视频生成-{mode_str}] 第 {current_empty_attempt}/{empty_result_max_attempts} 次返回空结果，"
                                        "准备自动重试..."
                                    )
                                    should_retry_empty_result = True
                                    break

                                log.error(
                                    f"[视频生成-{mode_str}] 连续空回，已达到最大重试次数（{empty_result_max_attempts}）"
                                )
                                return None

                            error_text = await response.text()
                            if (
                                status_code == 502
                                and attempt < retry_502_max_attempts - 1
                            ):
                                current_attempt = attempt + 1
                                log.warning(
                                    f"[视频生成-{mode_str}] API 第 {current_attempt}/{retry_502_max_attempts} 次请求返回 502，"
                                    f"{retry_502_delay_seconds:.1f} 秒后重试"
                                )
                                await asyncio.sleep(retry_502_delay_seconds)
                                continue
                            log.error(
                                f"视频生成 API 返回错误 {response.status}: {error_text[:500]}"
                            )
                            return None

                    if should_retry_empty_result:
                        await asyncio.sleep(min(1.0 * (empty_attempt + 1), 3.0))
                        continue

                return None

        except asyncio.TimeoutError:
            log.error("视频生成 API 请求超时")
            return None
        except Exception as e:
            log.error(f"视频生成时发生错误: {e}", exc_info=True)
            return None

    async def extend_video(
        self,
        *,
        post_id: str,
        prompt: str,
        video_length: int = 10,
        model: Optional[str] = None,
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        stream: bool = False,
        video_extension_start_time: Optional[float] = None,
        stitch_with_extend: bool = True,
    ) -> Optional[VideoResult]:
        """
        调用 /video/extend 扩展已有视频。
        """
        if not self.is_available():
            log.error("视频延长服务不可用")
            return None

        if not post_id.strip():
            log.error("视频延长失败：post_id 为空")
            return None

        config = app_config.VIDEO_GEN_CONFIG
        default_model = config.get("MODEL_NAME", "grok-imagine-1.0-video")
        model_name = model.strip() if isinstance(model, str) and model.strip() else default_model

        payload: Dict[str, Any] = {
            "model": model_name,
            "post_id": post_id.strip(),
            "prompt": prompt,
            "video_length": int(max(1, min(60, video_length))),
            "aspect_ratio": aspect_ratio or "16:9",
            "resolution": resolution or "720p",
            "stream": bool(stream),
            "stitch_with_extend": bool(stitch_with_extend),
        }
        if video_extension_start_time is not None:
            payload["video_extension_start_time"] = float(video_extension_start_time)

        base_url = self._client["base_url"].rstrip("/")
        api_key = self._client["api_key"]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base_url}/video/extend",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=300),
                ) as response:
                    if response.status != 200:
                        err_text = await response.text()
                        log.error(f"视频延长 API 返回错误 {response.status}: {err_text[:500]}")
                        return None

                    data = await response.json()
                    return self._extract_video_from_response(data, config.get("VIDEO_FORMAT", "url"))
        except asyncio.TimeoutError:
            log.error("视频延长 API 请求超时")
            return None
        except Exception as e:
            log.error(f"视频延长时发生错误: {e}", exc_info=True)
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

        post_id: Optional[str] = None
        if isinstance(data, dict):
            raw_post_id = data.get("post_id") or data.get("id")
            if raw_post_id is not None:
                post_id = str(raw_post_id)

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
            result = self._extract_video_html(text_content, video_urls)
        else:
            result = self._extract_video_url(text_content, video_urls)

        if result and post_id and not result.post_id:
            result.post_id = post_id
        return result

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
