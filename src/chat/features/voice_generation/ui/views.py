# -*- coding: utf-8 -*-

"""
语音生成交互面板
- 支持编辑文本与参数
- 支持试听（仅自己可见）
- 支持发送到当前频道
- 管理员可选保存音色（并可绑定复刻音色 APP_ID）
"""

import base64
import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import discord

from src import config
from src.chat.config import chat_config
from src.chat.features.voice_generation.services.voice_service import (
    VoiceResult,
    voice_service,
)
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)


def _is_admin_or_dev(user: discord.abc.User) -> bool:
    """检查是否为管理员或开发者。"""
    if user.id in config.DEVELOPER_USER_IDS:
        return True

    if isinstance(user, discord.Member):
        role_ids = {role.id for role in user.roles}
        return not role_ids.isdisjoint(config.ADMIN_ROLE_IDS)

    return False


def _strip_emoji_placeholders(text: str) -> str:
    """移除 <微笑> 这类表情占位符，避免 TTS 朗读占位文本。"""
    if not text:
        return ""
    cleaned = re.sub(r"<[^<>\r\n]{1,24}>", "", text)
    cleaned = re.sub(r"<[^<>\r\n]{1,24}>", "", cleaned)
    cleaned = re.sub(r"<[^<>\r\n]{1,24}>", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def _parse_optional_float(
    raw: str,
    *,
    field_name: str,
    minimum: float,
    maximum: float,
) -> Optional[float]:
    value = (raw or "").strip()
    if not value:
        return None
    try:
        parsed = float(value)
    except ValueError as exc:
        raise ValueError(f"{field_name} 必须是数字。") from exc

    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间。")
    return parsed


def _parse_emotion_control(raw: str) -> Tuple[Optional[bool], Optional[float]]:
    """
    解析“情感开关/强度”输入：
    - 留空：None, None
    - 仅布尔：true / false
    - 仅强度：4.0
    - 布尔+强度：true,4.0
    """
    text = (raw or "").strip()
    if not text:
        return None, None

    tokens = [seg.strip() for seg in re.split(r"[,\s，]+", text) if seg.strip()]
    if not tokens:
        return None, None

    bool_map = {
        "true": True,
        "1": True,
        "yes": True,
        "on": True,
        "false": False,
        "0": False,
        "no": False,
        "off": False,
    }

    enable_emotion: Optional[bool] = None
    emotion_scale: Optional[float] = None

    first = tokens[0].lower()
    if first in bool_map:
        enable_emotion = bool_map[first]
        if len(tokens) >= 2:
            emotion_scale = _parse_optional_float(
                tokens[1],
                field_name="emotion_scale",
                minimum=1.0,
                maximum=5.0,
            )
        return enable_emotion, emotion_scale

    # 尝试按“仅强度”解析
    emotion_scale = _parse_optional_float(
        tokens[0],
        field_name="emotion_scale",
        minimum=1.0,
        maximum=5.0,
    )
    return None, emotion_scale


def _format_optional_float(value: Optional[float]) -> str:
    return "自动" if value is None else f"{value:.2f}"


def _format_optional_bool(value: Optional[bool]) -> str:
    if value is None:
        return "自动"
    return "true" if value else "false"


def _truncate_text_for_select(text: str, max_length: int = 100) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    if len(text) <= max_length:
        return text
    return text[: max_length - 1] + "…"


def _split_text_chunks(text: str, chunk_size: int = 900) -> List[str]:
    content = (text or "").strip()
    if not content:
        return ["（空）"]

    normalized_chunk_size = max(100, min(1000, int(chunk_size)))
    chunks: List[str] = []
    start = 0
    while start < len(content):
        chunks.append(content[start : start + normalized_chunk_size])
        start += normalized_chunk_size
    return chunks


def _collect_saved_voice_entries() -> List[Tuple[str, str]]:
    default_voice = str(chat_config.VOICE_CONFIG.get("VOICE_TYPE", "")).strip()
    available_voice_types = [
        str(item).strip()
        for item in (chat_config.VOICE_CONFIG.get("AVAILABLE_VOICE_TYPES") or [])
        if str(item).strip()
    ]
    raw_hints = chat_config.VOICE_CONFIG.get("VOICE_TYPE_HINTS") or {}
    voice_type_hints = (
        {
            str(key).strip(): str(value).strip()
            for key, value in raw_hints.items()
            if str(key).strip()
        }
        if isinstance(raw_hints, dict)
        else {}
    )

    merged_entries: List[Tuple[str, str]] = []
    seen: set[str] = set()

    def _push(voice: str, hint: str) -> None:
        normalized_voice = str(voice or "").strip()
        if not normalized_voice or normalized_voice in seen:
            return
        seen.add(normalized_voice)
        merged_entries.append((normalized_voice, str(hint or "").strip()))

    if default_voice:
        _push(default_voice, voice_type_hints.get(default_voice, ""))

    for voice in available_voice_types:
        _push(voice, voice_type_hints.get(voice, ""))

    for voice, hint in voice_type_hints.items():
        _push(voice, hint)

    return merged_entries


def _estimate_ogg_opus_duration_seconds(audio_bytes: bytes) -> Optional[float]:
    """粗略估算 OGG/Opus 时长（秒）。"""
    if not audio_bytes or len(audio_bytes) < 27:
        return None

    try:
        offset = 0
        max_granule = -1

        while offset + 27 <= len(audio_bytes):
            if audio_bytes[offset : offset + 4] != b"OggS":
                break

            page_segments = audio_bytes[offset + 26]
            seg_table_start = offset + 27
            seg_table_end = seg_table_start + page_segments
            if seg_table_end > len(audio_bytes):
                break

            segment_table = audio_bytes[seg_table_start:seg_table_end]
            body_size = sum(segment_table)
            page_end = seg_table_end + body_size
            if page_end > len(audio_bytes):
                break

            granule = int.from_bytes(
                audio_bytes[offset + 6 : offset + 14], "little", signed=False
            )
            if granule > max_granule:
                max_granule = granule

            offset = page_end

        if max_granule <= 0:
            return None

        duration = max_granule / 48000.0
        return max(0.1, min(60 * 60, float(duration)))
    except Exception:
        return None


def _build_voice_waveform_base64(audio_bytes: bytes, points: int = 64) -> str:
    """生成 Discord 原生语音消息需要的 waveform（base64）。"""
    points = max(16, min(256, int(points)))
    if not audio_bytes:
        return base64.b64encode(bytes([128] * points)).decode("ascii")

    chunk_size = max(1, len(audio_bytes) // points)
    amplitudes: List[int] = []
    for idx in range(points):
        start = idx * chunk_size
        end = (
            len(audio_bytes)
            if idx == points - 1
            else min(len(audio_bytes), (idx + 1) * chunk_size)
        )
        chunk = audio_bytes[start:end]
        if not chunk:
            amplitudes.append(0)
            continue
        amplitudes.append(int(sum(chunk) / len(chunk)) & 0xFF)

    return base64.b64encode(bytes(amplitudes)).decode("ascii")


class _NativeVoiceMessageFile(discord.File):
    """带 voice metadata 的文件对象，用于 Discord 原生语音消息样式。"""

    def __init__(
        self,
        fp,
        *,
        filename: str,
        duration_secs: float,
        waveform: str,
    ):
        super().__init__(fp, filename=filename)
        self._duration_secs = max(0.1, float(duration_secs))
        self._waveform = waveform

    def to_dict(self, index: int) -> dict:
        payload = super().to_dict(index)
        payload["duration_secs"] = round(self._duration_secs, 3)
        payload["waveform"] = self._waveform
        return payload


async def _send_native_voice_message(
    channel: discord.abc.Messageable,
    *,
    audio_bytes: bytes,
    filename: str,
    duration_secs: float,
) -> None:
    """
    走底层 HTTP 参数发送“原生语音消息”：
    - 设置 MessageFlags.is_voice_message
    - 在附件里附带 duration_secs + waveform
    """
    from discord.http import handle_message_parameters
    from discord.message import MessageFlags

    state = getattr(channel, "_state", None)
    channel_id = getattr(channel, "id", None)
    if state is None or channel_id is None:
        raise RuntimeError("channel 不支持原生语音消息发送（缺少 _state/id）")

    waveform = _build_voice_waveform_base64(audio_bytes)
    voice_file = _NativeVoiceMessageFile(
        io.BytesIO(audio_bytes),
        filename=filename,
        duration_secs=duration_secs,
        waveform=waveform,
    )

    flags = MessageFlags._from_value(8192)

    with handle_message_parameters(
        content=None,
        attachments=[voice_file],
        flags=flags,
        allowed_mentions=state.allowed_mentions,
    ) as params:
        await state.http.send_message(channel_id, params=params)


@dataclass
class VoiceGenerationSession:
    text: str = ""
    voice_type: Optional[str] = None
    speed_ratio: Optional[float] = None
    pitch_ratio: Optional[float] = None
    emotion: Optional[str] = None
    enable_emotion: Optional[bool] = None
    emotion_scale: Optional[float] = None
    max_text_length: int = 500


class VoiceTextModal(discord.ui.Modal, title="编辑语音文本"):
    def __init__(self, parent_view: "VoiceGenerationPanelView"):
        super().__init__(timeout=300)
        self.parent_view = parent_view

        configured_max_text_length = max(20, int(parent_view.session.max_text_length or 500))
        # Discord Modal TextInput 最大长度为 4000，超过时按 UI 能力上限收敛
        modal_max_length = min(configured_max_text_length, 4000)
        placeholder_suffix = (
            f"（系统上限 {configured_max_text_length} 字）"
            if configured_max_text_length <= 4000
            else f"（系统上限 {configured_max_text_length} 字，单次编辑最多 4000 字）"
        )

        self.text_input = discord.ui.TextInput(
            label="要朗读的文本",
            placeholder=f"请输入要合成语音的文本{placeholder_suffix}",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=modal_max_length,
            default=(parent_view.session.text or "")[:modal_max_length],
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        max_text_length = max(20, int(self.parent_view.session.max_text_length or 500))
        text_value = (self.text_input.value or "").strip()

        if len(text_value) > max_text_length:
            await interaction.response.send_message(
                f"文本过长：当前最多 {max_text_length} 字。",
                ephemeral=True,
            )
            return

        self.parent_view.session.text = text_value
        await interaction.response.send_message("文本已更新。", ephemeral=True)
        await self.parent_view.refresh_panel()


class VoiceParamsModal(discord.ui.Modal, title="编辑语音参数"):
    def __init__(self, parent_view: "VoiceGenerationPanelView"):
        super().__init__(timeout=300)
        self.parent_view = parent_view
        session = parent_view.session

        self.voice_type_input = discord.ui.TextInput(
            label="音色 ID（留空=默认）",
            placeholder="例如: zh_female_wanwanxiaohe_moon_bigtts 或 S_xxx",
            required=False,
            max_length=120,
            default=session.voice_type or "",
        )
        self.add_item(self.voice_type_input)

        self.speed_ratio_input = discord.ui.TextInput(
            label="语速 speed_ratio（0.2~3.0）",
            placeholder="留空=自动",
            required=False,
            max_length=16,
            default="" if session.speed_ratio is None else str(session.speed_ratio),
        )
        self.add_item(self.speed_ratio_input)

        self.pitch_ratio_input = discord.ui.TextInput(
            label="音调 pitch_ratio（0.1~3.0）",
            placeholder="留空=自动",
            required=False,
            max_length=16,
            default="" if session.pitch_ratio is None else str(session.pitch_ratio),
        )
        self.add_item(self.pitch_ratio_input)

        self.emotion_input = discord.ui.TextInput(
            label="情感 emotion（可选）",
            placeholder="例如 happy / comfort / angry，留空=自动",
            required=False,
            max_length=64,
            default=session.emotion or "",
        )
        self.add_item(self.emotion_input)

        self.emotion_control_input = discord.ui.TextInput(
            label="情感开关/强度（可选）",
            placeholder="例: true,4.0 或 false 或 4.0 或留空",
            required=False,
            max_length=32,
            default=(
                (
                    f"{_format_optional_bool(session.enable_emotion)},"
                    f"{_format_optional_float(session.emotion_scale)}"
                )
                if session.enable_emotion is not None or session.emotion_scale is not None
                else ""
            ),
        )
        self.add_item(self.emotion_control_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            voice_type = (self.voice_type_input.value or "").strip() or None
            speed_ratio = _parse_optional_float(
                self.speed_ratio_input.value,
                field_name="speed_ratio",
                minimum=0.2,
                maximum=3.0,
            )
            pitch_ratio = _parse_optional_float(
                self.pitch_ratio_input.value,
                field_name="pitch_ratio",
                minimum=0.1,
                maximum=3.0,
            )
            emotion = (self.emotion_input.value or "").strip() or None
            enable_emotion, emotion_scale = _parse_emotion_control(
                self.emotion_control_input.value
            )
        except ValueError as e:
            await interaction.response.send_message(f"参数校验失败：{e}", ephemeral=True)
            return

        self.parent_view.session.voice_type = voice_type
        self.parent_view.session.speed_ratio = speed_ratio
        self.parent_view.session.pitch_ratio = pitch_ratio
        self.parent_view.session.emotion = emotion
        self.parent_view.session.enable_emotion = enable_emotion
        self.parent_view.session.emotion_scale = emotion_scale

        await interaction.response.send_message("参数已更新。", ephemeral=True)
        await self.parent_view.refresh_panel()


class SaveVoicePresetModal(discord.ui.Modal, title="管理员保存音色"):
    def __init__(self, parent_view: "VoiceGenerationPanelView"):
        super().__init__(timeout=300)
        self.parent_view = parent_view

        default_voice = (
            parent_view.session.voice_type
            or str(chat_config.VOICE_CONFIG.get("VOICE_TYPE", "")).strip()
        )

        self.voice_type_input = discord.ui.TextInput(
            label="音色 ID",
            placeholder="要保存的音色 ID（必填）",
            required=True,
            max_length=120,
            default=default_voice[:120],
        )
        self.add_item(self.voice_type_input)

        self.voice_hint_input = discord.ui.TextInput(
            label="音色说明（可选）",
            placeholder="例如：温柔女声，适合安慰场景",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=300,
            default="",
        )
        self.add_item(self.voice_hint_input)

        self.bind_app_id_input = discord.ui.TextInput(
            label="绑定复刻 APP_ID（可选）",
            placeholder="填写后写入 clone_voice_app_bindings",
            required=False,
            max_length=64,
            default="",
        )
        self.add_item(self.bind_app_id_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_admin_or_dev(interaction.user):
            await interaction.response.send_message(
                "仅管理员或开发者可以保存音色。", ephemeral=True
            )
            return

        voice_type = (self.voice_type_input.value or "").strip()
        voice_hint = (self.voice_hint_input.value or "").strip()
        bind_app_id = (self.bind_app_id_input.value or "").strip()

        if not voice_type:
            await interaction.response.send_message("音色 ID 不能为空。", ephemeral=True)
            return

        try:
            available_voice_types = [
                str(item).strip()
                for item in (chat_config.VOICE_CONFIG.get("AVAILABLE_VOICE_TYPES") or [])
                if str(item).strip()
            ]
            if voice_type not in available_voice_types:
                available_voice_types.append(voice_type)

            voice_type_hints = {
                str(k).strip(): str(v).strip()
                for k, v in (
                    (chat_config.VOICE_CONFIG.get("VOICE_TYPE_HINTS") or {}).items()
                    if isinstance(chat_config.VOICE_CONFIG.get("VOICE_TYPE_HINTS"), dict)
                    else []
                )
                if str(k).strip() and str(v).strip()
            }
            if voice_hint:
                voice_type_hints[voice_type] = voice_hint

            clone_voice_app_bindings = {
                str(k).strip(): str(v).strip()
                for k, v in (
                    (
                        chat_config.VOICE_CONFIG.get("CLONE_VOICE_APP_BINDINGS") or {}
                    ).items()
                    if isinstance(
                        chat_config.VOICE_CONFIG.get("CLONE_VOICE_APP_BINDINGS"), dict
                    )
                    else []
                )
                if str(k).strip() and str(v).strip()
            }
            if bind_app_id:
                clone_voice_app_bindings[voice_type] = bind_app_id

            # 更新运行时配置
            chat_config.VOICE_CONFIG["AVAILABLE_VOICE_TYPES"] = available_voice_types
            chat_config.VOICE_CONFIG["VOICE_TYPE_HINTS"] = voice_type_hints
            chat_config.VOICE_CONFIG["CLONE_VOICE_APP_BINDINGS"] = clone_voice_app_bindings

            # 同步环境变量（当前进程）
            serialized_available = json.dumps(available_voice_types, ensure_ascii=False)
            serialized_hints = json.dumps(voice_type_hints, ensure_ascii=False)
            serialized_bindings = json.dumps(
                clone_voice_app_bindings, ensure_ascii=False
            )

            os.environ["VOICE_AVAILABLE_TYPES"] = serialized_available
            os.environ["VOICE_TYPE_HINTS"] = serialized_hints
            os.environ["VOICE_CLONE_VOICE_APP_BINDINGS"] = serialized_bindings

            # 持久化到数据库全局设置
            await chat_db_manager.set_global_setting(
                "voice_available_types", serialized_available
            )
            await chat_db_manager.set_global_setting("voice_type_hints", serialized_hints)
            if bind_app_id:
                await chat_db_manager.set_global_setting(
                    "voice_clone_voice_app_bindings", serialized_bindings
                )

            # 重新初始化语音服务，确保路由立即生效
            voice_service.reinitialize()

            notes = ["已加入可用音色列表"]
            if voice_hint:
                notes.append("已保存音色说明")
            if bind_app_id:
                notes.append(f"已绑定 APP_ID={bind_app_id}")

            await interaction.response.send_message(
                f"音色 `{voice_type}` 保存成功：{', '.join(notes)}。",
                ephemeral=True,
            )
            await self.parent_view.refresh_panel()
        except Exception as e:
            log.error("保存音色失败: %s", e, exc_info=True)
            await interaction.response.send_message(
                f"保存音色失败：{e}", ephemeral=True
            )


class VoiceTypeSelectView(discord.ui.View):
    """音色下拉选择器（支持分页，展示已保存音色及说明）。"""

    def __init__(
        self,
        *,
        parent_panel: "VoiceGenerationPanelView",
        author_id: int,
        entries: Sequence[Tuple[str, str]],
    ):
        super().__init__(timeout=300)
        self.parent_panel = parent_panel
        self.author_id = author_id
        self.entries = list(entries)
        self.page_index = 0
        self.page_size = 25
        self._rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("这不是你的音色选择器。", ephemeral=True)
            return False
        return True

    def _total_pages(self) -> int:
        return max(1, (len(self.entries) + self.page_size - 1) // self.page_size)

    def _current_page_entries(self) -> List[Tuple[str, str]]:
        start = self.page_index * self.page_size
        end = start + self.page_size
        return self.entries[start:end]

    def _rebuild_items(self) -> None:
        self.clear_items()
        total_pages = self._total_pages()
        current_entries = self._current_page_entries()
        current_voice = (self.parent_panel.session.voice_type or "").strip()

        options: List[discord.SelectOption] = []
        for voice_type, hint in current_entries:
            options.append(
                discord.SelectOption(
                    label=_truncate_text_for_select(voice_type, 100),
                    value=voice_type,
                    description=_truncate_text_for_select(hint or "无说明", 100),
                    default=bool(current_voice and current_voice == voice_type),
                )
            )

        if not options:
            options.append(
                discord.SelectOption(
                    label="暂无可选音色",
                    value="__none__",
                    description="请先使用“管理员保存音色”",
                    default=True,
                )
            )

        select = discord.ui.Select(
            placeholder=f"选择音色（第 {self.page_index + 1}/{total_pages} 页）",
            options=options,
            min_values=1,
            max_values=1,
            disabled=not current_entries,
            row=0,
        )

        async def _on_select(interaction: discord.Interaction) -> None:
            selected = (select.values[0] if select.values else "").strip()
            if not selected or selected == "__none__":
                await interaction.response.send_message("当前没有可选音色。", ephemeral=True)
                return

            self.parent_panel.session.voice_type = selected
            await interaction.response.send_message(
                f"已选择音色：`{selected}`",
                ephemeral=True,
            )
            await self.parent_panel.refresh_panel()

        select.callback = _on_select
        self.add_item(select)

        if total_pages > 1:
            prev_btn = discord.ui.Button(
                label="上一页",
                style=discord.ButtonStyle.secondary,
                disabled=self.page_index <= 0,
                row=1,
            )
            next_btn = discord.ui.Button(
                label="下一页",
                style=discord.ButtonStyle.secondary,
                disabled=self.page_index >= total_pages - 1,
                row=1,
            )

            async def _on_prev(interaction: discord.Interaction) -> None:
                if self.page_index <= 0:
                    await interaction.response.defer()
                    return
                self.page_index -= 1
                self._rebuild_items()
                await interaction.response.edit_message(view=self)

            async def _on_next(interaction: discord.Interaction) -> None:
                if self.page_index >= total_pages - 1:
                    await interaction.response.defer()
                    return
                self.page_index += 1
                self._rebuild_items()
                await interaction.response.edit_message(view=self)

            prev_btn.callback = _on_prev
            next_btn.callback = _on_next
            self.add_item(prev_btn)
            self.add_item(next_btn)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True


class VoiceGenerationPanelView(discord.ui.View):
    """语音生成交互面板。"""

    def __init__(self, *, author_id: int, session: VoiceGenerationSession):
        super().__init__(timeout=900)
        self.author_id = author_id
        self.session = session
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("这不是你的语音面板。", ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except Exception:
                pass

    def build_panel_embed(self) -> discord.Embed:
        config_voice = str(chat_config.VOICE_CONFIG.get("VOICE_TYPE", "")).strip()
        provider = str(chat_config.VOICE_CONFIG.get("PROVIDER", "doubao")).strip().lower()

        text_preview = (self.session.text or "").strip()
        if not text_preview:
            text_preview = "（未设置）"
        elif len(text_preview) > 600:
            text_preview = text_preview[:600] + "..."

        default_voice_display = config_voice or "未配置"
        selected_voice_display = self.session.voice_type or f"默认({default_voice_display})"

        params_text = (
            f"voice_type: {selected_voice_display}\n"
            f"speed_ratio: {_format_optional_float(self.session.speed_ratio)}\n"
            f"pitch_ratio: {_format_optional_float(self.session.pitch_ratio)}\n"
            f"emotion: {self.session.emotion or '自动'}\n"
            f"enable_emotion: {_format_optional_bool(self.session.enable_emotion)}\n"
            f"emotion_scale: {_format_optional_float(self.session.emotion_scale)}\n"
            f"max_text_length: {max(20, int(self.session.max_text_length or 500))}"
        )

        embed = discord.Embed(
            title="语音生成面板",
            description="编辑文本和参数后，可试听或直接发送到当前频道。",
            color=discord.Color.blurple(),
        )
        embed.add_field(name="当前文本", value=text_preview, inline=False)
        embed.add_field(name="当前参数", value=f"```txt\n{params_text}\n```", inline=False)
        embed.add_field(
            name="服务状态",
            value=(
                f"provider: {provider}\n"
                f"service_available: {'yes' if voice_service.is_available() else 'no'}"
            ),
            inline=False,
        )
        saved_voice_entries = _collect_saved_voice_entries()
        embed.add_field(
            name="音色列表",
            value=f"已保存音色：{len(saved_voice_entries)} 个（可点“选择音色”下拉选择）",
            inline=False,
        )
        embed.set_footer(
            text="管理员可使用“管理员保存音色”将音色写入可用列表，并可选绑定复刻 APP_ID。"
        )
        return embed

    async def refresh_panel(self) -> None:
        if not self.message:
            return
        try:
            await self.message.edit(embed=self.build_panel_embed(), view=self)
        except Exception as e:
            log.debug("刷新语音面板失败（可忽略）: %s", e)

    async def _synthesize_current(self) -> Optional[VoiceResult]:
        clean_text = _strip_emoji_placeholders((self.session.text or "").strip())
        if not clean_text:
            return None

        return await voice_service.generate_voice(
            text=clean_text,
            voice_type=self.session.voice_type,
            speed_ratio=self.session.speed_ratio,
            pitch_ratio=self.session.pitch_ratio,
            emotion=self.session.emotion,
            enable_emotion=self.session.enable_emotion,
            emotion_scale=self.session.emotion_scale,
        )

    def _build_generation_report_embeds(
        self,
        *,
        request_user: discord.abc.User,
        generated_text: str,
        result: VoiceResult,
    ) -> List[discord.Embed]:
        requested_voice = (self.session.voice_type or "").strip() or "默认"
        actual_voice = (result.voice_type or "").strip() or requested_voice
        provider = (result.provider or "").strip() or "unknown"
        model_name = (result.model_name or "").strip() or "unknown"
        audio_format = (result.file_ext or "").strip() or "unknown"
        audio_kb = max(1, int(len(result.audio_bytes or b"") / 1024))

        params_text = (
            f"requested_voice_type: {requested_voice}\n"
            f"actual_voice_type: {actual_voice}\n"
            f"speed_ratio: {_format_optional_float(self.session.speed_ratio)}\n"
            f"pitch_ratio: {_format_optional_float(self.session.pitch_ratio)}\n"
            f"emotion: {self.session.emotion or '自动'}\n"
            f"enable_emotion: {_format_optional_bool(self.session.enable_emotion)}\n"
            f"emotion_scale: {_format_optional_float(self.session.emotion_scale)}\n"
            f"provider: {provider}\n"
            f"model_name: {model_name}\n"
            f"audio_format: {audio_format}\n"
            f"audio_size_kb: {audio_kb}"
        )

        chunks = _split_text_chunks(generated_text, chunk_size=900)
        total_chunks = len(chunks)
        embeds: List[discord.Embed] = []

        header_embed = discord.Embed(
            title="语音生成信息",
            description="以下为本次发送详情。",
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        header_embed.add_field(
            name="发送者",
            value=f"{request_user.mention}\nID: `{request_user.id}`",
            inline=False,
        )
        header_embed.add_field(
            name="生成参数",
            value=f"```txt\n{params_text}\n```",
            inline=False,
        )
        header_embed.add_field(
            name=f"生成文本（1/{total_chunks}）",
            value=chunks[0],
            inline=False,
        )
        embeds.append(header_embed)

        for index, chunk in enumerate(chunks[1:], start=2):
            text_embed = discord.Embed(
                title="语音生成信息（续）",
                color=discord.Color.green(),
            )
            text_embed.add_field(
                name=f"生成文本（{index}/{total_chunks}）",
                value=chunk,
                inline=False,
            )
            embeds.append(text_embed)

        return embeds

    @discord.ui.button(label="编辑文本", style=discord.ButtonStyle.primary, row=0)
    async def edit_text(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(VoiceTextModal(self))

    @discord.ui.button(label="选择音色", style=discord.ButtonStyle.secondary, row=0)
    async def choose_voice(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        entries = _collect_saved_voice_entries()
        if not entries:
            await interaction.response.send_message(
                "暂无已保存音色。请先让管理员点击“管理员保存音色”。",
                ephemeral=True,
            )
            return

        select_view = VoiceTypeSelectView(
            parent_panel=self,
            author_id=self.author_id,
            entries=entries,
        )
        await interaction.response.send_message(
            "请选择音色（已展示保存的音色和说明，可翻页）：",
            view=select_view,
            ephemeral=True,
        )

    @discord.ui.button(label="编辑参数", style=discord.ButtonStyle.secondary, row=0)
    async def edit_params(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(VoiceParamsModal(self))

    @discord.ui.button(
        label="试听（仅自己可见）",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def preview_voice(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if not voice_service.is_available():
            await interaction.response.send_message("语音服务当前不可用。", ephemeral=True)
            return

        clean_text = _strip_emoji_placeholders((self.session.text or "").strip())
        if not clean_text:
            await interaction.response.send_message(
                "文本为空，或仅包含表情占位符。请先编辑文本。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self._synthesize_current()
        if not result or not result.audio_bytes:
            await interaction.followup.send("试听失败，请稍后重试。", ephemeral=True)
            return

        if len(result.audio_bytes) > 25 * 1024 * 1024:
            await interaction.followup.send(
                "试听失败：音频文件过大（超过 25MB）。请缩短文本后重试。",
                ephemeral=True,
            )
            return

        filename = f"preview_voice.{result.file_ext or 'mp3'}"
        audio_file = discord.File(io.BytesIO(result.audio_bytes), filename=filename)
        await interaction.followup.send(
            content=(
                f"试听生成成功（provider={result.provider}, voice={result.voice_type}）。"
            ),
            file=audio_file,
            ephemeral=True,
        )

    @discord.ui.button(label="发送到当前频道", style=discord.ButtonStyle.success, row=1)
    async def send_voice(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if not voice_service.is_available():
            await interaction.response.send_message("语音服务当前不可用。", ephemeral=True)
            return

        channel = interaction.channel
        if channel is None:
            await interaction.response.send_message("当前会话不可用，无法发送语音。", ephemeral=True)
            return

        clean_text = _strip_emoji_placeholders((self.session.text or "").strip())
        if not clean_text:
            await interaction.response.send_message(
                "文本为空，或仅包含表情占位符。请先编辑文本。",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        result = await self._synthesize_current()
        if not result or not result.audio_bytes:
            await interaction.followup.send("语音生成失败，请稍后重试。", ephemeral=True)
            return

        if len(result.audio_bytes) > 25 * 1024 * 1024:
            await interaction.followup.send(
                "发送失败：音频文件过大（超过 25MB）。请缩短文本后重试。",
                ephemeral=True,
            )
            return

        sent_native_voice = False
        file_ext = (result.file_ext or "").strip().lower()

        try:
            # 优先发送 Discord 原生语音消息（ogg/opus）
            if file_ext in {"ogg", "opus"}:
                native_filename = "voice-message.ogg"
                estimated_duration = _estimate_ogg_opus_duration_seconds(result.audio_bytes)
                if estimated_duration is None:
                    estimated_duration = max(1.0, min(300.0, len(clean_text) / 6.0))

                try:
                    await _send_native_voice_message(
                        channel,
                        audio_bytes=result.audio_bytes,
                        filename=native_filename,
                        duration_secs=estimated_duration,
                    )
                    sent_native_voice = True
                    log.info(
                        "面板语音已按原生语音消息样式发送: provider=%s, model=%s, voice=%s",
                        result.provider,
                        result.model_name,
                        result.voice_type,
                    )
                except Exception as native_send_error:
                    log.warning(
                        "面板原生语音消息发送失败，将回退普通附件: %s",
                        native_send_error,
                    )

            if not sent_native_voice:
                filename = f"generated_voice.{result.file_ext or 'mp3'}"
                audio_file = discord.File(io.BytesIO(result.audio_bytes), filename=filename)
                await channel.send(file=audio_file)
                log.info(
                    "面板语音已按普通附件发送: provider=%s, model=%s, ext=%s",
                    result.provider,
                    result.model_name,
                    result.file_ext,
                )

            report_embeds = self._build_generation_report_embeds(
                request_user=interaction.user,
                generated_text=clean_text,
                result=result,
            )
            for report_embed in report_embeds:
                await channel.send(embed=report_embed)

        except Exception as send_error:
            log.error("发送语音或生成报告失败: %s", send_error, exc_info=True)
            await interaction.followup.send(
                f"发送失败：{send_error}",
                ephemeral=True,
            )
            return

        await interaction.followup.send(
            "语音已发送到当前频道（原生语音优先），并附带生成信息面板。",
            ephemeral=True,
        )

    @discord.ui.button(
        label="管理员保存音色",
        style=discord.ButtonStyle.secondary,
        row=2,
    )
    async def save_voice(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        if not _is_admin_or_dev(interaction.user):
            await interaction.response.send_message(
                "仅管理员或开发者可以使用此按钮。",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(SaveVoicePresetModal(self))

    @discord.ui.button(label="关闭面板", style=discord.ButtonStyle.danger, row=2)
    async def close_panel(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        await interaction.response.edit_message(view=self)
        self.stop()