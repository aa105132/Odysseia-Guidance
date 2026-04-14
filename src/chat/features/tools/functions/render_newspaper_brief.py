# -*- coding: utf-8 -*-

import logging
from datetime import datetime
from typing import Optional

from src.chat.features.tools.tool_metadata import tool_metadata

from src.chat.features.tools.functions.summarize_channel import (
    text_to_newspaper_brief_image,
)

log = logging.getLogger(__name__)


@tool_metadata(
    name="报纸摘要",
    description="当搜索总结或频道总结较长、适合用报纸风摘要图展示时使用。图片里不要放链接。",
    emoji="📰",
    category="总结",
)
async def render_newspaper_brief(
    body: str,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    section_name: Optional[str] = None,
    issue_date: Optional[str] = None,
    dek: Optional[str] = None,
    **kwargs,
) -> dict:
    """将整理好的摘要正文渲染成报纸风 PNG。"""
    try:
        if not str(body or "").strip():
            return {"error": "报纸摘要正文不能为空。"}

        final_title = (
            str(title or "").strip()
            or str(subtitle or "").strip()
            or str(section_name or "").strip()
            or "月月简报"
        )
        final_issue_date = str(issue_date or "").strip() or datetime.now().strftime(
            "%Y-%m-%d"
        )
        image_bytes = text_to_newspaper_brief_image(
            body=str(body).strip(),
            title=final_title,
            subtitle=str(subtitle or "").strip() or None,
            section_name=str(section_name or "").strip() or "月月简报",
            issue_date=final_issue_date,
            dek=str(dek or "").strip() or None,
        )
        if not image_bytes:
            return {"error": "报纸摘要图片生成失败。"}

        return {
            "image_data": {
                "mime_type": "image/png",
                "data": image_bytes,
            },
            "title": final_title,
            "section_name": str(section_name or "").strip() or "月月简报",
            "issue_date": final_issue_date,
            "message": "报纸摘要图已生成。若需要消息源，请在图片下方单独发送链接文本。",
        }
    except Exception as e:
        log.error(f"生成报纸摘要工具失败: {e}", exc_info=True)
        return {"error": f"生成报纸摘要失败: {e}"}
