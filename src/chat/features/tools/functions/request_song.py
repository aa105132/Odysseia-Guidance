# -*- coding: utf-8 -*-

"""
点歌工具

通过 GD Studio 音乐 API 搜索歌曲、获取音频链接，并把可发送大小内的音频
作为 Discord 附件发到当前频道。
"""

import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp
import discord

from src.chat.config.chat_config import MUSIC_REQUEST_CONFIG
from src.chat.features.tools.tool_metadata import tool_metadata

log = logging.getLogger(__name__)

_REQUEST_TIMESTAMPS: List[float] = []
_ALLOWED_BR = {128, 192, 320, 740, 999}
_TRADITIONAL_TO_SIMPLIFIED = str.maketrans(
    {
        "倫": "伦",
        "傑": "杰",
        "杰": "杰",
        "週": "周",
        "葉": "叶",
        "陽": "阳",
        "強": "强",
        "廣": "广",
        "語": "语",
        "鋼": "钢",
        "樂": "乐",
        "專": "专",
        "輯": "辑",
        "臺": "台",
        "灣": "湾",
    }
)


class _AudioTooLargeError(RuntimeError):
    pass


def _normalize_text(value: Any) -> str:
    text = str(value or "").translate(_TRADITIONAL_TO_SIMPLIFIED).lower()
    return re.sub(r"[\s\-_·・,，.。:：!！?？()\[\]【】《》<>\"'“”‘’/\\]+", "", text)


def _safe_filename_part(value: Any, fallback: str = "song") -> str:
    text = str(value or "").strip() or fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return (text or fallback)[:80]


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default


def _normalize_sources(source: Optional[str] = None) -> List[str]:
    if source:
        raw_values = str(source).replace("，", ",").split(",")
    else:
        configured = MUSIC_REQUEST_CONFIG.get("DEFAULT_SOURCES", [])
        if isinstance(configured, str):
            raw_values = configured.replace("，", ",").split(",")
        else:
            raw_values = list(configured or [])

    sources: List[str] = []
    for item in raw_values:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in sources:
            sources.append(normalized)
    return sources or ["joox", "netease", "bilibili"]


def _normalize_track(raw_track: Any, fallback_source: str) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_track, dict):
        return None

    track_id = str(raw_track.get("id") or raw_track.get("track_id") or "").strip()
    name = str(raw_track.get("name") or "").strip()
    if not track_id or not name:
        return None

    artists = raw_track.get("artist") or raw_track.get("artists") or []
    if isinstance(artists, str):
        artist_list = [artists]
    elif isinstance(artists, list):
        artist_list = [str(item).strip() for item in artists if str(item).strip()]
    else:
        artist_list = []

    return {
        "id": track_id,
        "name": name,
        "artist": artist_list,
        "album": str(raw_track.get("album") or "").strip(),
        "pic_id": str(raw_track.get("pic_id") or "").strip(),
        "lyric_id": str(raw_track.get("lyric_id") or "").strip(),
        "source": str(raw_track.get("source") or fallback_source).strip() or fallback_source,
    }


def _track_artist_text(track: Dict[str, Any]) -> str:
    artists = track.get("artist") or []
    if isinstance(artists, list):
        return "/".join(str(item) for item in artists if str(item).strip())
    return str(artists or "").strip()


def _score_track(track: Dict[str, Any], *, query: str, artist: Optional[str]) -> int:
    query_norm = _normalize_text(query)
    artist_norm = _normalize_text(artist)
    name_norm = _normalize_text(track.get("name"))
    artists_norm = _normalize_text(_track_artist_text(track))
    album_norm = _normalize_text(track.get("album"))
    score = 0

    if name_norm and name_norm in query_norm:
        score += 60
    if query_norm and query_norm in name_norm:
        score += 45
    if artist_norm and artists_norm:
        if artist_norm in artists_norm or artists_norm in artist_norm:
            score += 55

    for token in re.split(r"[\s,，/\\]+", str(query or "")):
        token_norm = _normalize_text(token)
        if not token_norm:
            continue
        if token_norm and token_norm in name_norm:
            score += 12
        if token_norm and token_norm in artists_norm:
            score += 10
        if token_norm and token_norm in album_norm:
            score += 3

    raw_name = str(track.get("name") or "").lower()
    raw_album = str(track.get("album") or "").lower()
    query_lower = str(query or "").lower()
    if "live" not in query_lower and ("live" in raw_name or "演唱會" in raw_album):
        score -= 8
    if any(word in raw_name for word in ("伴奏", "纯音乐", "純音樂", "钢琴", "鋼琴")):
        score -= 6
    return score


def _pick_best_track(
    tracks: List[Dict[str, Any]],
    *,
    query: str,
    artist: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    if not tracks:
        return None
    return max(tracks, key=lambda item: _score_track(item, query=query, artist=artist))


def _prepare_llm_candidates(
    tracks: List[Dict[str, Any]],
    *,
    query: str,
    artist: Optional[str],
    limit: int = 12,
) -> List[Dict[str, Any]]:
    """按启发式分数预筛候选数量，但保留 API 返回顺序给内部 LLM 分析。"""
    scored = [
        (
            _score_track(track, query=query, artist=artist),
            index,
            track,
        )
        for index, track in enumerate(tracks)
    ]
    scored.sort(key=lambda item: (-item[0], item[1]))
    kept_indexes = {index for _, index, _ in scored[: max(1, limit)]}
    return [
        track
        for index, track in enumerate(tracks)
        if index in kept_indexes
    ]


def _build_song_selection_prompt(
    *,
    query: str,
    artist: Optional[str],
    candidates: List[Dict[str, Any]],
    song_comment: Optional[str] = None,
) -> str:
    candidate_lines = []
    for index, track in enumerate(candidates, 1):
        candidate_lines.append(
            (
                f"{index}. 歌名：{track.get('name') or '未知'} | "
                f"歌手：{_track_artist_text(track) or '未知'} | "
                f"专辑：{track.get('album') or '未知'} | "
                f"来源：{track.get('source') or '未知'} | "
                f"id：{track.get('id') or ''}"
            )
        )

    existing_comment = str(song_comment or "").strip()
    comment_instruction = (
        f"用户/上一轮已经给过短评草稿：{existing_comment}\n"
        "你可以保留或润色它，但必须仍输出 song_comment。"
        if existing_comment
        else "请顺便写一句 15-60 个汉字的 song_comment，像月月对这首歌的短短感悟。"
    )

    return (
        "你是月月的点歌选择器。请只根据用户点歌请求和 API 候选列表，选出最应该播放的一首。\n"
        "规则：\n"
        "1. 用户指定歌手时，歌手匹配优先级最高。\n"
        "2. 优先选择正式录音室/专辑版本；除非用户明确要 live、翻唱、伴奏、纯音乐、教程或 MV，否则避开这些版本。\n"
        "3. 不要因为候选排在前面就选它，要比较歌名、歌手、专辑和版本信息。\n"
        "4. 如果多个候选都合理，选最贴近用户原话的一项。\n"
        "5. 即使候选只有一首，也要根据曲目信息输出自然的 song_comment，不要复读模板。\n"
        f"{comment_instruction}\n\n"
        f"用户请求：{query}\n"
        f"指定歌手：{artist or '未指定'}\n\n"
        "候选列表：\n"
        + "\n".join(candidate_lines)
        + "\n\n"
        "严格只输出 JSON，不要 markdown，不要解释。格式：\n"
        '{"selected_index": 1, "reason": "选择理由", "song_comment": "月月短评"}'
    )


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    raw_text = str(text or "").strip()
    if not raw_text:
        return None

    raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text, flags=re.IGNORECASE)
    raw_text = re.sub(r"\s*```$", "", raw_text)

    candidates = [raw_text]
    match = re.search(r"\{.*\}", raw_text, flags=re.DOTALL)
    if match:
        candidates.append(match.group(0))

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


async def _generate_song_selection_response(prompt: str) -> Optional[str]:
    """调用无工具的轻量 LLM，让月月在下载前分析候选并选择曲目。"""
    from src.chat.services.gemini_service import gemini_service

    return await gemini_service.generate_simple_response(
        prompt=prompt,
        generation_config={
            "temperature": 0.2,
            "max_output_tokens": 512,
        },
        return_error_text=False,
    )


async def _select_track_with_llm(
    tracks: List[Dict[str, Any]],
    *,
    query: str,
    artist: Optional[str],
    song_comment: Optional[str] = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Dict[str, Any]]:
    """让内部 LLM 从搜索候选中选曲；失败时回退启发式选择。"""
    fallback_track = _pick_best_track(tracks, query=query, artist=artist)
    if not tracks:
        return None, song_comment, {"selected_by": "none", "reason": "no_candidates"}

    candidates = _prepare_llm_candidates(tracks, query=query, artist=artist)
    if len(candidates) == 1 and str(song_comment or "").strip():
        return candidates[0], song_comment, {
            "selected_by": "single_candidate_with_comment",
            "candidate_count": 1,
            "selected_index": 1,
            "reason": "只有一个候选结果，且已有短评草稿。",
        }

    prompt = _build_song_selection_prompt(
        query=query,
        artist=artist,
        candidates=candidates,
        song_comment=song_comment,
    )

    try:
        raw_response = await _generate_song_selection_response(prompt)
        parsed = _extract_json_object(raw_response or "")
    except Exception as exc:
        log.warning("点歌候选 LLM 分析失败，回退启发式选择: %s", exc, exc_info=True)
        parsed = None
        raw_response = ""

    if isinstance(parsed, dict):
        selected_index = _coerce_int(
            parsed.get("selected_index") or parsed.get("index") or parsed.get("choice"),
            0,
        )
        if 1 <= selected_index <= len(candidates):
            selected_track = candidates[selected_index - 1]
            selected_comment = str(
                parsed.get("song_comment")
                or parsed.get("comment")
                or song_comment
                or ""
            ).strip()
            return selected_track, selected_comment, {
                "selected_by": "llm",
                "candidate_count": len(candidates),
                "selected_index": selected_index,
                "reason": str(parsed.get("reason") or "").strip(),
            }

    return fallback_track, song_comment, {
        "selected_by": "heuristic_fallback",
        "candidate_count": len(candidates),
        "reason": "LLM 未返回有效选择，已回退启发式评分。",
        "raw_response": str(raw_response or "")[:300],
    }


def _infer_audio_extension(mime_type: str, audio_url: str) -> str:
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    if mime in {"audio/mpeg", "audio/mp3"}:
        return "mp3"
    if mime in {"audio/ogg", "audio/opus"}:
        return "ogg"
    if mime in {"audio/wav", "audio/x-wav"}:
        return "wav"
    if mime in {"audio/flac"}:
        return "flac"
    if mime in {"audio/aac"}:
        return "aac"
    if mime in {"audio/mp4", "audio/m4a", "audio/x-m4a"}:
        return "m4a"

    path = urlparse(str(audio_url or "")).path
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return ext if ext in {"mp3", "ogg", "opus", "wav", "flac", "aac", "m4a"} else "mp3"


def _build_audio_filename(
    track: Dict[str, Any],
    *,
    extension: str,
) -> str:
    title = _safe_filename_part(track.get("name"), "song")
    artists = _safe_filename_part(_track_artist_text(track), "unknown")
    return f"{title}-{artists}.{extension or 'mp3'}"


def _format_track_label(track: Dict[str, Any]) -> str:
    artists = _track_artist_text(track) or "未知歌手"
    album = str(track.get("album") or "").strip()
    label = f"{track.get('name', '未知歌曲')} - {artists}"
    return f"{label}（{album}）" if album else label


def _build_default_song_comment(track: Dict[str, Any]) -> str:
    title = str(track.get("name") or "这首歌").strip() or "这首歌"
    return f"{title}听起来像把一点心事摊在阳光下，轻轻晒干。"


def _normalize_song_comment(
    song_comment: Optional[str],
    track: Dict[str, Any],
) -> str:
    comment = str(song_comment or "").strip()
    comment = re.sub(r"\s+", " ", comment)
    comment = comment.removeprefix("月月短评：").strip()
    if not comment:
        comment = _build_default_song_comment(track)
    return comment[:160].rstrip()


def _format_song_comment_line(song_comment: str) -> str:
    comment = str(song_comment or "").strip()
    return f"\n月月短评：{comment}" if comment else ""


def _register_rate_limit() -> Optional[Dict[str, Any]]:
    limit = _coerce_int(MUSIC_REQUEST_CONFIG.get("RATE_LIMIT_PER_5_MINUTES"), 45)
    if limit <= 0:
        return None

    now = time.monotonic()
    window_start = now - 300
    del _REQUEST_TIMESTAMPS[:]
    _REQUEST_TIMESTAMPS.extend(ts for ts in _REQUEST_TIMESTAMPS if ts >= window_start)
    if len(_REQUEST_TIMESTAMPS) >= limit:
        return {
            "error": True,
            "reason": "rate_limited",
            "hint": "点歌接口调用太频繁了，请稍后再试。",
        }
    _REQUEST_TIMESTAMPS.append(now)
    return None


async def _fetch_json(
    session: aiohttp.ClientSession,
    params: Dict[str, Any],
) -> Any:
    api_url = str(MUSIC_REQUEST_CONFIG.get("API_URL") or "").strip()
    if not api_url:
        raise RuntimeError("MUSIC_REQUEST_API_URL 未配置")

    async with session.get(api_url, params=params) as response:
        response_text = await response.text()
        if response.status != 200:
            raise RuntimeError(f"音乐 API 返回 HTTP {response.status}: {response_text[:200]}")
        try:
            return json.loads(response_text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"音乐 API 返回非 JSON 内容: {response_text[:120]}") from exc


async def _search_source(
    session: aiohttp.ClientSession,
    source: str,
    query: str,
    count: int,
) -> List[Dict[str, Any]]:
    payload = await _fetch_json(
        session,
        {
            "types": "search",
            "source": source,
            "name": query,
            "count": count,
            "pages": 1,
        },
    )

    if isinstance(payload, dict):
        candidates = payload.get("data") or payload.get("results") or payload.get("songs") or []
    else:
        candidates = payload
    if not isinstance(candidates, list):
        return []

    tracks: List[Dict[str, Any]] = []
    for item in candidates:
        normalized = _normalize_track(item, source)
        if normalized:
            tracks.append(normalized)
    return tracks


async def _fetch_song_url(
    session: aiohttp.ClientSession,
    track: Dict[str, Any],
    br: int,
) -> Dict[str, Any]:
    payload = await _fetch_json(
        session,
        {
            "types": "url",
            "source": track.get("source") or "joox",
            "id": track.get("id"),
            "br": br,
        },
    )
    if not isinstance(payload, dict):
        return {}
    return payload


async def _download_audio(
    session: aiohttp.ClientSession,
    audio_url: str,
    max_bytes: int,
) -> Tuple[bytes, str]:
    async with session.get(
        audio_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            **({"Referer": "https://www.bilibili.com"} if "bilivideo" in audio_url or "bilibili" in audio_url else {}),
        },
    ) as response:
        if response.status != 200:
            raise RuntimeError(f"音频下载返回 HTTP {response.status}")

        content_length = _coerce_int(response.headers.get("Content-Length"))
        if content_length > max_bytes:
            raise _AudioTooLargeError("音频文件超过发送上限")

        mime_type = response.headers.get("Content-Type", "audio/mpeg")
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(64 * 1024):
            if not chunk:
                continue
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise _AudioTooLargeError("音频文件超过发送上限")
        return bytes(chunks), str(mime_type or "audio/mpeg")


async def _send_song_message(
    *,
    message: Optional[discord.Message],
    channel: Optional[discord.abc.Messageable],
    content: str,
    audio_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
) -> bool:
    if message is None and channel is None:
        return False

    kwargs: Dict[str, Any] = {"content": content}
    if audio_bytes is not None and filename:
        kwargs["file"] = discord.File(io.BytesIO(audio_bytes), filename=filename)

    if message is not None:
        await message.reply(**kwargs, mention_author=False)
    elif channel is not None:
        await channel.send(**kwargs)
    return True


def _build_sent_content(
    track: Dict[str, Any],
    *,
    br: int,
    source: str,
    song_comment: str,
) -> str:
    return (
        f"点好啦：{_format_track_label(track)}\n"
        f"来源：GD音乐台 / {source}，音质：{br}kbps。"
        f"{_format_song_comment_line(song_comment)}"
    )


def _build_link_fallback_content(
    track: Dict[str, Any],
    *,
    br: int,
    source: str,
    audio_url: str,
    reason: str,
    song_comment: str,
) -> str:
    return (
        f"找到了：{_format_track_label(track)}\n"
        f"来源：GD音乐台 / {source}，音质：{br}kbps。\n"
        f"月月短评：{song_comment}\n"
        f"{reason}，先给你播放链接：{audio_url}"
    )


@tool_metadata(
    name="点歌",
    description="分析用户点歌意图，按歌名、歌手或歌词片段搜索音乐，获取音频文件并发送到当前频道",
    emoji="🎵",
    category="娱乐",
)
async def request_song(
    song_name: str,
    artist: Optional[str] = None,
    source: Optional[str] = None,
    br: Optional[int] = None,
    song_comment: Optional[str] = None,
    send_to_channel: bool = True,
    **kwargs,
) -> dict:
    """
    点歌并把音频发送到当前频道。

    使用场景：
    - 用户明确说“点歌”“放一首”“想听”“来首”“播放某首歌”等需求时调用。
    - 调用前先仔细分析用户真实点歌意图：判断用户给的是歌名、歌手、歌词片段，
      还是 live、翻唱、伴奏、纯音乐、MV 等版本偏好。
    - 用户只提供一句歌词或歌词片段时，不要把歌词直接当作随便的歌名；
      应把关键歌词和上下文整理成最可能命中的搜索关键词。
    - 不要把闲聊解释当作歌名，也不要把情绪描述、短评请求、音乐知识问答误当成点歌。
    - song_name 写用户想听的歌名、完整关键词或关键歌词片段；如果用户指定歌手，把歌手放到 artist。
    - 默认会依次搜索配置中的音乐源，再让内部轻量 LLM 分析候选并选中最匹配曲目。
    - 发送歌曲时会附带一句“月月短评”；song_comment 可作为短评草稿，内部 LLM 可保留或润色。
    - 如果音频文件不超过发送上限，会直接作为 Discord 附件发出去，并返回 skip_ai_response=True。
    - 如果文件过大或下载失败，会把播放链接发到频道。
    - 不要用于普通音乐知识问答；只有用户想“听歌/点歌/播放”时调用。

    Args:
        song_name: 歌名、搜索关键词或用户只给歌词时的关键歌词片段，
            例如“晴天”“周杰伦 晴天”“从前从前有个人爱你很久”。
            调用前必须先从用户原话中提炼最适合搜索的关键词。
        artist: 可选歌手名，例如“周杰伦”。只有用户明确指定或高度确定时填写；
            不确定不要硬猜，可留空交给候选分析。
        source: 可选音乐源，支持 joox、netease、bilibili 等；留空使用默认兜底源。
        br: 可选音质，支持 128、192、320、740、999。默认 128，避免 Discord 附件过大。
        song_comment: 可选短评草稿；内部候选分析 LLM 会输出最终月月短评，建议 15-60 个汉字。
        send_to_channel: 是否发送到当前频道。默认 true。

    Returns:
        成功发送文件或链接时返回 skip_ai_response=True，避免月月再补发重复文本。
    """
    query = str(song_name or "").strip()
    artist_text = str(artist or "").strip()
    if artist_text and artist_text not in query:
        query = f"{artist_text} {query}".strip()
    if not query:
        return {
            "error": True,
            "reason": "empty_query",
            "hint": "缺少歌曲关键词，请让用户说清楚想听哪首歌。",
        }

    rate_limit_error = _register_rate_limit()
    if rate_limit_error:
        return rate_limit_error

    requested_br = _coerce_int(br, _coerce_int(MUSIC_REQUEST_CONFIG.get("DEFAULT_BR"), 128))
    if requested_br not in _ALLOWED_BR:
        requested_br = _coerce_int(MUSIC_REQUEST_CONFIG.get("DEFAULT_BR"), 128)
    if requested_br not in _ALLOWED_BR:
        requested_br = 128

    search_count = max(1, min(_coerce_int(MUSIC_REQUEST_CONFIG.get("SEARCH_COUNT"), 8), 30))
    timeout_seconds = max(5, _coerce_int(MUSIC_REQUEST_CONFIG.get("TIMEOUT_SECONDS"), 30))
    max_download_bytes = max(
        1024 * 1024,
        _coerce_int(MUSIC_REQUEST_CONFIG.get("MAX_DOWNLOAD_BYTES"), 24 * 1024 * 1024),
    )
    sources = _normalize_sources(source)
    message = kwargs.get("message")
    channel = kwargs.get("channel") or (getattr(message, "channel", None) if message else None)

    try:
        timeout = aiohttp.ClientTimeout(total=timeout_seconds, connect=10)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            all_tracks: List[Dict[str, Any]] = []
            source_result_counts: Dict[str, int] = {}
            for source_name in sources:
                try:
                    tracks = await _search_source(
                        session,
                        source_name,
                        query,
                        search_count,
                    )
                except Exception as exc:
                    log.warning("点歌搜索源 %s 失败: %s", source_name, exc)
                    source_result_counts[source_name] = 0
                    continue
                source_result_counts[source_name] = len(tracks)
                all_tracks.extend(tracks)

            selected, llm_song_comment, selection_info = await _select_track_with_llm(
                all_tracks,
                query=query,
                artist=artist_text,
                song_comment=song_comment,
            )
            if not selected:
                return {
                    "success": False,
                    "reason": "no_results",
                    "query": query,
                    "sources": sources,
                    "source_result_counts": source_result_counts,
                    "hint": "没有搜到可点播的歌曲，请让用户换个歌名或加上歌手名。",
                }

            final_song_comment = _normalize_song_comment(llm_song_comment, selected)
            url_info = await _fetch_song_url(session, selected, requested_br)
            audio_url = str(url_info.get("url") or "").strip()
            actual_br = _coerce_int(url_info.get("br"), requested_br)
            remote_size = _coerce_int(url_info.get("size"))
            source_name = str(selected.get("source") or "").strip() or sources[0]

            base_result: Dict[str, Any] = {
                "success": True,
                "query": query,
                "track": selected,
                "source": source_name,
                "br": actual_br,
                "remote_size": remote_size,
                "audio_url": audio_url,
                "song_comment": final_song_comment,
                "selection": selection_info,
                "source_result_counts": source_result_counts,
            }

            if not audio_url:
                return {
                    **base_result,
                    "success": False,
                    "reason": "no_audio_url",
                    "hint": "搜到了歌曲，但音乐 API 没有返回可播放音频链接。",
                }

            if not send_to_channel or (message is None and channel is None):
                return {
                    **base_result,
                    "sent_to_channel": False,
                    "sent_file": False,
                    "skip_ai_response": False,
                    "message": "已获取音频链接，但当前没有可发送的频道上下文。",
                }

            if remote_size and remote_size > max_download_bytes:
                content = _build_link_fallback_content(
                    selected,
                    br=actual_br,
                    source=source_name,
                    audio_url=audio_url,
                    reason="文件太大，不能直接当附件发送",
                    song_comment=final_song_comment,
                )
                sent = await _send_song_message(
                    message=message,
                    channel=channel,
                    content=content,
                )
                return {
                    **base_result,
                    "sent_to_channel": sent,
                    "sent_file": False,
                    "skip_ai_response": sent,
                    "message": "音频链接已发送；远端文件超过 Discord 附件大小上限。",
                }

            try:
                audio_bytes, mime_type = await _download_audio(
                    session,
                    audio_url,
                    max_download_bytes,
                )
            except _AudioTooLargeError:
                content = _build_link_fallback_content(
                    selected,
                    br=actual_br,
                    source=source_name,
                    audio_url=audio_url,
                    reason="文件太大，不能直接当附件发送",
                    song_comment=final_song_comment,
                )
                sent = await _send_song_message(
                    message=message,
                    channel=channel,
                    content=content,
                )
                return {
                    **base_result,
                    "sent_to_channel": sent,
                    "sent_file": False,
                    "skip_ai_response": sent,
                    "message": "音频链接已发送；下载时发现文件超过发送上限。",
                }
            except Exception as exc:
                log.warning("点歌音频下载失败，回退链接: %s", exc, exc_info=True)
                content = _build_link_fallback_content(
                    selected,
                    br=actual_br,
                    source=source_name,
                    audio_url=audio_url,
                    reason="音频下载失败",
                    song_comment=final_song_comment,
                )
                sent = await _send_song_message(
                    message=message,
                    channel=channel,
                    content=content,
                )
                return {
                    **base_result,
                    "sent_to_channel": sent,
                    "sent_file": False,
                    "skip_ai_response": sent,
                    "message": "音频下载失败，已改发播放链接。",
                }

            extension = _infer_audio_extension(mime_type, audio_url)
            filename = _build_audio_filename(selected, extension=extension)
            content = _build_sent_content(
                selected,
                br=actual_br,
                source=source_name,
                song_comment=final_song_comment,
            )
            sent = await _send_song_message(
                message=message,
                channel=channel,
                content=content,
                audio_bytes=audio_bytes,
                filename=filename,
            )

            return {
                **base_result,
                "sent_to_channel": sent,
                "sent_file": sent,
                "skip_ai_response": sent,
                "audio_format": extension,
                "downloaded_size": len(audio_bytes),
                "filename": filename,
                "message": "歌曲音频已发送到频道。",
            }
    except Exception as exc:
        log.error("点歌工具执行失败: %s", exc, exc_info=True)
        return {
            "error": True,
            "reason": "request_failed",
            "query": query,
            "hint": f"点歌接口请求失败：{exc}",
        }
