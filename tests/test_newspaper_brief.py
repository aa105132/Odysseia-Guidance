import asyncio
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
