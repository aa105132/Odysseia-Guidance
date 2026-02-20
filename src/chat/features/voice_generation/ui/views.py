# -*- coding: utf-8 -*-

"""
语音生成交互面板
- 支持编辑文本与参数
- 支持试听（仅自己可见）
- 支持发送到当前频道
- 管理员可选保存音色（并可绑定复刻音色 APP_ID）
"""

import io
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

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


@dataclass
class VoiceGenerationSession:
    text: str = ""
    voice_type: Optional[str] = None
    speed_ratio: Optional[float] = None
    pitch_ratio: Optional[float] = None
    emotion: Optional[str] = None
    enable_emotion: Optional[bool] = None
    emotion_scale: Optional[float] = None


class VoiceTextModal(discord.ui.Modal, title="编辑语音文本"):
    def __init__(self, parent_view: "VoiceGenerationPanelView"):
        super().__init__(timeout=300)
        self.parent_view = parent_view

        self.text_input = discord.ui.TextInput(
            label="要朗读的文本",
            placeholder="请输入要合成语音的文本",
            style=discord.TextStyle.paragraph,
            required=True,
            max_length=3000,
            default=(parent_view.session.text or "")[:3000],
        )
        self.add_item(self.text_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.parent_view.session.text = self.text_input.value.strip()
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
            f"emotion_scale: {_format_optional_float(self.session.emotion_scale)}"
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

    @discord.ui.button(label="编辑文本", style=discord.ButtonStyle.primary, row=0)
    async def edit_text(
        self, interaction: discord.Interaction, _: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(VoiceTextModal(self))

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

        filename = f"generated_voice.{result.file_ext or 'mp3'}"
        audio_file = discord.File(io.BytesIO(result.audio_bytes), filename=filename)
        await channel.send(file=audio_file)
        await interaction.followup.send("语音已发送到当前频道。", ephemeral=True)

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