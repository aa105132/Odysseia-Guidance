# -*- coding: utf-8 -*-

from typing import Optional


def should_spoiler_image(content_rating: Optional[str]) -> bool:
    """仅对 NSFW 图片启用 Discord spoiler 遮罩。"""
    return str(content_rating or "").strip().lower() == "nsfw"
