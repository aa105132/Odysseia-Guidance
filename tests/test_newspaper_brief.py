import asyncio
from PIL import ImageDraw
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.chat.features.tools.functions.render_newspaper_brief import (
    render_newspaper_brief,
)
from src.chat.features.tools.functions.summarize_channel import (
    text_to_newspaper_brief_image,
)


def test_render_newspaper_brief_returns_image_data():
    result = asyncio.run(
        render_newspaper_brief(
            title="测试快报",
            body="这是一段用于测试的摘要正文。" * 20,
            subtitle="副标题",
            section_name="搜索速报",
            dek="导语内容",
        )
    )

    assert isinstance(result, dict)
    assert "image_data" in result
    assert result["image_data"]["mime_type"] == "image/png"
    assert result["image_data"]["data"]


def test_text_to_newspaper_brief_image_returns_png_bytes():
    image_bytes = text_to_newspaper_brief_image(
        body="报纸正文内容。" * 40,
        title="月月简报",
        subtitle="这是一个测试副标题",
        section_name="频道纪要",
        dek="导语在这里。",
    )

    assert image_bytes is not None
    assert image_bytes.startswith(b"\x89PNG")


def test_text_to_newspaper_brief_image_does_not_pass_raw_newlines_to_draw(monkeypatch):
    drawn_texts = []
    original_text = ImageDraw.ImageDraw.text

    def tracking_text(self, xy, text, *args, **kwargs):
        if isinstance(text, str):
            drawn_texts.append(text)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", tracking_text)

    image_bytes = text_to_newspaper_brief_image(
        body="第一段内容。\n\n第二段内容。\n第三段内容。",
        title="月月简报",
        subtitle="测试副标题",
        section_name="频道纪要",
    )

    assert image_bytes is not None
    assert all("\n" not in text and "\r" not in text for text in drawn_texts)


def test_text_to_newspaper_brief_image_filters_citation_and_markdown(monkeypatch):
    drawn_texts = []
    original_text = ImageDraw.ImageDraw.text

    def tracking_text(self, xy, text, *args, **kwargs):
        if isinstance(text, str):
            drawn_texts.append(text)
        return original_text(self, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", tracking_text)

    image_bytes = text_to_newspaper_brief_image(
        body=(
            "根据预测，价格还会涨[citation:1][citation:6]。\n\n"
            "**重点**：参考[来源](https://example.com)和`DDR5`。\n"
            "> 引用内容"
        ),
        title="月月简报",
        subtitle="### 搜索 / 总结",
        section_name="搜索 / 总结",
    )

    rendered_text = "\n".join(drawn_texts)

    assert image_bytes is not None
    assert "citation:" not in rendered_text.lower()
    assert "https://example.com" not in rendered_text
    assert "**" not in rendered_text
    assert "`" not in rendered_text
    assert "重点" in rendered_text
    assert "来源" in rendered_text
    assert "DDR5" in rendered_text
    assert "引用内容" in rendered_text
