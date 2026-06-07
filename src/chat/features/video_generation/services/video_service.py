# -*- coding: utf-8 -*-

"""
视频生成服务
使用专用的 /v1/videos API 生成视频

支持两种视频格式:
1. URL: 直接从 API 响应中提取视频 URL
2. HTML: 从 API 响应中提取 HTML 页面中的视频链接
"""

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import aiohttp

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)

LEGACY_VIDEO_ENDPOINT_SUFFIXES = (
    "/v1/chat/completions",
    "/chat/completions",
    "/v1/responses",
    "/responses",
    "/v1/images/generations",
    "/images/generations",
    "/v1/images/edits",
    "/images/edits",
)


@dataclass
class VideoResult:
    """视频生成结果"""

    url: Optional[str] = None
    html_content: Optional[str] = None
    text_response: Optional[str] = None
    post_id: Optional[str] = None
    format_type: str = "url"


class VideoGenerationService:
    """视频生成服务类"""

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
        log.info("视频生成服务已初始化, Base URL: %s...", base_url[:30])

    def is_available(self) -> bool:
        """检查服务是否可用"""
        return self._client is not None and app_config.VIDEO_GEN_CONFIG.get(
            "ENABLED", False
        )

    def reinitialize(self):
        """重新初始化客户端"""
        self._client = None
        self._initialize_client()

    def _resolve_videos_endpoint(self) -> str:
        """根据 BASE_URL 推导最终的视频生成端点。"""
        raw_base_url = str(self._client["base_url"]).strip().rstrip("/")
        if not raw_base_url:
            raise ValueError("视频生成 BASE_URL 为空")

        base_url = raw_base_url
        if base_url.endswith("/v1/video/generate"):
            return base_url
        if base_url.endswith("/v1/videos") or base_url.endswith("/videos"):
            return base_url

        for legacy_suffix in LEGACY_VIDEO_ENDPOINT_SUFFIXES:
            if base_url.endswith(legacy_suffix):
                normalized_root = base_url[: -len(legacy_suffix)].rstrip("/")
                normalized_endpoint = f"{normalized_root}/v1/videos"
                log.warning(
                    "视频生成 BASE_URL 指向旧兼容端点，已自动改为 /v1/videos: %s -> %s",
                    raw_base_url,
                    normalized_endpoint,
                )
                return normalized_endpoint

        if base_url.endswith("/v1"):
            return f"{base_url}/videos"
        return f"{base_url}/v1/videos"

    def _normalize_duration(self, duration: Any, min_seconds: Optional[int] = None) -> int:
        """将时长限制到当前允许范围。"""
        effective_min = int(min_seconds or app_config.VIDEO_GEN_MIN_SECONDS)
        config_max = int(
            app_config.VIDEO_GEN_CONFIG.get(
                "MAX_DURATION", app_config.VIDEO_GEN_MAX_SECONDS
            )
        )
        effective_max = min(
            app_config.VIDEO_GEN_MAX_SECONDS,
            max(effective_min, config_max),
        )
        try:
            parsed_duration = int(duration)
        except (TypeError, ValueError):
            parsed_duration = effective_min
        return min(max(effective_min, parsed_duration), effective_max)

    def _normalize_size(self, size: Optional[str]) -> str:
        """规范化画幅尺寸。"""
        default_size = str(
            app_config.VIDEO_GEN_CONFIG.get(
                "DEFAULT_SIZE", app_config.VIDEO_GEN_ALLOWED_SIZES[0]
            )
        ).strip()
        if default_size not in app_config.VIDEO_GEN_ALLOWED_SIZES:
            default_size = app_config.VIDEO_GEN_ALLOWED_SIZES[0]

        normalized_size = str(size or default_size).strip()
        if normalized_size not in app_config.VIDEO_GEN_ALLOWED_SIZES:
            log.warning("视频尺寸不受支持，已回退到默认值: %s", normalized_size)
            return default_size
        return normalized_size

    def _is_video_generate_endpoint(self, endpoint: str) -> bool:
        """判断是否为 /v1/video/generate 兼容端点。"""
        return str(endpoint or "").rstrip("/").endswith("/v1/video/generate")

    def _size_to_aspect_ratio(self, size: str) -> str:
        """将内部 size 映射为 /v1/video/generate 使用的 aspect_ratio。"""
        aspect_ratio_map = {
            "1280x720": "16:9",
            "720x1280": "9:16",
            "1792x1024": "16:9",
            "1024x1792": "9:16",
            "1024x1024": "1:1",
        }
        return aspect_ratio_map.get(str(size or "").strip(), "16:9")

    def _quality_to_resolution(self, quality: str) -> str:
        """将内部 quality 映射为 /v1/video/generate 使用的 resolution。"""
        return "480p" if str(quality or "").strip().lower() == "standard" else "720p"

    def _normalize_quality(self, quality: Optional[str]) -> str:
        """规范化视频质量。"""
        default_quality = str(
            app_config.VIDEO_GEN_CONFIG.get("DEFAULT_QUALITY", "high")
        ).strip().lower()
        if default_quality not in app_config.VIDEO_GEN_ALLOWED_QUALITIES:
            default_quality = "high"

        normalized_quality = str(quality or default_quality).strip().lower()
        if normalized_quality not in app_config.VIDEO_GEN_ALLOWED_QUALITIES:
            log.warning("视频质量不受支持，已回退到默认值: %s", normalized_quality)
            return default_quality
        return normalized_quality

    async def _download_reference_image_as_data_uri(self, image_url: str) -> Optional[str]:
        """下载参考图并转为 data URI，避免上游无法自行拉取外链。"""
        normalized_url = str(image_url or "").strip()
        if not normalized_url.startswith(("http://", "https://")):
            return None

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    normalized_url,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status != 200:
                        log.warning(
                            "下载视频参考图失败，状态码: %s, url: %s",
                            response.status,
                            normalized_url[:160],
                        )
                        return None
                    image_bytes = await response.read()
                    if not image_bytes:
                        log.warning("下载视频参考图为空: %s", normalized_url[:160])
                        return None
                    content_type = str(response.headers.get("Content-Type") or "").split(";")[0].strip()
                    if not content_type.startswith("image/"):
                        content_type = "image/png"
                    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
                    return f"data:{content_type};base64,{image_b64}"
        except Exception as exc:
            log.warning("下载视频参考图异常，保留原 URL 兜底: %s", exc)
            return None

    async def _build_image_reference(
        self,
        *,
        image_data: Optional[bytes],
        image_mime_type: Optional[str],
        reference_images: Optional[List[Dict[str, Any]]],
        reference_image_url: Optional[str],
    ) -> Optional[Any]:
        """构建视频接口所需的参考图字段，优先返回 data URI。"""
        if isinstance(reference_image_url, str):
            normalized_url = reference_image_url.strip()
            if normalized_url:
                if normalized_url.startswith("data:"):
                    return normalized_url
                if normalized_url.startswith(("http://", "https://")):
                    data_uri = await self._download_reference_image_as_data_uri(normalized_url)
                    if data_uri:
                        return data_uri
                    return {"image_url": normalized_url}
                log.warning("参考图 URL 格式无效，已忽略: %s", normalized_url[:120])

        normalized_reference_images: List[Dict[str, Any]] = []
        if reference_images and isinstance(reference_images, list):
            for ref in reference_images:
                if isinstance(ref, dict) and ref.get("data"):
                    normalized_reference_images.append(
                        {
                            "data": ref["data"],
                            "mime_type": ref.get("mime_type", "image/png"),
                        }
                    )

        if not normalized_reference_images and image_data:
            normalized_reference_images.append(
                {
                    "data": image_data,
                    "mime_type": image_mime_type or "image/png",
                }
            )

        if not normalized_reference_images:
            return None

        if len(normalized_reference_images) > 1:
            log.info("当前上游视频链路仅使用第 1 张参考图，其余参考图将忽略")

        first_reference = normalized_reference_images[0]
        image_b64 = base64.b64encode(first_reference["data"]).decode("utf-8")
        mime_type = first_reference.get("mime_type", "image/png")
        return f"data:{mime_type};base64,{image_b64}"

    def _extract_json_candidates_from_stream_text(self, raw_text: str) -> List[dict]:
        """从 SSE/流式文本中提取可能的 JSON payload。"""
        candidates: List[dict] = []
        for raw_line in str(raw_text or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if not line or line == "[DONE]":
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                log.debug("视频流式片段不是合法 JSON，已跳过: %s", line[:200])
                continue
            if isinstance(payload, dict):
                candidates.append(payload)
        return candidates

    async def _read_video_response_payload(
        self,
        response: aiohttp.ClientResponse,
        video_format: str,
    ) -> Optional[dict]:
        """读取视频 API 响应，兼容普通 JSON 与 SSE/流式心跳。"""
        content_type = str(getattr(response, "headers", {}).get("content-type", "")).lower()
        is_stream_response = "text/event-stream" in content_type

        if not is_stream_response:
            try:
                return await response.json()
            except Exception:
                raw_text = await response.text()
                stream_candidates = self._extract_json_candidates_from_stream_text(raw_text)
                if stream_candidates:
                    return stream_candidates[-1]
                return json.loads(raw_text)

        last_payload: Optional[dict] = None
        async for raw_chunk in response.content:
            if not raw_chunk:
                continue
            chunk_text = raw_chunk.decode("utf-8", errors="replace")
            for payload in self._extract_json_candidates_from_stream_text(chunk_text):
                last_payload = payload
                if self._extract_video_from_response(payload, video_format) is not None:
                    return payload

        return last_payload

    async def generate_video(
        self,
        prompt: str,
        duration: int = 6,
        image_data: Optional[bytes] = None,
        image_mime_type: Optional[str] = None,
        reference_images: Optional[List[Dict[str, Any]]] = None,
        model_override: Optional[str] = None,
        size: Optional[str] = None,
        quality: Optional[str] = None,
        reference_image_url: Optional[str] = None,
    ) -> Optional[VideoResult]:
        """生成视频（支持文生视频和图生视频）"""
        if not self.is_available():
            log.error("视频生成服务不可用")
            return None
        if not isinstance(prompt, str) or not prompt.strip():
            log.error("视频生成失败：prompt 为空")
            return None

        config = app_config.VIDEO_GEN_CONFIG
        video_format = config.get("VIDEO_FORMAT", "url")
        endpoint = self._resolve_videos_endpoint()
        endpoint_min_duration = 1 if self._is_video_generate_endpoint(endpoint) else None
        normalized_duration = self._normalize_duration(
            duration, min_seconds=endpoint_min_duration
        )
        normalized_size = self._normalize_size(size)
        normalized_quality = self._normalize_quality(quality)
        image_reference = await self._build_image_reference(
            image_data=image_data,
            image_mime_type=image_mime_type,
            reference_images=reference_images,
            reference_image_url=reference_image_url,
        )

        default_model = config.get("MODEL_NAME", "grok-imagine-1.0-video")
        if image_reference:
            i2v_model = config.get("I2V_MODEL_NAME", "")
            model_name = i2v_model if i2v_model else default_model
            mode_str = "图生视频"
        else:
            model_name = default_model
            mode_str = "文生视频"

        if model_override and str(model_override).strip():
            model_name = str(model_override).strip()

        log.info(
            "使用模型 %s 生成视频 (%s), 时长: %ss, size: %s, quality: %s, endpoint: %s",
            model_name,
            mode_str,
            normalized_duration,
            normalized_size,
            normalized_quality,
            endpoint,
        )

        if self._is_video_generate_endpoint(endpoint):
            is_image_to_video = image_reference is not None
            payload: Dict[str, Any] = {
                "model": model_name,
                "mode": "image-to-video" if is_image_to_video else "text-to-video",
                "prompt": prompt.strip(),
                "duration": normalized_duration,
                "aspect_ratio": self._size_to_aspect_ratio(normalized_size),
                "resolution": self._quality_to_resolution(normalized_quality),
                "format": "mp4",
                "generate_audio": False if is_image_to_video else True,
                "stream": True,
            }
            if image_reference is not None:
                if isinstance(image_reference, dict) and image_reference.get("image_url"):
                    image_url = image_reference["image_url"]
                    payload["first_frame_url"] = image_url
                    payload["images"] = [image_url]
                elif isinstance(image_reference, str):
                    payload["first_frame_resource_path"] = image_reference
                    payload["images"] = [image_reference]
        else:
            payload = {
                "model": model_name,
                "prompt": prompt.strip(),
                "size": normalized_size,
                "seconds": normalized_duration,
                "quality": normalized_quality,
                "stream": True,
            }
            if image_reference is not None:
                payload["image_reference"] = image_reference

        retry_502_max_attempts = max(
            1, int(config.get("RETRY_502_MAX_ATTEMPTS", 3))
        )
        retry_502_delay_seconds = max(
            0.0, float(config.get("RETRY_502_DELAY_SECONDS", 2))
        )
        empty_result_max_attempts = max(
            1, int(config.get("EMPTY_RESULT_MAX_RETRIES", 3))
        )

        headers = {
            "Authorization": f"Bearer {self._client['api_key']}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                for empty_attempt in range(empty_result_max_attempts):
                    should_retry_empty_result = False

                    for attempt in range(retry_502_max_attempts):
                        async with session.post(
                            endpoint,
                            headers=headers,
                            json=payload,
                            timeout=aiohttp.ClientTimeout(total=300),
                        ) as response:
                            status_code = response.status

                            if status_code == 200:
                                data = await self._read_video_response_payload(
                                    response, video_format
                                )
                                if not isinstance(data, dict):
                                    log.warning("视频生成 API 流式响应未返回有效 JSON payload")
                                    data = {}
                                video_result = self._extract_video_from_response(
                                    data, video_format
                                )
                                if video_result is not None:
                                    if empty_attempt > 0:
                                        log.info(
                                            "[视频生成-%s] 空回重试成功（第 %s/%s 次）",
                                            mode_str,
                                            empty_attempt + 1,
                                            empty_result_max_attempts,
                                        )
                                    return video_result

                                if empty_attempt < empty_result_max_attempts - 1:
                                    log.warning(
                                        "[视频生成-%s] 第 %s/%s 次返回空结果，准备自动重试...",
                                        mode_str,
                                        empty_attempt + 1,
                                        empty_result_max_attempts,
                                    )
                                    should_retry_empty_result = True
                                    break

                                log.error(
                                    "[视频生成-%s] 连续空回，已达到最大重试次数（%s）",
                                    mode_str,
                                    empty_result_max_attempts,
                                )
                                return None

                            error_text = await response.text()
                            if status_code == 502 and attempt < retry_502_max_attempts - 1:
                                log.warning(
                                    "[视频生成-%s] API 第 %s/%s 次请求返回 502，%.1f 秒后重试",
                                    mode_str,
                                    attempt + 1,
                                    retry_502_max_attempts,
                                    retry_502_delay_seconds,
                                )
                                await asyncio.sleep(retry_502_delay_seconds)
                                continue

                            log.error(
                                "视频生成 API 返回错误 %s, endpoint=%s, base_url=%s: %s",
                                response.status,
                                endpoint,
                                self._client["base_url"],
                                error_text[:500],
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
            log.error("视频生成时发生错误: %s", e, exc_info=True)
            return None

    def _extract_video_from_response(
        self, data: dict, video_format: str
    ) -> Optional[VideoResult]:
        """从 API 响应中提取视频数据"""
        text_content = ""
        video_urls: List[str] = []
        post_id: Optional[str] = None

        if isinstance(data, dict):
            raw_post_id = data.get("post_id") or data.get("id")
            if raw_post_id is not None:
                post_id = str(raw_post_id)

            for url_key in ("url", "video_url", "download_url"):
                direct_url = data.get(url_key)
                if isinstance(direct_url, str) and direct_url:
                    video_urls.append(direct_url)

            if isinstance(data.get("result"), dict):
                result_dict = data["result"]
                for url_key in ("url", "video_url", "download_url"):
                    direct_url = result_dict.get(url_key)
                    if isinstance(direct_url, str) and direct_url:
                        video_urls.append(direct_url)

            if isinstance(data.get("data"), list):
                for item in data["data"]:
                    if not isinstance(item, dict):
                        continue
                    for url_key in ("url", "video_url", "download_url"):
                        direct_url = item.get(url_key)
                        if isinstance(direct_url, str) and direct_url:
                            video_urls.append(direct_url)
                    revised_prompt = item.get("revised_prompt")
                    if isinstance(revised_prompt, str) and revised_prompt and not text_content:
                        text_content = revised_prompt

            nested_data = data.get("data")
            if isinstance(nested_data, dict):
                for url_key in ("url", "video_url", "download_url"):
                    direct_url = nested_data.get(url_key)
                    if isinstance(direct_url, str) and direct_url:
                        video_urls.append(direct_url)
                nested_outputs = nested_data.get("outputs")
                if isinstance(nested_outputs, list):
                    for item in nested_outputs:
                        if not isinstance(item, dict):
                            continue
                        for url_key in ("url", "video_url", "download_url"):
                            direct_url = item.get(url_key)
                            if isinstance(direct_url, str) and direct_url:
                                video_urls.append(direct_url)

            if isinstance(data.get("outputs"), list):
                for item in data["outputs"]:
                    if not isinstance(item, dict):
                        continue
                    for url_key in ("url", "video_url", "download_url"):
                        direct_url = item.get(url_key)
                        if isinstance(direct_url, str) and direct_url:
                            video_urls.append(direct_url)

            if isinstance(data.get("output"), list):
                for item in data["output"]:
                    if not isinstance(item, dict):
                        continue
                    for url_key in ("url", "video_url", "download_url"):
                        direct_url = item.get(url_key)
                        if isinstance(direct_url, str) and direct_url:
                            video_urls.append(direct_url)

        if "choices" in data:
            for choice in data["choices"]:
                message = choice.get("message", {})
                content = message.get("content")

                if isinstance(content, str):
                    text_content = content
                elif isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "text" or "text" in part:
                            text_content += part.get("text", "")
                        elif part.get("type") == "video_url":
                            url_data = part.get("video_url", {})
                            if isinstance(url_data, dict) and "url" in url_data:
                                video_urls.append(url_data["url"])

        if not text_content and not video_urls:
            log.warning("视频生成 API 返回空内容")
            log.debug("完整响应: %s", json.dumps(data, ensure_ascii=False)[:1000])
            return None

        if video_format == "html":
            result = self._extract_video_html(text_content, video_urls)
        else:
            result = self._extract_video_url(text_content, video_urls)

        if result and post_id and not result.post_id:
            result.post_id = post_id
        return result

    def _extract_video_url(
        self, text_content: str, video_urls: List[str]
    ) -> Optional[VideoResult]:
        """从响应中提取视频 URL"""
        if video_urls:
            return VideoResult(
                url=video_urls[0],
                text_response=text_content if text_content else None,
                format_type="url",
            )

        video_src_pattern = r'<video[^>]+src=["\'](https?://[^"\']+)["\']'
        src_matches = re.findall(video_src_pattern, text_content, re.IGNORECASE)
        if src_matches:
            return VideoResult(
                url=src_matches[0],
                text_response=text_content,
                format_type="url",
            )

        video_url_pattern = (
            r'(https?://[^\s\)\]\"\'<>]+\.(?:mp4|webm|mov|avi|mkv|m3u8)'
            r'(?:\?[^\s\)\]\"\'<>]*)?)'
        )
        matches = re.findall(video_url_pattern, text_content, re.IGNORECASE)
        if matches:
            return VideoResult(
                url=matches[0],
                text_response=text_content,
                format_type="url",
            )

        md_video_pattern = r'\[(?:[^\]]*(?:video|视频|播放)[^\]]*)\]\((https?://[^\)]+)\)'
        md_matches = re.findall(md_video_pattern, text_content, re.IGNORECASE)
        if md_matches:
            return VideoResult(
                url=md_matches[0],
                text_response=text_content,
                format_type="url",
            )

        generic_url_pattern = (
            r'(https?://[^\s\)\]\"\'<>]+(?:/video[s]?/|/media/|/stream/)'
            r'[^\s\)\]\"\'<>]*)'
        )
        generic_matches = re.findall(generic_url_pattern, text_content, re.IGNORECASE)
        if generic_matches:
            return VideoResult(
                url=generic_matches[0],
                text_response=text_content,
                format_type="url",
            )

        any_url_pattern = r'(https?://[^\s\)\]\"\'<>]+)'
        any_matches = re.findall(any_url_pattern, text_content)
        if any_matches:
            for url in any_matches:
                if not any(
                    ext in url.lower()
                    for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js"]
                ):
                    return VideoResult(
                        url=url,
                        text_response=text_content,
                        format_type="url",
                    )

        log.warning("未能从响应中提取到视频 URL")
        log.debug("响应文本: %s", text_content[:500])

        if text_content:
            return VideoResult(text_response=text_content, format_type="url")
        return None

    def _extract_video_html(
        self, text_content: str, video_urls: List[str]
    ) -> Optional[VideoResult]:
        """从响应中提取 HTML 视频内容"""
        if (
            "<video" in text_content
            or "<iframe" in text_content
            or "<!DOCTYPE" in text_content.upper()
        ):
            return VideoResult(html_content=text_content, format_type="html")

        html_block_pattern = r"```html\s*([\s\S]*?)\s*```"
        html_matches = re.findall(html_block_pattern, text_content, re.IGNORECASE)
        if html_matches:
            return VideoResult(
                html_content=html_matches[0],
                text_response=text_content,
                format_type="html",
            )

        url = None
        if video_urls:
            url = video_urls[0]
        else:
            video_url_pattern = (
                r'(https?://[^\s\)\]\"\'<>]+\.(?:mp4|webm|mov)'
                r'(?:\?[^\s\)\]\"\'<>]*)?)'
            )
            matches = re.findall(video_url_pattern, text_content, re.IGNORECASE)
            if matches:
                url = matches[0]

        if url:
            html = f"""<!DOCTYPE html>
<html><head><style>
body {{ margin: 0; display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #000; }}
video {{ max-width: 100%; max-height: 100vh; }}
</style></head><body>
<video controls autoplay loop>
<source src="{url}" type="video/mp4">
Your browser does not support the video tag.
</video>
</body></html>"""
            return VideoResult(
                url=url,
                html_content=html,
                text_response=text_content,
                format_type="html",
            )

        return self._extract_video_url(text_content, video_urls)


video_service = VideoGenerationService()
