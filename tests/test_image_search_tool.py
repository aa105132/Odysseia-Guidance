import asyncio
from unittest.mock import AsyncMock

from src.chat.features.tools.functions import image_search as image_search_tool


def test_parse_html_img_results():
    html = '<html><body><img src="https://example.com/a.jpg" alt="A"><img data-src="/b.png" alt="B"></body></html>'

    results = image_search_tool._parse_image_search_results(
        html,
        base_url="https://example.com/root/",
        max_results=4,
    )

    assert [item["url"] for item in results] == [
        "https://example.com/a.jpg",
        "https://example.com/b.png",
    ]
    assert results[0]["title"] == "A"


def test_parse_markdown_and_plain_image_urls_dedupes():
    text = "![图](https://example.com/a.webp) https://example.com/a.webp https://example.com/b.jpg"

    results = image_search_tool._parse_image_search_results(text, max_results=10)

    assert [item["url"] for item in results] == [
        "https://example.com/a.webp",
        "https://example.com/b.jpg",
    ]


def test_build_results_html_contains_img_tags():
    html = image_search_tool._build_results_html(
        [{"url": "https://example.com/a.jpg", "title": "A", "source_url": ""}],
        "cat",
    )

    assert '<img src="https://example.com/a.jpg" alt="A">' in html
    assert 'data-query="cat"' in html


def test_image_search_returns_image_data_for_first_result(monkeypatch):
    monkeypatch.setitem(image_search_tool.IMAGE_SEARCH_CONFIG, "MAX_RESULTS", 6)
    monkeypatch.setattr(
        image_search_tool,
        "_post_openai_image_search",
        AsyncMock(return_value={"html": '<img src="https://example.com/a.jpg" alt="A">'}),
    )
    monkeypatch.setattr(
        image_search_tool,
        "fetch_image_from_url",
        AsyncMock(return_value={"data": b"img", "mime_type": "image/jpeg", "filename": "a.jpg"}),
    )

    result = asyncio.run(
        image_search_tool.image_search(
            "cat",
            max_results=2,
            analyze_images=True,
        )
    )

    assert result["success"] is True
    assert result["result_count"] == 1
    assert result["internal_only"] is True
    assert result["image_data"]["data"] == b"img"
    assert result["image_data_list"][0]["data"] == b"img"
    assert result["image_data"]["mime_type"] == "image/jpeg"


def test_image_search_defaults_not_send_to_channel_param_exists():
    import inspect
    sig = inspect.signature(image_search_tool.image_search)
    assert sig.parameters["send_to_channel"].default is False
    assert sig.parameters["max_send_images"].default == 6
