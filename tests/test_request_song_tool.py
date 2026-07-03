import asyncio
import inspect
from unittest.mock import AsyncMock

from src.chat.features.tools.functions import request_song as request_song_tool
from src.chat.features.tools.tool_loader import load_tools_from_directory


class _FakeDiscordFile:
    def __init__(self, fp, filename):
        self.fp = fp
        self.filename = filename


def test_pick_best_track_prefers_matching_artist_and_title():
    tracks = [
        {
            "id": "other",
            "name": "太陽之子",
            "artist": ["周杰倫"],
            "album": "太陽之子",
            "source": "joox",
        },
        {
            "id": "target",
            "name": "晴天",
            "artist": ["周杰倫"],
            "album": "葉惠美",
            "source": "joox",
        },
    ]

    selected = request_song_tool._pick_best_track(
        tracks,
        query="周杰伦 晴天",
        artist="周杰伦",
    )

    assert selected["id"] == "target"


def test_request_song_falls_back_sources_and_sends_audio(monkeypatch):
    async def fake_search_source(session, source, query, count):
        if source == "netease":
            return []
        return [
            {
                "id": "bLnv0PqDX_qAlIqapc+Okw==",
                "name": "晴天",
                "artist": ["周杰倫"],
                "album": "葉惠美",
                "source": source,
            }
        ]

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(
        request_song_tool,
        "_fetch_song_url",
        AsyncMock(
            return_value={
                "url": "https://example.com/sunny.mp3",
                "br": 128,
                "size": 4317292,
            }
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_download_audio",
        AsyncMock(return_value=(b"audio-bytes", "audio/mpeg")),
    )
    monkeypatch.setattr(request_song_tool.discord, "File", _FakeDiscordFile)
    monkeypatch.setitem(
        request_song_tool.MUSIC_REQUEST_CONFIG,
        "DEFAULT_SOURCES",
        ["netease", "joox"],
    )
    monkeypatch.setitem(
        request_song_tool.MUSIC_REQUEST_CONFIG,
        "MAX_DOWNLOAD_BYTES",
        25 * 1024 * 1024,
    )
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()
    channel = type("Channel", (), {})()
    channel.send = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="周杰伦 晴天",
            artist="周杰伦",
            song_comment="像雨后突然透进来的光，听起来很适合偷偷怀旧一下。",
            channel=channel,
            message=message,
        )
    )

    assert result["success"] is True
    assert result["skip_ai_response"] is True
    assert result["sent_to_channel"] is True
    assert result["track"]["name"] == "晴天"
    assert result["track"]["source"] == "joox"
    message.reply.assert_awaited_once()
    _, kwargs = message.reply.await_args
    assert kwargs["mention_author"] is False
    assert kwargs["file"].filename.endswith(".mp3")
    assert "晴天" in kwargs["content"]
    assert "月月短评：像雨后突然透进来的光，听起来很适合偷偷怀旧一下。" in kwargs["content"]
    channel.send.assert_not_called()


def test_request_song_uses_llm_selection_before_fetching_url(monkeypatch):
    async def fake_search_source(session, source, query, count):
        return [
            {
                "id": "cover",
                "name": "晴天 (翻唱版)",
                "artist": ["其他歌手"],
                "album": "晴天翻唱",
                "source": source,
            },
            {
                "id": "original",
                "name": "晴天",
                "artist": ["周杰倫"],
                "album": "葉惠美",
                "source": source,
            },
        ]

    fetched_tracks = []

    async def fake_fetch_song_url(session, track, br):
        fetched_tracks.append(track)
        return {
            "url": "https://example.com/original.mp3",
            "br": 128,
            "size": 4317292,
        }

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(request_song_tool, "_fetch_song_url", fake_fetch_song_url)
    monkeypatch.setattr(
        request_song_tool,
        "_download_audio",
        AsyncMock(return_value=(b"audio-bytes", "audio/mpeg")),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_generate_song_selection_response",
        AsyncMock(
            return_value=(
                '{"selected_index": 2, "reason": "用户指定周杰伦，第二首是原唱专辑版本", '
                '"song_comment": "这首歌像把青春晒在操场边，风一吹就响起来。"}'
            )
        ),
    )
    monkeypatch.setattr(request_song_tool.discord, "File", _FakeDiscordFile)
    monkeypatch.setitem(request_song_tool.MUSIC_REQUEST_CONFIG, "DEFAULT_SOURCES", ["joox"])
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="晴天",
            artist="周杰伦",
            message=message,
        )
    )

    assert result["success"] is True
    assert fetched_tracks[0]["id"] == "original"
    assert result["track"]["id"] == "original"
    assert result["selection"]["selected_by"] == "llm"
    _, kwargs = message.reply.await_args
    assert "月月短评：这首歌像把青春晒在操场边，风一吹就响起来。" in kwargs["content"]


def test_request_song_single_candidate_still_uses_llm_comment(monkeypatch):
    async def fake_search_source(session, source, query, count):
        return [
            {
                "id": "asmr",
                "name": "【asmr】穿耳prprpr",
                "artist": ["未知歌手"],
                "album": "耳边小剧场",
                "source": source,
            }
        ]

    llm_mock = AsyncMock(
        return_value='{"selected_index": 1, "reason": "唯一候选，直接选择", '
        '"song_comment": "这首听起来像有人在耳边放慢呼吸，细小又贴近。"}'
    )

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(
        request_song_tool,
        "_fetch_song_url",
        AsyncMock(
            return_value={
                "url": "https://example.com/asmr.mp3",
                "br": 128,
                "size": 4317292,
            }
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_download_audio",
        AsyncMock(return_value=(b"audio-bytes", "audio/mpeg")),
    )
    monkeypatch.setattr(request_song_tool, "_generate_song_selection_response", llm_mock)
    monkeypatch.setattr(request_song_tool.discord, "File", _FakeDiscordFile)
    monkeypatch.setitem(request_song_tool.MUSIC_REQUEST_CONFIG, "DEFAULT_SOURCES", ["joox"])
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="穿耳prprpr",
            message=message,
        )
    )

    assert result["success"] is True
    assert result["selection"]["selected_by"] == "llm"
    llm_mock.assert_awaited_once()
    _, kwargs = message.reply.await_args
    assert "月月短评：这首听起来像有人在耳边放慢呼吸，细小又贴近。" in kwargs["content"]
    assert "一点心事摊在阳光下" not in kwargs["content"]


def test_request_song_resolves_bilibili_link_title_and_prioritizes_source(monkeypatch):
    search_calls = []
    bilibili_title = "在百万豪装录音棚大声听 五月天《干杯》【Hi-res】"

    async def fake_search_source(session, source, query, count):
        search_calls.append((source, query))
        if source != "bilibili":
            return []
        return [
            {
                "id": "bili-target",
                "name": bilibili_title,
                "artist": ["五月天"],
                "album": "B站视频",
                "source": source,
            }
        ]

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(
        request_song_tool,
        "_fetch_song_url",
        AsyncMock(
            return_value={
                "url": "https://example.com/bili.mp3",
                "br": 128,
                "size": 4317292,
            }
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_download_audio",
        AsyncMock(return_value=(b"audio-bytes", "audio/mpeg")),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_generate_song_selection_response",
        AsyncMock(
            return_value=(
                '{"selected_index": 1, "reason": "B站链接标题匹配", '
                '"song_comment": "这版像把现场灯光推到耳边，热烈得很干净。"}'
            )
        ),
    )
    monkeypatch.setattr(request_song_tool.discord, "File", _FakeDiscordFile)
    monkeypatch.setitem(
        request_song_tool.MUSIC_REQUEST_CONFIG,
        "DEFAULT_SOURCES",
        ["joox", "netease", "bilibili"],
    )
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    embed = type("Embed", (), {"title": bilibili_title, "url": "https://m.bilibili.com/video/BV1K8411Z7GJ"})()
    message = type("Message", (), {})()
    message.embeds = [embed]
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="https://m.bilibili.com/video/BV1K8411Z7GJ",
            message=message,
        )
    )

    assert result["success"] is True
    assert result["query"] == bilibili_title
    assert result["original_query"] == "https://m.bilibili.com/video/BV1K8411Z7GJ"
    assert result["resolved_from"] == "bilibili_embed"
    assert search_calls[0] == ("bilibili", bilibili_title)
    assert all("BV1K8411Z7GJ" not in query for _, query in search_calls)


def test_request_song_rejects_irrelevant_fallback_candidate(monkeypatch):
    async def fake_search_source(session, source, query, count):
        return [
            {
                "id": "wrong",
                "name": "體面（電影《前任3：再見前任》插曲）",
                "artist": ["于文文"],
                "album": "體面（電影《前任3：再見前任》插曲）",
                "source": source,
            }
        ]

    fetch_url_mock = AsyncMock()

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(request_song_tool, "_fetch_song_url", fetch_url_mock)
    monkeypatch.setattr(
        request_song_tool,
        "_generate_song_selection_response",
        AsyncMock(return_value=None),
    )
    monkeypatch.setitem(request_song_tool.MUSIC_REQUEST_CONFIG, "DEFAULT_SOURCES", ["joox"])
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="人鱼公主",
            message=message,
        )
    )

    assert result["success"] is False
    assert result["reason"] == "no_relevant_results"
    assert result["selection"]["selected_by"] == "heuristic_no_match"
    fetch_url_mock.assert_not_awaited()
    message.reply.assert_not_awaited()


def test_request_song_respects_llm_no_match_selection(monkeypatch):
    async def fake_search_source(session, source, query, count):
        return [
            {
                "id": "wrong",
                "name": "體面",
                "artist": ["于文文"],
                "album": "尚未界定",
                "source": source,
            }
        ]

    fetch_url_mock = AsyncMock()

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(request_song_tool, "_fetch_song_url", fetch_url_mock)
    monkeypatch.setattr(
        request_song_tool,
        "_generate_song_selection_response",
        AsyncMock(
            return_value=(
                '{"selected_index": 0, "reason": "候选都不是用户要的人鱼公主", '
                '"song_comment": ""}'
            )
        ),
    )
    monkeypatch.setitem(request_song_tool.MUSIC_REQUEST_CONFIG, "DEFAULT_SOURCES", ["joox"])
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="人鱼公主",
            send_to_channel=False,
        )
    )

    assert result["success"] is False
    assert result["reason"] == "no_relevant_results"
    assert result["selection"]["selected_by"] == "llm_no_match"
    fetch_url_mock.assert_not_awaited()


def test_request_song_retries_with_llm_refined_query_until_match(monkeypatch):
    search_calls = []

    async def fake_search_source(session, source, query, count):
        search_calls.append((source, query))
        if query == "人鱼公主" and source == "joox":
            return [
                {
                    "id": "wrong",
                    "name": "體面",
                    "artist": ["于文文"],
                    "album": "尚未界定",
                    "source": source,
                }
            ]
        if query == "人鱼公主 MV" and source == "bilibili":
            return [
                {
                    "id": "right",
                    "name": "人鱼公主",
                    "artist": ["爱你卧蚕明眸俏模样"],
                    "album": "MV",
                    "source": source,
                }
            ]
        return []

    llm_mock = AsyncMock(
        side_effect=[
            (
                '{"selected_index": 0, "reason": "joox 候选不相关", '
                '"retry_query": "人鱼公主 MV", "retry_source": "bilibili", '
                '"song_comment": ""}'
            ),
            (
                '{"selected_index": 1, "reason": "标题与用户请求匹配", '
                '"song_comment": "这首像从海面下慢慢亮起的一束光。"}'
            ),
        ]
    )

    monkeypatch.setattr(request_song_tool, "_search_source", fake_search_source)
    monkeypatch.setattr(
        request_song_tool,
        "_fetch_song_url",
        AsyncMock(
            return_value={
                "url": "https://example.com/mermaid.mp3",
                "br": 128,
                "size": 4317292,
            }
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_download_audio",
        AsyncMock(return_value=(b"audio-bytes", "audio/mpeg")),
    )
    monkeypatch.setattr(request_song_tool, "_generate_song_selection_response", llm_mock)
    monkeypatch.setattr(request_song_tool.discord, "File", _FakeDiscordFile)
    monkeypatch.setitem(request_song_tool.MUSIC_REQUEST_CONFIG, "DEFAULT_SOURCES", ["joox"])
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="人鱼公主",
            message=message,
        )
    )

    assert result["success"] is True
    assert result["track"]["id"] == "right"
    assert result["query"] == "人鱼公主 MV"
    assert result["original_query"] == "人鱼公主"
    assert result["selection"]["selected_by"] == "llm"
    assert result["selection"]["attempt"] == 2
    assert len(result["search_attempts"]) == 2
    assert ("joox", "人鱼公主") in search_calls
    assert ("bilibili", "人鱼公主 MV") in search_calls
    assert llm_mock.await_count == 2
    _, kwargs = message.reply.await_args
    assert "人鱼公主" in kwargs["content"]
    assert "月月短评：这首像从海面下慢慢亮起的一束光。" in kwargs["content"]


def test_request_song_large_remote_file_falls_back_to_link(monkeypatch):
    monkeypatch.setattr(
        request_song_tool,
        "_search_source",
        AsyncMock(
            return_value=[
                {
                    "id": "target",
                    "name": "晴天",
                    "artist": ["周杰倫"],
                    "album": "葉惠美",
                    "source": "joox",
                }
            ]
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_fetch_song_url",
        AsyncMock(
            return_value={
                "url": "https://example.com/sunny.mp3",
                "br": 999,
                "size": 30 * 1024 * 1024,
            }
        ),
    )
    monkeypatch.setattr(
        request_song_tool,
        "_generate_song_selection_response",
        AsyncMock(
            return_value=(
                '{"selected_index": 1, "reason": "唯一候选，直接选择", '
                '"song_comment": "这首歌像雨后操场边的一阵风，适合慢慢回头听。"}'
            )
        ),
    )
    download_mock = AsyncMock()
    monkeypatch.setattr(request_song_tool, "_download_audio", download_mock)
    monkeypatch.setitem(
        request_song_tool.MUSIC_REQUEST_CONFIG,
        "MAX_DOWNLOAD_BYTES",
        24 * 1024 * 1024,
    )
    request_song_tool._REQUEST_TIMESTAMPS.clear()

    message = type("Message", (), {})()
    message.reply = AsyncMock()

    result = asyncio.run(
        request_song_tool.request_song(
            song_name="晴天",
            artist="周杰伦",
            br=999,
            message=message,
        )
    )

    assert result["success"] is True
    assert result["sent_to_channel"] is True
    assert result["sent_file"] is False
    assert result["audio_url"] == "https://example.com/sunny.mp3"
    download_mock.assert_not_awaited()
    message.reply.assert_awaited_once()
    _, kwargs = message.reply.await_args
    assert "文件太大" in kwargs["content"]
    assert "https://example.com/sunny.mp3" in kwargs["content"]


def test_tool_loader_discovers_request_song():
    _, tool_map = load_tools_from_directory("src/chat/features/tools/functions")

    assert "request_song" in tool_map


def test_request_song_prompt_requires_intent_and_lyric_analysis():
    doc = inspect.getdoc(request_song_tool.request_song) or ""

    assert "调用前先仔细分析用户真实点歌意图" in doc
    assert "只提供一句歌词" in doc
    assert "歌词片段" in doc
    assert "不要把闲聊解释当作歌名" in doc
