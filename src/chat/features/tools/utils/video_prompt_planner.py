# -*- coding: utf-8 -*-
"""视频按钮提示词规划工具。"""

import logging
from typing import Any, Dict, List, Optional

import discord

log = logging.getLogger(__name__)

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


async def extract_image_payloads_from_message(
    message: Optional[discord.Message],
    *,
    max_images: int = 3,
) -> List[Dict[str, Any]]:
    """从按钮所在消息提取图片附件，供月月看图后规划视频分镜。"""
    if not isinstance(message, discord.Message):
        return []

    payloads: List[Dict[str, Any]] = []
    for attachment in getattr(message, "attachments", []) or []:
        if len(payloads) >= max_images:
            break
        content_type = str(getattr(attachment, "content_type", "") or "").lower()
        filename = str(getattr(attachment, "filename", "") or "")
        lower_filename = filename.lower()
        if not (content_type.startswith("image/") or lower_filename.endswith(_IMAGE_EXTENSIONS)):
            continue
        try:
            payloads.append(
                {
                    "data": await attachment.read(),
                    "mime_type": content_type or _guess_image_mime_type(lower_filename),
                    "filename": filename or "reference.png",
                }
            )
        except Exception as e:
            log.warning("读取按钮消息图片附件失败: %s", e)
    return payloads


def _guess_image_mime_type(filename: str) -> str:
    if filename.endswith(".jpg") or filename.endswith(".jpeg"):
        return "image/jpeg"
    if filename.endswith(".webp"):
        return "image/webp"
    if filename.endswith(".gif"):
        return "image/gif"
    return "image/png"


def _looks_complete_video_prompt(prompt: str, duration: int) -> bool:
    """粗略判断视频分镜是否完整，避免半截提示词直接送去生成。"""
    text = str(prompt or "").strip()
    if len(text) < 120:
        return False

    required_markers = (
        "不要文字",
        "不要水印",
        "不要闪烁",
    )
    if not all(marker in text for marker in required_markers):
        return False

    safe_duration = max(5, min(15, int(duration or 8)))
    has_start_timeline = "0-" in text or "0到" in text or "0 至" in text
    has_end_timeline = str(safe_duration) in text and "秒" in text
    has_terminal_punctuation = text[-1] in "。！？.!?"
    return has_start_timeline and has_end_timeline and has_terminal_punctuation


def _normalize_planned_video_prompt(prompt: str) -> str:
    """清理模型输出，并在缺少安全结尾时补齐硬性负面约束。"""
    text = str(prompt or "").strip().strip('"').strip("'")
    if not text:
        return ""
    if len(text) > 3200:
        text = text[:3200].rstrip("，,；;：:")
    if not text.endswith(("。", "！", "？", ".", "!", "?")):
        text += "。"

    negative_tail = "不要文字，不要水印，不要闪烁，不要变脸，不要肢体畸变，不要背景乱变。"
    if "不要文字" not in text or "不要水印" not in text or "不要闪烁" not in text:
        text += negative_tail
    return text


async def plan_video_prompt_with_yueyue(
    *,
    image_prompt: str = "",
    user_idea: str = "",
    images: Optional[List[Dict[str, Any]]] = None,
    mode: str = "image_to_video",
    duration: int = 8,
) -> Optional[str]:
    """让月月先看参考图/尾帧，再生成用于视频工具的中文分镜提示词。"""
    normalized_images = [img for img in (images or []) if isinstance(img, dict) and img.get("data")]
    if not normalized_images:
        return None

    from src.chat.services.gemini_service import gemini_service

    safe_duration = max(5, min(15, int(duration or 8)))
    midpoint = max(2, min(safe_duration - 1, safe_duration // 2))
    normalized_idea = str(user_idea or "").strip()
    normalized_image_prompt = str(image_prompt or "").strip()

    if mode == "extend":
        task_text = (
            "你现在要为“延长视频”写下一段图生视频提示词。"
            "我给你的图片是上一段视频的尾帧/最后一帧，你必须先观察尾帧里的主体、场景、构图、光影、动作趋势，"
            "然后写一段能自然承接尾帧继续生成的视频分镜。"
        )
        continuity_rule = (
            "必须把尾帧当作新片段第0秒的起点，保持主体身份、服装、背景、镜头方向、光影和画风连续，"
            "不要突然换人、换场景、跳镜或重置动作。"
        )
    else:
        task_text = (
            "你现在要为“根据当前图片生成视频”写图生视频提示词。"
            "我给你的图片就是按钮所在消息中的结果图/参考图，你必须先观察图片中的主体、场景、构图、光影和风格，"
            "再写一段适合让这张图动起来的视频分镜。"
        )
        continuity_rule = (
            "必须保持图片中的主体身份、服装、场景、构图和画风一致，只设计自然动作、表情、镜头运动和二级动画。"
        )

    prompt_parts = [
        task_text,
        "输出要求：",
        "1) 只输出最终视频提示词，不要解释、不要标题、不要项目符号。",
        "2) 必须使用简体中文自然语言。",
        f"3) 按 {safe_duration} 秒视频设计明确分镜，至少包含 0-{midpoint} 秒、{midpoint}-{safe_duration} 秒两个时间段；需要时可拆得更细。",
        "4) 描述主体动作、表情、镜头运动、环境运动、光影变化和收束画面。",
        f"5) {continuity_rule}",
        "6) 结尾加入安全负面约束：不要文字，不要水印，不要闪烁，不要变脸，不要肢体畸变，不要背景乱变。",
    ]
    if normalized_image_prompt:
        prompt_parts.append(f"原图/原视频提示词线索：{normalized_image_prompt}")
    if normalized_idea:
        prompt_parts.append(f"用户这次点击按钮时补充的想法：{normalized_idea}")
    else:
        prompt_parts.append("用户没有补充想法，请你根据画面内容自己设计最合适、最好看的自然运动。")

    request_prompt = "\n".join(prompt_parts)
    planned = await gemini_service.generate_simple_response(
        prompt=request_prompt,
        generation_config={
            "temperature": 0.7,
            "max_output_tokens": 4096,
        },
        images=normalized_images,
        return_error_text=False,
    )
    planned = _normalize_planned_video_prompt(str(planned or ""))

    if not _looks_complete_video_prompt(planned, safe_duration):
        log.warning("月月生成的视频分镜疑似不完整，准备提高约束重试一次: %s", planned[:120])
        retry_prompt = (
            f"{request_prompt}\n\n"
            "上一轮输出疑似被截断或不完整。请重新输出一段完整的视频提示词，"
            "必须写完整时间轴，必须以“不要文字，不要水印，不要闪烁，不要变脸，不要肢体畸变，不要背景乱变。”结尾。"
        )
        retry_planned = await gemini_service.generate_simple_response(
            prompt=retry_prompt,
            generation_config={
                "temperature": 0.55,
                "max_output_tokens": 4096,
            },
            images=normalized_images,
            return_error_text=False,
        )
        retry_planned = _normalize_planned_video_prompt(str(retry_planned or ""))
        if retry_planned:
            planned = retry_planned

    if not planned:
        return None
    return planned
