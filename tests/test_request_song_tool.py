import asyncio
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
    channel.send.assert_not_called()


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
