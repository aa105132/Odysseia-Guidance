# -*- coding: utf-8 -*-

"""
GIF 动图分析工具

让模型在需要“看懂动图过程”时，主动调用该工具。
工具会提取 GIF 关键帧并拼成一张时间序列拼图，
再把拼图作为图片返回给模型视觉通道进行分析。
"""

import io
import logging
from typing import Optional, Dict, Any, List

import discord
from PIL import Image, ImageDraw

from src.chat.features.tools.tool_metadata import tool_metadata
from src.chat.features.tools.utils.discord_image_utils import (
    fetch_image_from_url,
    extract_image_from_message_url,
)
from src.chat.utils.image_utils import extract_image_frames_for_ai

log = logging.getLogger(__name__)


def _is_animated_image(image_bytes: bytes, mime_type: str = "") -> bool:
    """判断图片是否为多帧动画图。"""
    if not image_bytes:
        return False

    if "gif" in (mime_type or "").lower():
        return True

    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            return bool(getattr(img, "is_animated", False)) and int(
                getattr(img, "n_frames", 1) or 1
            ) > 1
    except Exception:
        return False


def _build_storyboard_png(frames: List[Image.Image], max_frame_side: int = 240) -> bytes:
    """将关键帧拼接为时间序列图（从左到右）。"""
    if not frames:
        raise ValueError("没有可用于拼图的帧。")

    prepared: List[Image.Image] = []
    for frame in frames:
        temp = frame.convert("RGBA")
        temp.thumbnail((max_frame_side, max_frame_side), Image.Resampling.LANCZOS)
        prepared.append(temp)

    gap = 8
    top_bar_height = 28
    max_height = max(img.height for img in prepared)
    total_width = sum(img.width for img in prepared) + gap * (len(prepared) - 1)
    total_height = top_bar_height + max_height

    canvas = Image.new("RGBA", (total_width, total_height), (16, 18, 24, 255))
    draw = ImageDraw.Draw(canvas)

    x_offset = 0
    for idx, img in enumerate(prepared):
        y_offset = top_bar_height + (max_height - img.height) // 2
        canvas.paste(img, (x_offset, y_offset), img)
        draw.text((x_offset + 4, 6), f"F{idx + 1}", fill=(255, 255, 255, 255))
        x_offset += img.width + gap

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG")
    return output.getvalue()


async def _extract_gif_from_message(
    message: Optional[discord.Message],
) -> Optional[Dict[str, Any]]:
    """从消息中提取 GIF 或其他动态图片。"""
    if not message:
        return None

    # 1) 优先附件
    for attachment in getattr(message, "attachments", []) or []:
        content_type = (getattr(attachment, "content_type", "") or "").lower()
        filename = (getattr(attachment, "filename", "") or "").lower()

        if not content_type.startswith("image/") and not filename.endswith(
            (".gif", ".png", ".webp", ".apng")
        ):
            continue

        try:
            image_bytes = await attachment.read()
        except Exception as e:
            log.warning(f"读取附件失败: {e}")
            continue

        mime_type = content_type or "image/gif"
        if _is_animated_image(image_bytes, mime_type):
            return {
                "data": image_bytes,
                "mime_type": mime_type,
                "filename": getattr(attachment, "filename", "attachment.gif"),
            }

    # 2) 尝试从消息文本/Embed URL 提取
    try:
        url_image = await extract_image_from_message_url(message)
        if url_image and _is_animated_image(
            url_image.get("data", b""), url_image.get("mime_type", "")
        ):
            return url_image
    except Exception as e:
        log.warning(f"从消息 URL 提取动态图失败: {e}")

    return None


@tool_metadata(
    name="查看GIF",
    description="提取并分析 GIF 关键帧，适用于描述动图从头到尾的变化",
    emoji="🎞️",
    category="图像识别",
)
async def analyze_gif(
    gif_url: Optional[str] = None,
    max_frames: int = 6,
    **kwargs,
) -> dict:
    """
    提取 GIF 动图关键帧并返回时间序列拼图，供模型视觉分析。

    [必须调用场景]
    - 用户要求“详细描述这张 gif 从头到尾发生了什么”。
    - 用户质疑“你到底能不能看懂动图”。
    - 用户需要动作变化、时间顺序、帧间差异描述。

    [参数说明]
    - gif_url: 可选，直接指定 GIF 链接。
    - max_frames: 关键帧数量上限，推荐 4~8。

    [返回]
    - image_data: 关键帧拼图（PNG）
    - frame_info: 关键帧抽样信息
    - hint: 分析提示
    """
    message: Optional[discord.Message] = kwargs.get("message")

    # 限制帧数，避免上下文和 token 过大
    safe_max_frames = max(2, min(int(max_frames or 6), 8))

    source_image: Optional[Dict[str, Any]] = None
    source_label = "unknown"

    # 1) 用户显式 URL
    if gif_url and gif_url.strip():
        fetched = await fetch_image_from_url(gif_url.strip())
        if not fetched:
            return {
                "error": True,
                "hint": "GIF 链接下载失败，请检查链接是否可访问。",
            }
        if not _is_animated_image(fetched.get("data", b""), fetched.get("mime_type", "")):
            return {
                "error": True,
                "hint": "提供的链接不是可识别的动态图（GIF/APNG）。",
            }
        source_image = fetched
        source_label = "url"

    # 2) 当前消息
    if not source_image:
        source_image = await _extract_gif_from_message(message)
        if source_image:
            source_label = "current_message"

    # 3) 回复消息
    if not source_image and message and message.reference and message.reference.message_id:
        try:
            ref_msg = await message.channel.fetch_message(message.reference.message_id)
            source_image = await _extract_gif_from_message(ref_msg)
            if source_image:
                source_label = "replied_message"
        except Exception as e:
            log.warning(f"读取回复消息中的 GIF 失败: {e}")

    if not source_image:
        return {
            "error": True,
            "hint": "没有找到可分析的 GIF 动图。请在当前消息附上 GIF，或传入 gif_url。",
        }

    image_bytes = source_image.get("data", b"")
    mime_type = source_image.get("mime_type", "image/gif")

    try:
        frames, frame_meta = extract_image_frames_for_ai(
            image_bytes=image_bytes,
            mime_type=mime_type,
            max_gif_frames=safe_max_frames,
        )
        storyboard_png = _build_storyboard_png(frames)
    except Exception as e:
        log.error(f"GIF 关键帧处理失败: {e}", exc_info=True)
        return {
            "error": True,
            "hint": "GIF 关键帧提取失败，请换一张动图再试。",
        }

    return {
        "image_data": {
            "data": storyboard_png,
            "mime_type": "image/png",
        },
        "frame_info": {
            "source": source_label,
            "total_frames": frame_meta.get("total_frames", len(frames)),
            "sampled_frames": frame_meta.get("sampled_frames", len(frames)),
            "frame_indices": frame_meta.get("frame_indices", []),
        },
        "hint": (
            "这是一张 GIF 时间序列关键帧拼图，从左到右代表时间推进。"
            "请基于帧间变化描述动作过程，不要编造画面中不存在的元素。"
        ),
    }

