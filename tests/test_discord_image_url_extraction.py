# -*- coding: utf-8 -*-

import os
import sys

sys.path.insert(0, os.path.abspath("."))

from src.chat.features.tools.utils.discord_image_utils import (
    extract_image_urls_from_text,
)


def test_extract_image_urls_supports_webp_plain_url():
    text = "请处理这张图：https://example.com/assets/cat.webp"
    urls = extract_image_urls_from_text(text)
    assert urls == ["https://example.com/assets/cat.webp"]


def test_extract_image_urls_supports_webp_markdown_and_query():
    text = (
        "![封面](https://cdn.example.com/images/cover.webp?token=abc123) "
        "备用链接 https://cdn.example.com/images/cover.webp?token=abc123"
    )
    urls = extract_image_urls_from_text(text)
    assert urls == ["https://cdn.example.com/images/cover.webp?token=abc123"]


def test_extract_image_urls_ignores_non_image_links():
    text = "视频: https://example.com/clip.mp4 文档: https://example.com/readme"
    urls = extract_image_urls_from_text(text)
    assert urls == []
