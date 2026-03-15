# -*- coding: utf-8 -*-

"""
NovelAI 交互式绘图面板
提供 Discord UI 组件，让用户通过按钮和模态框设置绘图参数并生成图片
支持两种模式：
1. 直接Tag模式 - 直接输入英文Tag
2. AI描述模式 - 输入中文描述，由AI转换为英文Tag
"""

import logging
import io
import base64
import random
import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import discord

from src.chat.config.chat_config import NOVELAI_CONFIG
from src.chat.features.novelai_generation.services.novelai_service import novelai_service
from src.chat.features.novelai_generation.tag_rules import (
    clamp_danbooru_tags,
    get_tag_generation_prompt,
    get_rewrite_prompt,
    get_tag_generation_messages,
    get_rewrite_messages,
)
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)

NOVELAI_PROMPT_MAX_OUTPUT_TOKENS = 4096


def _detect_novelai_prompt_api_format(prompt_api_url: Optional[str]) -> Optional[str]:
    """根据 NovelAI 提示词专用 URL 自动推断 API 格式。"""
    if not prompt_api_url:
        return None

    lowered = prompt_api_url.lower()
    if (
        "generativelanguage.googleapis.com" in lowered
        or "aiplatform.googleapis.com" in lowered
        or "/v1beta" in lowered
    ):
        return "gemini"

    # 非 Gemini 风格端点默认按 OpenAI 兼容处理（如 /v1/chat/completions）
    return "openai"


def _get_novelai_prompt_llm_overrides() -> Dict[str, Optional[str]]:
    """获取 NovelAI 提示词专用 LLM 覆盖配置（模型 / URL / KEY / API 格式）。"""
    prompt_model = str(NOVELAI_CONFIG.get("PROMPT_MODEL", "") or "").strip() or None
    prompt_api_url = str(NOVELAI_CONFIG.get("PROMPT_API_URL", "") or "").strip() or None
    prompt_api_key = str(NOVELAI_CONFIG.get("PROMPT_API_KEY", "") or "").strip() or None
    prompt_api_format = _detect_novelai_prompt_api_format(prompt_api_url)
    return {
        "model_name": prompt_model,
        "api_url": prompt_api_url,
        "api_key": prompt_api_key,
        "api_format": prompt_api_format,
    }


def _is_probably_tag_prompt(prompt: str) -> bool:
    """粗略判断是否为 Danbooru 标签串（用于切换到 Imagen 时转自然语言）。"""
    if not prompt:
        return False
    tokens = [seg.strip() for seg in prompt.split(",") if seg.strip()]
    if len(tokens) < 16:
        return False
    ascii_like = sum(
        1
        for seg in tokens
        if re.fullmatch(r"[a-zA-Z0-9_:\-\.#()/'\s]+", seg) is not None
    )
    return (ascii_like / max(1, len(tokens))) >= 0.75


async def _convert_tag_prompt_to_imagen_prompt(prompt: str, force_rewrite: bool = False) -> str:
    """将 Danbooru Tag 串转换为适配 Imagen 的中文自然语言提示词。"""
    if not force_rewrite and not _is_probably_tag_prompt(prompt):
        return prompt

    from src.chat.services.gemini_service import gemini_service

    llm_overrides = _get_novelai_prompt_llm_overrides()
    conversion_messages = [
        {
            "role": "user",
            "content": (
                "你是图像提示词转换助手。请把下面的 Danbooru 标签串转换为适配 Gemini Imagen 的简体中文自然语言提示词。"
                "要求：\n"
                "1) 只输出最终中文提示词，不要解释。\n"
                "2) 保留并准确传达原标签中的主体、身份、服饰、场景、动作、构图、光影、氛围，不得篡改。\n"
                "3) 仅可在不改变语义的前提下做细化补充。\n"
                "4) 不要输出英文标签列表，不要输出多段。\n\n"
                f"标签串：{prompt}"
            ),
        }
    ]

    try:
        converted = await gemini_service.generate_simple_response(
            prompt="",
            generation_config={
                "temperature": 0.5,
                "max_output_tokens": 1200,
            },
            messages=conversion_messages,
            model_name=llm_overrides["model_name"],
            api_url=llm_overrides["api_url"],
            api_key=llm_overrides["api_key"],
            api_format=llm_overrides["api_format"],
        )
        normalized = (converted or "").strip().strip('"').strip("'")
        if normalized:
            log.info("已将 Danbooru 标签转换为 Imagen 中文自然语言提示词")
            return normalized
    except Exception as e:
        log.warning(f"Tag->Imagen 自然语言转换失败，回退原提示词: {e}")

    return prompt


async def _convert_imagen_prompt_to_novelai_prompt(prompt: str, force_rewrite: bool = False) -> str:
    """将 Imagen 中文自然语言提示词转换为 NovelAI Danbooru Tag。"""
    if not force_rewrite and _is_probably_tag_prompt(prompt):
        return prompt

    from src.chat.services.gemini_service import gemini_service

    llm_overrides = _get_novelai_prompt_llm_overrides()
    tag_messages = get_tag_generation_messages(description=prompt)
    try:
        converted = await gemini_service.generate_simple_response(
            prompt="",
            generation_config={
                "temperature": 0.45,
                "max_output_tokens": NOVELAI_PROMPT_MAX_OUTPUT_TOKENS,
            },
            messages=tag_messages,
            model_name=llm_overrides["model_name"],
            api_url=llm_overrides["api_url"],
            api_key=llm_overrides["api_key"],
            api_format=llm_overrides["api_format"],
        )
        normalized = clamp_danbooru_tags(converted, max_tags=90)
        if normalized:
            log.info("已将 Imagen 自然语言提示词转换为 NovelAI 标签串（标签数≤90）")
            return normalized
    except Exception as e:
        log.warning(f"Imagen->NovelAI 提示词转换失败，回退原提示词: {e}")

    return prompt


def _prompt_already_contains_artist(prompt: str, artist_string: str) -> bool:
    """检查 prompt 中是否已经包含了画师串的核心内容，避免重复拼接。"""
    artist_tags = re.findall(r'artist[:\s]+[\w\-_/()]+', artist_string.lower())
    if not artist_tags:
        first_tags = [t.strip().lower() for t in artist_string.split(",")[:3] if t.strip()]
        if not first_tags:
            return False
        prompt_lower = prompt.lower()
        match_count = sum(1 for t in first_tags if t in prompt_lower)
        return match_count >= max(1, len(first_tags) // 2)

    prompt_lower = prompt.lower()
    match_count = sum(1 for tag in artist_tags if tag in prompt_lower)
    return match_count > len(artist_tags) // 2


def _normalize_tag_for_match(tag: str) -> str:
    """标准化标签文本，用于去权重后的等价匹配。"""
    normalized = str(tag or "").strip().lower()
    normalized = normalized.replace("（", "(").replace("）", ")")
    normalized = re.sub(r"^\s*\d+(?:\.\d+)?::\s*", "", normalized)
    normalized = re.sub(r"\s*::\s*$", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _split_prompt_tokens(prompt: str) -> List[str]:
    """按逗号切分并清洗 Tag。"""
    return [seg.strip() for seg in str(prompt or "").split(",") if seg and seg.strip()]


def _strip_artist_tokens(prompt: str, artist_strings: List[Optional[str]]) -> str:
    """
    从 prompt 中剥离画师串里的标签（不限于前缀，进行全量剥离）。
    用于“切换画师串/重复重生”时，避免旧画师串残留。
    """
    tokens = _split_prompt_tokens(prompt)
    if not tokens:
        return str(prompt or "").strip()

    artist_token_set = set()
    for artist in artist_strings:
        for artist_token in _split_prompt_tokens(str(artist or "")):
            normalized_artist_token = _normalize_tag_for_match(artist_token)
            if normalized_artist_token:
                artist_token_set.add(normalized_artist_token)

    if not artist_token_set:
        return ", ".join(tokens)

    filtered_tokens: List[str] = []
    for token in tokens:
        normalized_token = _normalize_tag_for_match(token)
        if normalized_token and normalized_token in artist_token_set:
            continue
        filtered_tokens.append(token)

    return ", ".join(filtered_tokens)


def _compose_prompt_with_artist(prompt: str, artist_string: Optional[str]) -> str:
    """将画师串与正面提示词进行组合，并避免重复拼接。"""
    base_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()

    if not normalized_artist:
        return base_prompt
    if not base_prompt:
        return normalized_artist

    if _prompt_already_contains_artist(base_prompt, normalized_artist):
        return base_prompt
    return f"{normalized_artist}, {base_prompt}"


def _build_prompt_preview_pages(
    artist_string: Optional[str],
    positive_prompt: Optional[str],
    negative_prompt: Optional[str],
) -> List[str]:
    """构建“画师串/正面/负面”三段代码块展示内容，超长时自动拆分多条消息。"""
    default_negative_prompt = str(NOVELAI_CONFIG.get("DEFAULT_NEGATIVE_PROMPT", "") or "").strip()
    artist_text = str(artist_string or "").strip() or "（无画师串）"
    positive_text = str(positive_prompt or "").strip() or "（无）"
    negative_text = str(negative_prompt or "").strip() or default_negative_prompt or "（无默认负面提示词）"

    def _build_section_blocks(title: str, value: str) -> List[str]:
        chunk_size = 1400
        chunks = [value[i:i + chunk_size] for i in range(0, len(value), chunk_size)] or [value]
        blocks: List[str] = []
        for idx, chunk in enumerate(chunks):
            suffix = "（续）" if idx > 0 else ""
            blocks.append(f"**{title}{suffix}：**\n```\n{chunk}\n```")
        return blocks

    all_blocks: List[str] = []
    all_blocks.extend(_build_section_blocks("画师串", artist_text))
    all_blocks.extend(_build_section_blocks("正面提示词", positive_text))
    all_blocks.extend(_build_section_blocks("负面提示词", negative_text))

    pages: List[str] = []
    current_page = ""
    for block in all_blocks:
        candidate = f"{current_page}\n\n{block}" if current_page else block
        if len(candidate) <= 2000:
            current_page = candidate
            continue

        if current_page:
            pages.append(current_page)
            current_page = block
            continue

        # 极端兜底：单个区块仍超长时硬切
        hard_chunks = [block[i:i + 1900] for i in range(0, len(block), 1900)] or [block]
        pages.extend(hard_chunks[:-1])
        current_page = hard_chunks[-1]

    if current_page:
        pages.append(current_page)

    return pages or ["（无可展示内容）"]


async def _build_prompt_summary_for_embed(
    prompt: Optional[str],
    negative_prompt: Optional[str],
    artist_string: Optional[str],
    use_ai_conversion: bool = True,
) -> str:
    """构建主体外貌展示文本：直接预填最终提示词，不再额外调用 AI 转中文。"""
    _ = negative_prompt  # 保留参数签名，避免影响现有调用方
    _ = use_ai_conversion  # 历史参数保留，当前统一走本地预填逻辑

    normalized_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()
    if normalized_artist and normalized_prompt:
        stripped_prompt = _strip_artist_tokens(normalized_prompt, [normalized_artist])
        normalized_prompt = stripped_prompt or normalized_prompt

    compact_prompt = re.sub(r"\s+", " ", normalized_prompt).strip()
    if not compact_prompt:
        return "（AI未提供主体外貌标签）"

    if len(compact_prompt) > 980:
        return compact_prompt[:977] + "..."
    return compact_prompt


def _build_artist_name_for_embed(preset_name: Optional[str], artist_string: Optional[str]) -> str:
    normalized_preset_name = str(preset_name or '').strip()
    if normalized_preset_name:
        return normalized_preset_name

    normalized_artist = str(artist_string or '').strip()
    if normalized_artist:
        return '\u7cfb\u7edf\u9ed8\u8ba4/\u81ea\u5b9a\u4e49\u753b\u5e08\u4e32'

    return '\uff08\u65e0\u753b\u5e08\u4e32\uff09'


def _build_video_prompt_from_image(image_prompt: str, user_idea: str) -> str:
    """基于图片提示词和用户补充描述构建视频提示词。"""
    base_prompt = (image_prompt or "").strip()
    idea = (user_idea or "").strip()
    common_requirements = (
        "保持原图画风，保持主体与场景一致，画风一致，整体风格自然流畅，镜头具有电影感。"
    )

    if idea:
        return (
            "请基于这张图片生成一段短视频。"
            f"通用要求：{common_requirements}"
            f"\n原图提示词：{base_prompt}"
            f"\n补充要求：{idea}"
        )

    return (
        "请基于这张图片生成一段短视频。"
        f"通用要求：{common_requirements}"
        f"\n原图提示词：{base_prompt}"
    )


async def _generate_video_from_image_interaction(
    interaction: discord.Interaction,
    image_prompt: str,
    user_idea: str,
) -> None:
    """根据当前图片消息生成视频并反馈执行结果。"""
    if interaction.channel is None:
        await interaction.followup.send("当前频道不可用，无法生成视频。", ephemeral=True)
        return

    from src.chat.features.tools.functions.generate_video import generate_video

    video_prompt = _build_video_prompt_from_image(
        image_prompt=image_prompt,
        user_idea=user_idea,
    )
    result = await generate_video(
        prompt=video_prompt,
        duration=5,
        use_reference_image=True,
        preview_message="正在根据这张图片生成视频，请稍候...",
        success_message="视频已生成并发送到频道。",
        message=interaction.message if isinstance(interaction.message, discord.Message) else None,
        channel=interaction.channel,
        user_id=str(interaction.user.id),
        bot=interaction.client,
        request_user=interaction.user,
    )

    if isinstance(result, dict) and result.get("success"):
        await interaction.followup.send("视频已生成，结果消息已发送到当前频道。", ephemeral=True)
        return

    hint = ""
    if isinstance(result, dict):
        hint = str(result.get("hint") or "").strip()

    await interaction.followup.send(
        f"生成视频失败：{hint or '服务暂时不可用或本次请求未成功，请稍后重试。'}",
        ephemeral=True,
    )


# 尺寸预设映射
SIZE_PRESETS = {
    "竖图": (832, 1216),
    "横图": (1216, 832),
    "正方形": (1024, 1024),

}



@dataclass
class NovelAISession:
    """存储一次绘图会话的参数状态"""
    mode: str = "tag"  # "tag" 或 "ai_describe"
    scene_prompt: str = ""  # 场景/描述文本（AI模式用中文，Tag模式用英文Tag）
    negative_prompt: str = ""
    artist_prefix_mode: str = "default"  # default | preset | none
    preset_name: Optional[str] = None
    preset_artist_string: str = ""
    width: int = field(default_factory=lambda: int(NOVELAI_CONFIG.get("DEFAULT_WIDTH", 832)))
    height: int = field(default_factory=lambda: int(NOVELAI_CONFIG.get("DEFAULT_HEIGHT", 1216)))
    size_label: str = "竖图"
    sampler: str = field(default_factory=lambda: str(NOVELAI_CONFIG.get("DEFAULT_SAMPLER", "k_euler_ancestral")))
    steps: int = field(default_factory=lambda: int(NOVELAI_CONFIG.get("DEFAULT_STEPS", 28)))
    scale: float = field(default_factory=lambda: float(NOVELAI_CONFIG.get("DEFAULT_SCALE", 5.0)))
    seed: Optional[int] = None
    reference_image_b64: Optional[str] = None
    reference_strength: float = 0.6
    reference_info_extracted: float = 1.0


def _infer_size_label(width: int, height: int) -> str:
    for label, (w, h) in SIZE_PRESETS.items():
        if width == w and height == h:
            return label
    return f"{width}x{height}"


async def _persist_novelai_generation_settings(user_id: int, session: NovelAISession) -> None:
    await chat_db_manager.set_novelai_generation_settings(
        user_id=user_id,
        width=session.width,
        height=session.height,
        steps=session.steps,
        scale=session.scale,
        sampler=session.sampler,
        model=NOVELAI_CONFIG.get("MODEL", "nai-diffusion-4-5-full"),
    )


class NovelAIDrawPanel(discord.ui.View):
    """
    交互式绘图面板
    使用按钮控制各项参数，一键生成图片
    """

    def __init__(self, session: NovelAISession, user_id: int):
        super().__init__(timeout=600)  # 10 分钟超时
        self.session = session
        self.user_id = user_id
        # 动态更新模式切换按钮的文字
        self._update_mode_button()

    def _update_mode_button(self):
        """根据当前模式更新按钮标签"""
        if self.session.mode == "tag":
            self.btn_mode.label = "切换为AI描述模式"
            self.btn_mode.emoji = "🤖"
        else:
            self.btn_mode.label = "切换为Tag模式"
            self.btn_mode.emoji = "📝"

    def build_panel_embed(self) -> discord.Embed:
        """构建面板的 Embed"""
        embed = discord.Embed(
            title="🎨 NovelAI 绘图面板",
            description="配置参数后点击「开始绘制」",
            color=0x9B59B6,
        )

        # 模式显示
        if self.session.mode == "tag":
            mode_text = "📝 直接Tag模式\n直接输入英文Tag，不经过AI转换"
        else:
            mode_text = "🤖 AI描述模式\n输入中文/英文描述，由AI生成英文Tag"
        embed.add_field(name="🎯 模式", value=mode_text, inline=False)

        # 场景/描述
        if self.session.scene_prompt:
            prompt_preview = self.session.scene_prompt[:200]
            if len(self.session.scene_prompt) > 200:
                prompt_preview += "..."
        else:
            prompt_preview = "（未设置）"
        embed.add_field(name="📝 场景/描述", value=prompt_preview, inline=False)

        # 负面提示词
        if self.session.negative_prompt:
            neg_preview = self.session.negative_prompt[:100]
            if len(self.session.negative_prompt) > 100:
                neg_preview += "..."
            neg_text = f"🌐 自定义: {neg_preview}"
        else:
            default_neg = NOVELAI_CONFIG.get("DEFAULT_NEGATIVE_PROMPT", "")
            neg_text = f"🌐 通用: {default_neg[:60]}..." if default_neg else "🌐 使用默认"
        embed.add_field(name="🚫 负面提示词", value=neg_text, inline=False)

        # 尺寸 / 步数 / CFG (一行三列)
        embed.add_field(
            name="📐 尺寸",
            value=f"▪ {self.session.size_label} ({self.session.width}x{self.session.height})",
            inline=True,
        )
        embed.add_field(
            name="🔢 步数",
            value=f"{self.session.steps} 步",
            inline=True,
        )
        embed.add_field(
            name="📊 CFG",
            value=f"{self.session.scale}",
            inline=True,
        )

        # 采样器 / 画师串
        embed.add_field(
            name="⚙️ 采样器",
            value=f"⭐ {self.session.sampler}",
            inline=True,
        )
        if self.session.artist_prefix_mode == "none":
            artist_display = "无画师串"
        elif self.session.artist_prefix_mode == "preset" and self.session.preset_name:
            artist_display = self.session.preset_name
        else:
            artist_display = "默认"
        embed.add_field(
            name="🎨 画师串/前缀",
            value=artist_display,
            inline=True,
        )

        # 种子
        seed_display = str(self.session.seed) if self.session.seed is not None else "随机"
        embed.add_field(name="🎲 种子", value=seed_display, inline=True)

        # 氛围转移
        if self.session.reference_image_b64:
            embed.add_field(
                name="🎭 氛围转移",
                value=f"已上传 | 强度: {self.session.reference_strength}",
                inline=True,
            )

        # 成本提示
        cost = NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5)
        model = NOVELAI_CONFIG.get("MODEL", "nai-diffusion-4-5-full")
        embed.set_footer(text=f"💎 生成成本: {cost} 月光币 | 大尺寸/高步数会消耗更多点数 | 模型: {model}")

        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("这不是你的面板。", ephemeral=True)
            return False
        return True

    # ================================================================
    # Row 0: 模式切换 + 设置场景
    # ================================================================

    @discord.ui.button(label="切换为AI描述模式", style=discord.ButtonStyle.secondary, emoji="🤖", row=0)
    async def btn_mode(self, interaction: discord.Interaction, button: discord.ui.Button):
        """切换 Tag 模式 / AI 描述模式"""
        if self.session.mode == "tag":
            self.session.mode = "ai_describe"
        else:
            self.session.mode = "tag"
        self._update_mode_button()
        embed = self.build_panel_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="设置场景", style=discord.ButtonStyle.secondary, emoji="📝", row=0)
    async def btn_scene(self, interaction: discord.Interaction, button: discord.ui.Button):
        """打开场景/描述输入模态框"""
        modal = SceneModal(self.session)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="负面提示词", style=discord.ButtonStyle.secondary, emoji="🚫", row=0)
    async def btn_negative(self, interaction: discord.Interaction, button: discord.ui.Button):
        """设置负面提示词"""
        modal = NegativePromptModal(self.session)
        await interaction.response.send_modal(modal)

    # ================================================================
    # Row 1: 尺寸 + 步数 + CFG + 采样器 + 画师串
    # ================================================================

    @discord.ui.button(label="尺寸", style=discord.ButtonStyle.secondary, emoji="📐", row=1)
    async def btn_size(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SizeSelectView(self.session, self)
        await interaction.response.send_message("选择图片尺寸:", view=view, ephemeral=True)

    @discord.ui.button(label="步数", style=discord.ButtonStyle.secondary, emoji="🔢", row=1)
    async def btn_steps(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = StepsModal(self.session)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="CFG", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def btn_cfg(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = CFGModal(self.session)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="采样器", style=discord.ButtonStyle.secondary, emoji="⚙️", row=1)
    async def btn_sampler(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = SamplerSelectView(self.session, self)
        await interaction.response.send_message("选择采样器:", view=view, ephemeral=True)

    @discord.ui.button(label="画师串", style=discord.ButtonStyle.secondary, emoji="🎨", row=1)
    async def btn_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_presets = await chat_db_manager.get_novelai_presets(self.user_id)
        admin_presets = await chat_db_manager.get_novelai_admin_presets()
        view = PresetSelectView(self.session, self, user_presets, admin_presets)
        await interaction.response.send_message("选择画师串模式/预设:", view=view, ephemeral=True)

    # ================================================================
    # Row 2: 种子 + 保存预设 + 管理预设
    # ================================================================

    @discord.ui.button(label="种子", style=discord.ButtonStyle.secondary, emoji="🎲", row=2)
    async def btn_seed(self, interaction: discord.Interaction, button: discord.ui.Button):
        """设置随机种子"""
        modal = SeedModal(self.session)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="保存预设", style=discord.ButtonStyle.secondary, emoji="💾", row=2)
    async def btn_save_preset(self, interaction: discord.Interaction, button: discord.ui.Button):
        """保存当前画师串为预设"""
        modal = SavePresetModal(self.user_id)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="预设列表", style=discord.ButtonStyle.secondary, emoji="📋", row=2)
    async def btn_manage_presets(self, interaction: discord.Interaction, button: discord.ui.Button):
        """查看和管理预设列表"""
        presets = await chat_db_manager.get_novelai_presets(self.user_id)
        if not presets:
            return await interaction.response.send_message(
                "你还没有保存任何预设。点击「保存预设」来创建一个！",
                ephemeral=True,
            )
        view = PresetManageView(self.session, self, presets, self.user_id)
        await interaction.response.send_message("管理你的画师串预设:", view=view, ephemeral=True)

    # ================================================================
    # Row 3: 重置配置 + 开始绘制
    # ================================================================

    @discord.ui.button(label="重置配置", style=discord.ButtonStyle.danger, emoji="🗑️", row=3)
    async def btn_reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        """重置所有参数为默认"""
        self.session.mode = "tag"
        self.session.scene_prompt = ""
        self.session.negative_prompt = ""
        self.session.artist_prefix_mode = "default"
        self.session.preset_name = None
        self.session.preset_artist_string = ""
        self.session.width = int(NOVELAI_CONFIG.get("DEFAULT_WIDTH", 832))
        self.session.height = int(NOVELAI_CONFIG.get("DEFAULT_HEIGHT", 1216))
        self.session.size_label = _infer_size_label(self.session.width, self.session.height)
        self.session.sampler = str(NOVELAI_CONFIG.get("DEFAULT_SAMPLER", "k_euler_ancestral"))
        self.session.steps = int(NOVELAI_CONFIG.get("DEFAULT_STEPS", 28))
        self.session.scale = float(NOVELAI_CONFIG.get("DEFAULT_SCALE", 5.0))
        self.session.seed = None
        self.session.reference_image_b64 = None
        self.session.reference_strength = 0.6
        self.session.reference_info_extracted = 1.0

        await chat_db_manager.set_novelai_active_preset_state(self.user_id, "default")
        await _persist_novelai_generation_settings(self.user_id, self.session)

        self._update_mode_button()
        embed = self.build_panel_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="开始绘制", style=discord.ButtonStyle.success, emoji="🖌️", row=3)
    async def btn_generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        """开始生成图片"""
        if not self.session.scene_prompt and not self.session.preset_artist_string:
            return await interaction.response.send_message(
                "请先设置场景/描述或选择画师串预设！",
                ephemeral=True,
            )

        cost = NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5)

        # 检查余额
        balance = await coin_service.get_balance(self.user_id)
        if balance < cost:
            return await interaction.response.send_message(
                f"月光币余额不足！需要 {cost}，当前 {balance}。",
                ephemeral=True,
            )

        await interaction.response.defer(thinking=True)

        try:
            await _persist_novelai_generation_settings(self.user_id, self.session)

            # === 根据模式构建最终提示词 ===
            final_prompt = await self._build_final_prompt()

            if not final_prompt:
                return await interaction.followup.send(
                    "提示词生成失败，请检查输入或稍后重试。",
                    ephemeral=True,
                )

            # 负面提示词
            negative = self.session.negative_prompt if self.session.negative_prompt else None

            current_artist_string: Optional[str] = None
            if self.session.artist_prefix_mode == 'preset':
                current_artist_string = str(self.session.preset_artist_string or '').strip() or None
            elif self.session.artist_prefix_mode != 'none':
                current_artist_string = str(NOVELAI_CONFIG.get('DEFAULT_ARTIST_STRING', '') or '').strip() or None

            appearance_summary = await _build_prompt_summary_for_embed(
                prompt=final_prompt,
                negative_prompt=negative,
                artist_string=current_artist_string,
            )

            result = await novelai_service.generate_image(
                prompt=final_prompt,
                negative_prompt=negative,
                width=self.session.width,
                height=self.session.height,
                sampler=self.session.sampler,
                steps=self.session.steps,
                scale=self.session.scale,
                seed=self.session.seed,
                reference_image=self.session.reference_image_b64,
                reference_strength=self.session.reference_strength,
                reference_info_extracted=self.session.reference_info_extracted,
            )

            if result is None:
                return await interaction.followup.send(
                    "图片生成失败了...请稍后再试或更换提示词。"
                )

            # 扣除月光币
            new_balance = await coin_service.remove_coins(
                user_id=self.user_id,
                amount=cost,
                reason=f"NovelAI 生图: {final_prompt[:50]}",
            )
            if new_balance is None:
                return await interaction.followup.send(
                    "月光币扣除失败，余额不足。",
                    ephemeral=True,
                )

            # 构建结果 Embed（不显示提示词，通过按钮查看）
            embed = discord.Embed(title="🎨 NovelAI 图像生成", color=0x2B2D31)
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            # 生成信息（紧凑排列）
            model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
            embed.add_field(
                name='画师串',
                value=_build_artist_name_for_embed(self.session.preset_name, current_artist_string),
                inline=True,
            )
            embed.add_field(name="种子", value=str(result.seed), inline=True)
            embed.add_field(
                name="参数",
                value=f"{result.width}x{result.height} | {self.session.steps}步 | CFG {self.session.scale}",
                inline=True,
            )
            embed.add_field(
                name="主体外貌",
                value=appearance_summary,
                inline=False,
            )
            if self.session.mode == "ai_describe" and self.session.scene_prompt:
                embed.add_field(
                    name="原始描述",
                    value=self.session.scene_prompt[:200],
                    inline=False,
                )
            if self.session.reference_image_b64:
                embed.add_field(
                    name="氛围转移",
                    value=f"强度: {self.session.reference_strength}",
                    inline=True,
                )
            embed.set_footer(
                text=(
                    f"消耗 {cost} 月光币 | 余额: {new_balance} | "
                    f"{self.session.sampler} | {model_name}"
                )
            )

            image_file = discord.File(
                io.BytesIO(result.image_data),
                filename="novelai_generated.png",
                spoiler=True,
            )
            # 注意：不使用 embed.set_image()，因为 embed 中的图片不会被 spoiler 遮罩
            # 图片作为独立附件发送，这样 spoiler 标记才能正常生效

            # 创建交互按钮视图（重新生成/修改提示词/切换到Imagen/AI重写prompt）
            prompt_without_artist = _strip_artist_tokens(final_prompt, [current_artist_string])
            if not prompt_without_artist:
                prompt_without_artist = final_prompt

            result_view = SlashNovelAIResultView(
                prompt=prompt_without_artist,
                artist_string=current_artist_string,
                negative_prompt=self.session.negative_prompt or None,
                width=self.session.width,
                height=self.session.height,
                steps=self.session.steps,
                scale=self.session.scale,
                sampler=self.session.sampler,
                preset_name=self.session.preset_name,
                user_id=interaction.user.id,
                cost=cost,
            )

            await interaction.followup.send(embed=embed, file=image_file, view=result_view)

            # 更新面板（清除种子以便下次随机）
            self.session.seed = None
            panel_embed = self.build_panel_embed()
            try:
                await interaction.message.edit(embed=panel_embed, view=self)
            except Exception:
                pass

        except Exception as e:
            log.error(f"NovelAI 生图出错: {e}", exc_info=True)
            await interaction.followup.send(
                f"生成图片时发生错误: {str(e)[:200]}",
                ephemeral=True,
            )

    async def _build_final_prompt(self) -> Optional[str]:
        """根据模式构建最终的英文提示词"""
        parts = []

        # 添加画师串前缀（支持 default / preset / none 三种模式）
        if self.session.artist_prefix_mode == "none":
            log.info("/draw 面板当前为无画师串模式，不拼接前缀")
        elif self.session.artist_prefix_mode == "preset" and self.session.preset_artist_string:
            parts.append(self.session.preset_artist_string)
        else:
            default_artist = NOVELAI_CONFIG.get("DEFAULT_ARTIST_STRING", "")
            if default_artist:
                parts.append(default_artist)
                log.info(f"/draw 面板应用全局默认画师串: {default_artist[:60]}...")

        if self.session.mode == "ai_describe" and self.session.scene_prompt:
            # AI 描述模式：调用 AI 将中文描述转换为英文 Tag（含预填充对话解除限制）
            try:
                from src.chat.services.gemini_service import gemini_service

                llm_overrides = _get_novelai_prompt_llm_overrides()
                prefill_messages = get_tag_generation_messages(description=self.session.scene_prompt)
                tags = await gemini_service.generate_simple_response(
                    prompt="",  # messages 模式下 prompt 被忽略
                    generation_config={
                        "temperature": 0.45,
                        "max_output_tokens": NOVELAI_PROMPT_MAX_OUTPUT_TOKENS,
                    },
                    messages=prefill_messages,
                    model_name=llm_overrides["model_name"],
                    api_url=llm_overrides["api_url"],
                    api_key=llm_overrides["api_key"],
                    api_format=llm_overrides["api_format"],
                )
                if tags:
                    tags = clamp_danbooru_tags(tags, max_tags=90)
                    parts.append(tags)
                    log.info(f"AI描述模式生成Tag（标签数≤90）: {tags[:100]}...")
                else:
                    log.warning("AI描述模式生成Tag失败，回退使用原始描述")
                    parts.append(self.session.scene_prompt)
            except Exception as e:
                log.error(f"AI描述转Tag失败: {e}")
                # 回退：直接使用原始描述
                parts.append(self.session.scene_prompt)

        elif self.session.scene_prompt:
            # Tag 模式：直接使用用户输入的英文 Tag
            parts.append(self.session.scene_prompt)

        return ", ".join(parts) if parts else None


# ================================================================
# 模态框：场景/描述输入
# ================================================================

class SceneModal(discord.ui.Modal, title="设置场景/描述"):
    scene_input = discord.ui.TextInput(
        label="场景/描述",
        style=discord.TextStyle.paragraph,
        placeholder="Tag模式: 1girl, silver hair, blue eyes\nAI模式: 一个银发蓝眼的少女站在星空下",
        required=True,
        max_length=2000,
    )

    def __init__(self, session: NovelAISession):
        super().__init__()
        self.session = session
        if session.scene_prompt:
            self.scene_input.default = session.scene_prompt

    async def on_submit(self, interaction: discord.Interaction):
        self.session.scene_prompt = self.scene_input.value.strip()
        embed = NovelAIDrawPanel(self.session, interaction.user.id).build_panel_embed()
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.response.send_message("场景已设置！", ephemeral=True)


# ================================================================
# 模态框：负面提示词
# ================================================================

class NegativePromptModal(discord.ui.Modal, title="设置负面提示词"):
    negative_input = discord.ui.TextInput(
        label="负面提示词（留空使用默认）",
        style=discord.TextStyle.paragraph,
        placeholder="留空使用默认负面提示词",
        required=False,
        max_length=2000,
    )

    def __init__(self, session: NovelAISession):
        super().__init__()
        self.session = session
        if session.negative_prompt:
            self.negative_input.default = session.negative_prompt

    async def on_submit(self, interaction: discord.Interaction):
        self.session.negative_prompt = self.negative_input.value.strip()
        embed = NovelAIDrawPanel(self.session, interaction.user.id).build_panel_embed()
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.response.send_message("负面提示词已设置！", ephemeral=True)


# ================================================================
# 模态框：步数
# ================================================================

class StepsModal(discord.ui.Modal, title="设置步数"):
    steps_input = discord.ui.TextInput(
        label="步数 (1-28)",
        placeholder="28",
        required=True,
        max_length=3,
    )

    def __init__(self, session: NovelAISession):
        super().__init__()
        self.session = session
        self.steps_input.default = str(session.steps)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            steps = int(self.steps_input.value)
            if steps > 28:
                return await interaction.response.send_message("步数最大为 28，已超出限制。", ephemeral=True)
            self.session.steps = max(1, min(28, steps))
        except ValueError:
            return await interaction.response.send_message("步数必须是整数。", ephemeral=True)

        await _persist_novelai_generation_settings(interaction.user.id, self.session)
        embed = NovelAIDrawPanel(self.session, interaction.user.id).build_panel_embed()
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.response.send_message(f"步数已设置为 {self.session.steps}", ephemeral=True)


# ================================================================
# 模态框：CFG Scale
# ================================================================

class CFGModal(discord.ui.Modal, title="设置引导强度 (CFG)"):
    cfg_input = discord.ui.TextInput(
        label="CFG Scale (1.0 - 10.0)",
        placeholder="5.0",
        required=True,
        max_length=5,
    )

    def __init__(self, session: NovelAISession):
        super().__init__()
        self.session = session
        self.cfg_input.default = str(session.scale)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            scale = float(self.cfg_input.value)
            self.session.scale = round(max(1.0, min(10.0, scale)), 1)
        except ValueError:
            return await interaction.response.send_message("CFG 必须是数字。", ephemeral=True)

        await _persist_novelai_generation_settings(interaction.user.id, self.session)
        embed = NovelAIDrawPanel(self.session, interaction.user.id).build_panel_embed()
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.response.send_message(f"CFG 已设置为 {self.session.scale}", ephemeral=True)


# ================================================================
# 尺寸选择视图
# ================================================================

class SizeSelectView(discord.ui.View):
    def __init__(self, session: NovelAISession, parent_panel: NovelAIDrawPanel):
        super().__init__(timeout=60)
        self.session = session
        self.parent_panel = parent_panel

        options = []
        for label, (w, h) in SIZE_PRESETS.items():
            is_default = (label == session.size_label)
            options.append(
                discord.SelectOption(
                    label=f"{label} ({w}x{h})",
                    value=label,
                    default=is_default,
                )
            )
        select = discord.ui.Select(placeholder="选择尺寸", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        if value in SIZE_PRESETS:
            w, h = SIZE_PRESETS[value]
            self.session.width = w
            self.session.height = h
            self.session.size_label = value

        await _persist_novelai_generation_settings(interaction.user.id, self.session)
        await interaction.response.send_message(
            f"尺寸已设置为 {self.session.size_label} ({self.session.width}x{self.session.height})",
            ephemeral=True,
        )


# ================================================================
# 采样器选择视图
# ================================================================

class SamplerSelectView(discord.ui.View):
    SAMPLERS = [
        ("k_euler", "Euler"),
        ("k_euler_ancestral", "Euler Ancestral (推荐)"),
        ("k_dpmpp_2s_ancestral", "DPM++ 2S Ancestral"),
        ("k_dpmpp_2m", "DPM++ 2M"),
        ("k_dpmpp_sde", "DPM++ SDE"),
        ("ddim", "DDIM"),
    ]

    def __init__(self, session: NovelAISession, parent_panel: NovelAIDrawPanel):
        super().__init__(timeout=60)
        self.session = session
        self.parent_panel = parent_panel

        options = []
        for value, label in self.SAMPLERS:
            is_default = (value == session.sampler)
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    default=is_default,
                )
            )
        select = discord.ui.Select(placeholder="选择采样器", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        self.session.sampler = value
        await _persist_novelai_generation_settings(interaction.user.id, self.session)
        await interaction.response.send_message(
            f"采样器已设置为 {value}",
            ephemeral=True,
        )


# ================================================================
# 预设选择视图
# ================================================================

class PresetSelectView(discord.ui.View):
    def __init__(
        self,
        session: NovelAISession,
        parent_panel: NovelAIDrawPanel,
        user_presets: list,
        admin_presets: list,
    ):
        super().__init__(timeout=60)
        self.session = session
        self.parent_panel = parent_panel
        self.preset_map: Dict[str, dict] = {}

        options = [
            discord.SelectOption(
                label="系统默认画师串",
                value="__default__",
                description="使用全局默认画师串",
                emoji="🌐",
                default=session.artist_prefix_mode == "default",
            ),
            discord.SelectOption(
                label="无画师串",
                value="__none__",
                description="不拼接任何画师串",
                emoji="🚫",
                default=session.artist_prefix_mode == "none",
            ),
        ]

        def _append_option(preset: dict, scope: str):
            if len(options) >= 25:
                return

            artist_string = preset.get("artist_string", "")
            preview = artist_string[:80]
            if len(artist_string) > 80:
                preview += "..."

            preset_id = preset.get("id")
            key = f"{scope}:{preset_id}" if preset_id is not None else f"{scope}:{preset.get('name', '')}"
            base_name = preset.get("name", "未命名预设")
            label_prefix = "👤" if scope == "user" else "🛡️"
            label = f"{label_prefix} {base_name}"
            display_name = base_name if scope == "user" else f"管理员/{base_name}"

            payload = {
                "scope": scope,
                "name": base_name,
                "display_name": display_name,
                "artist_string": artist_string,
                "negative_prompt": preset.get("negative_prompt", ""),
            }
            self.preset_map[key] = payload

            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=key[:100],
                    description=preview[:100],
                    default=(
                        session.artist_prefix_mode == "preset"
                        and session.preset_name == display_name
                    ),
                )
            )

        # 先放用户预设，再放管理员预设
        for p in user_presets:
            _append_option(p, "user")
            if len(options) >= 25:
                break

        if len(options) < 25:
            for p in admin_presets:
                _append_option(p, "admin")
                if len(options) >= 25:
                    break

        select = discord.ui.Select(placeholder="选择画师串模式/预设", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]

        if value == "__default__":
            self.session.artist_prefix_mode = "default"
            self.session.preset_name = None
            self.session.preset_artist_string = ""
            await chat_db_manager.set_novelai_active_preset_state(interaction.user.id, "default")
            msg = "已切换为系统默认画师串"
        elif value == "__none__":
            self.session.artist_prefix_mode = "none"
            self.session.preset_name = None
            self.session.preset_artist_string = ""
            await chat_db_manager.set_novelai_active_preset_state(interaction.user.id, "none")
            msg = "已切换为无画师串模式"
        else:
            preset = self.preset_map.get(value)
            if preset:
                scope = preset.get("scope", "user")
                display_name = preset.get("display_name") or (preset.get("name") or "未命名预设")

                self.session.artist_prefix_mode = "preset"
                self.session.preset_name = display_name
                self.session.preset_artist_string = preset.get("artist_string", "")
                preset_negative = str(preset.get("negative_prompt") or "").strip()
                self.session.negative_prompt = preset_negative

                await chat_db_manager.set_novelai_active_preset_state(
                    interaction.user.id,
                    "preset",
                    display_name,
                )

                scope_text = "管理员" if scope == "admin" else "用户"
                msg = f"已选择{scope_text}预设: {display_name}"
            else:
                msg = "预设不存在或已失效"

        await interaction.response.send_message(msg, ephemeral=True)



# ================================================================
# 模态框：随机种子
# ================================================================

class SeedModal(discord.ui.Modal, title="设置随机种子"):
    seed_input = discord.ui.TextInput(
        label="种子 (留空或填0表示随机)",
        placeholder="0",
        required=False,
        max_length=12,
    )

    def __init__(self, session: NovelAISession):
        super().__init__()
        self.session = session
        if session.seed is not None:
            self.seed_input.default = str(session.seed)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.seed_input.value.strip()
        if not value or value == "0":
            self.session.seed = None
            msg = "种子已设为随机"
        else:
            try:
                seed = int(value)
                self.session.seed = max(0, min(4294967295, seed))
                msg = f"种子已设为 {self.session.seed}"
            except ValueError:
                return await interaction.response.send_message("种子必须是整数。", ephemeral=True)

        embed = NovelAIDrawPanel(self.session, interaction.user.id).build_panel_embed()
        try:
            await interaction.response.edit_message(embed=embed)
        except Exception:
            await interaction.response.send_message(msg, ephemeral=True)


# ================================================================
# 模态框：保存预设
# ================================================================

class SavePresetModal(discord.ui.Modal, title="保存画师串预设"):
    preset_name_input = discord.ui.TextInput(
        label="预设名称",
        placeholder="例如: 赛博朋克风格",
        required=True,
        max_length=50,
    )
    artist_string_input = discord.ui.TextInput(
        label="画师串 (英文Tag)",
        style=discord.TextStyle.paragraph,
        placeholder="artist:xxx, style:xxx, ...",
        required=True,
        max_length=2000,
    )
    negative_prompt_input = discord.ui.TextInput(
        label="负面提示词 (可选)",
        style=discord.TextStyle.paragraph,
        placeholder="留空则不设置自定义负面提示词",
        required=False,
        max_length=2000,
    )

    def __init__(self, user_id: int):
        super().__init__()
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction):
        name = self.preset_name_input.value.strip()
        artist_string = self.artist_string_input.value.strip()
        negative_prompt = self.negative_prompt_input.value.strip()

        if not name or not artist_string:
            return await interaction.response.send_message(
                "预设名称和画师串不能为空。", ephemeral=True
            )

        # 检查预设数量上限
        existing = await chat_db_manager.get_novelai_presets(self.user_id)
        existing_names = [p["name"] for p in existing]
        if len(existing) >= 25 and name not in existing_names:
            return await interaction.response.send_message(
                f"已达预设上限（25个）。请先删除旧预设再保存。", ephemeral=True
            )

        success = await chat_db_manager.save_novelai_preset(
            user_id=self.user_id,
            name=name,
            artist_string=artist_string,
            negative_prompt=negative_prompt,
        )

        if success:
            embed = discord.Embed(
                title="预设已保存",
                description=f"预设名称: **{name}**",
                color=0x2ECC71,
            )
            embed.add_field(
                name="画师串",
                value=f"```\n{artist_string[:500]}\n```",
                inline=False,
            )
            if negative_prompt:
                embed.add_field(
                    name="负面提示词",
                    value=f"```\n{negative_prompt[:300]}\n```",
                    inline=False,
                )
            embed.set_footer(text="可在绘图面板中通过「画师串」按钮选择此预设")
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message("保存预设失败，请稍后重试。", ephemeral=True)


# ================================================================
# 预设管理视图 (查看 / 删除)
# ================================================================

class PresetManageView(discord.ui.View):
    """展示预设列表，支持查看详情和删除"""

    def __init__(
        self,
        session: NovelAISession,
        parent_panel: NovelAIDrawPanel,
        presets: list,
        user_id: int,
    ):
        super().__init__(timeout=120)
        self.session = session
        self.parent_panel = parent_panel
        self.presets = presets
        self.user_id = user_id

        # 构建选择菜单
        options = []
        for p in presets[:25]:
            preview = p["artist_string"][:80]
            if len(p["artist_string"]) > 80:
                preview += "..."
            options.append(
                discord.SelectOption(
                    label=p["name"][:100],
                    value=p["name"][:100],
                    description=preview[:100],
                )
            )

        select = discord.ui.Select(
            placeholder=f"选择预设查看详情 ({len(presets)}/25)",
            options=options,
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        """选中预设时显示详情和操作按钮"""
        name = interaction.data["values"][0]
        preset = await chat_db_manager.get_novelai_preset(self.user_id, name)

        if not preset:
            return await interaction.response.send_message(
                f"预设「{name}」不存在。", ephemeral=True
            )

        embed = discord.Embed(
            title=f"预设详情: {preset['name']}",
            color=0x9B59B6,
        )
        embed.add_field(
            name="画师串",
            value=f"```\n{preset['artist_string'][:1000]}\n```",
            inline=False,
        )
        if preset.get("negative_prompt"):
            embed.add_field(
                name="负面提示词",
                value=f"```\n{preset['negative_prompt'][:500]}\n```",
                inline=False,
            )
        embed.set_footer(text=f"创建时间: {preset.get('created_at', '未知')}")

        # 操作按钮视图
        action_view = PresetActionView(
            session=self.session,
            parent_panel=self.parent_panel,
            preset=preset,
            user_id=self.user_id,
        )

        await interaction.response.send_message(
            embed=embed, view=action_view, ephemeral=True
        )


# ================================================================
# 斜杠命令 NovelAI 生成结果的交互按钮
# ================================================================


class SlashEditPromptModal(discord.ui.Modal, title="修改提示词"):
    """斜杠命令生成结果 - 修改提示词弹窗"""

    prompt_input = discord.ui.TextInput(
        label="正面提示词 (Danbooru Tag)",
        style=discord.TextStyle.paragraph,
        placeholder="masterpiece, best quality, 1girl, ...",
        required=True,
        max_length=4000,
    )
    negative_input = discord.ui.TextInput(
        label="负面提示词（可选）",
        style=discord.TextStyle.paragraph,
        placeholder="lowres, bad anatomy, ...",
        required=False,
        max_length=2000,
    )

    def __init__(
        self,
        current_prompt: str,
        current_negative: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: int,
        cost: int,
        current_artist_string: Optional[str] = None,
    ):
        super().__init__()
        self.prompt_input.default = current_prompt[:4000]
        if current_negative:
            self.negative_input.default = current_negative[:2000]
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost
        normalized_artist = str(current_artist_string or "").strip()
        self._artist_string = normalized_artist or None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            new_prompt = self.prompt_input.value.strip()
            new_negative = self.negative_input.value.strip() if self.negative_input.value else None

            if not new_prompt:
                await interaction.followup.send("提示词不能为空！", ephemeral=True)
                return

            await _slash_regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=new_negative,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
            )
        except Exception as e:
            log.error(f"斜杠命令修改提示词重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class AIRewriteDescriptionModal(discord.ui.Modal, title="AI 重写提示词"):
    """用户输入描述，让 AI 根据描述重新生成 prompt"""

    description_input = discord.ui.TextInput(
        label="描述你想要的变化",
        style=discord.TextStyle.paragraph,
        placeholder="例如：换成夜晚的场景，加上星空和月亮\n或：改为更动感的姿势，添加战斗元素\n留空则自动优化当前提示词",
        required=False,
        max_length=1000,
    )

    def __init__(
        self,
        current_prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: int,
        cost: int,
        artist_string: Optional[str] = None,
    ):
        super().__init__()
        self._current_prompt = current_prompt
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost
        normalized_artist = str(artist_string or "").strip()
        self._artist_string = normalized_artist or None

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            description = self.description_input.value.strip() if self.description_input.value else "在不改变原始设定与用户意图的前提下，细化画面细节、光影和层次"

            # 调用 AI 重写 prompt
            from src.chat.services.gemini_service import gemini_service

            llm_overrides = _get_novelai_prompt_llm_overrides()
            rewrite_messages = get_rewrite_messages(
                prompt=self._current_prompt,
                description=description,
            )
            new_tags = await gemini_service.generate_simple_response(
                prompt="",  # messages 模式下 prompt 被忽略
                generation_config={
                    "temperature": 0.45,
                    "max_output_tokens": NOVELAI_PROMPT_MAX_OUTPUT_TOKENS,
                },
                messages=rewrite_messages,
                model_name=llm_overrides["model_name"],
                api_url=llm_overrides["api_url"],
                api_key=llm_overrides["api_key"],
                api_format=llm_overrides["api_format"],
            )

            if not new_tags or not new_tags.strip():
                await interaction.followup.send("AI 重写失败，请稍后重试。", ephemeral=True)
                return

            new_prompt = clamp_danbooru_tags(new_tags, max_tags=90)
            log.info(f"AI 重写 prompt 成功（标签数≤90）: {new_prompt[:100]}...")

            await _slash_regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
                title_suffix="（AI 重写）",
            )
        except Exception as e:
            log.error(f"AI 重写 prompt 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"AI 重写失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class SlashGenerateVideoModal(discord.ui.Modal, title="生成视频"):
    """从当前生图结果生成视频的描述输入框。"""

    description_input = discord.ui.TextInput(
        label="描述你的想法（可选）",
        style=discord.TextStyle.paragraph,
        placeholder="例如：镜头缓慢推进，角色转头微笑；留空则 AI 自由发挥",
        required=False,
        max_length=1000,
    )

    def __init__(self, image_prompt: str):
        super().__init__()
        self._image_prompt = image_prompt

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        try:
            user_idea = self.description_input.value.strip() if self.description_input.value else ""
            await _generate_video_from_image_interaction(
                interaction=interaction,
                image_prompt=self._image_prompt,
                user_idea=user_idea,
            )
        except Exception as e:
            log.error(f"从生图结果生成视频失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"生成视频失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class SlashNovelAIResultView(discord.ui.View):
    """斜杠命令 /draw 生成结果的交互按钮（重新生成/修改提示词/切换到Imagen/AI重写prompt）"""

    def __init__(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: int,
        cost: int,
        artist_string: Optional[str] = None,
    ):
        super().__init__(timeout=600)  # 10 分钟超时
        normalized_artist = str(artist_string or "").strip()
        self._artist_string = normalized_artist or None

        normalized_prompt = str(prompt or "").strip()
        if self._artist_string:
            stripped_prompt = _strip_artist_tokens(normalized_prompt, [self._artist_string])
            self._prompt = stripped_prompt or normalized_prompt
        else:
            self._prompt = normalized_prompt

        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost

    @discord.ui.button(label="重新生成", style=discord.ButtonStyle.primary, row=0)
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """用相同 prompt 但新种子重新生成"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            await _slash_regenerate_novelai(
                interaction=interaction,
                prompt=self._prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                artist_string=self._artist_string,
            )
        except Exception as e:
            log.error(f"斜杠命令重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="修改提示词", style=discord.ButtonStyle.secondary, row=0)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """弹出 Modal 让用户编辑提示词"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = SlashEditPromptModal(
            current_prompt=self._prompt,
            current_negative=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
            current_artist_string=self._artist_string,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="切换到 Imagen", style=discord.ButtonStyle.success, row=0)
    async def switch_to_imagen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用 Gemini Imagen 重新生成"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            prompt_for_imagen = _compose_prompt_with_artist(self._prompt, self._artist_string)
            await _slash_regenerate_with_imagen(
                interaction=interaction,
                prompt=prompt_for_imagen,
                user_id=self._user_id,
                force_prompt_rewrite=True,
            )
        except Exception as e:
            log.error(f"斜杠命令切换到 Imagen 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="查看提示词", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def view_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """以 ephemeral 消息展示提示词（三段代码块）"""
        pages = _build_prompt_preview_pages(
            artist_string=self._artist_string,
            positive_prompt=self._prompt,
            negative_prompt=self._negative_prompt,
        )
        await interaction.response.send_message(pages[0], ephemeral=True)
        for page in pages[1:]:
            await interaction.followup.send(page, ephemeral=True)

    @discord.ui.button(label="AI 重写", style=discord.ButtonStyle.secondary, row=1)
    async def ai_rewrite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """让 AI 根据用户描述重新生成 prompt"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = AIRewriteDescriptionModal(
            current_prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
            artist_string=self._artist_string,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="生成视频", style=discord.ButtonStyle.secondary, row=1)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """基于当前结果图生成视频。"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        prompt_for_video = _compose_prompt_with_artist(self._prompt, self._artist_string)
        modal = SlashGenerateVideoModal(image_prompt=prompt_for_video)
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        """超时后禁用所有按钮"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


async def _slash_regenerate_novelai(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: Optional[str],
    width: int,
    height: int,
    steps: int,
    scale: float,
    sampler: str,
    preset_name: Optional[str],
    user_id: int,
    cost: int,
    artist_string: Optional[str] = None,
    title_suffix: str = "（重新生成）",
):
    """内部函数：斜杠命令 NovelAI 重新生成图片"""
    from src.chat.utils.database import chat_db_manager

    base_prompt = str(prompt or "").strip()
    normalized_artist = str(artist_string or "").strip()
    effective_artist_string = normalized_artist or None
    final_prompt = _compose_prompt_with_artist(base_prompt, effective_artist_string)

    if not final_prompt:
        await interaction.followup.send("提示词不能为空！", ephemeral=True)
        return

    # 成本实时读取数据库配置（避免旧消息按钮使用到历史成本）
    try:
        db_generation_cost = await chat_db_manager.get_global_setting("novelai_generation_cost")
        if db_generation_cost is not None:
            cost = int(db_generation_cost)
    except Exception as e:
        log.warning(f"斜杠重新生成读取实时成本配置失败，回退到当前值 {cost}: {e}")

    # 按钮实际操作者付费（管理员代点时扣管理员）
    billing_user_id = int(interaction.user.id)

    # 检查余额
    if cost > 0:
        balance = await coin_service.get_balance(billing_user_id)
        if balance < cost:
            await interaction.followup.send(
                f"月光币不足（需要 {cost}，当前 {balance}）",
                ephemeral=True,
            )
            return

    appearance_summary = await _build_prompt_summary_for_embed(
        prompt=final_prompt,
        negative_prompt=negative_prompt,
        artist_string=effective_artist_string,
        use_ai_conversion=False,
    )

    # 生成图片（新种子）
    result = await novelai_service.generate_image(
        prompt=final_prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        seed=None,
    )

    if result is None:
        await interaction.followup.send("NovelAI 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if cost > 0:
        try:
            await coin_service.remove_coins(
                billing_user_id, cost, f"NovelAI重新生图: {final_prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    # 构建 Embed（不显示提示词，通过按钮查看）
    embed = discord.Embed(
        title=f"NovelAI 图像生成{title_suffix}",
        color=0x9B59B6,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    # 生成信息（紧凑排列）
    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
    embed.add_field(
        name='画师串',
        value=_build_artist_name_for_embed(preset_name, effective_artist_string),
        inline=True,
    )
    embed.add_field(name="种子", value=str(result.seed), inline=True)
    embed.add_field(
        name="参数",
        value=f"{result.width}x{result.height} | {steps}步 | CFG {scale}",
        inline=True,
    )
    embed.add_field(
        name="主体外貌",
        value=appearance_summary,
        inline=False,
    )
    new_balance = await coin_service.get_balance(billing_user_id)
    embed.set_footer(
        text=f"消耗 {cost} 月光币 | 余额: {new_balance} | {sampler} | {model_name}"
    )

    image_file = discord.File(
        io.BytesIO(result.image_data),
        filename="novelai_generated.png",
        spoiler=True,
    )
    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

    prompt_without_artist = _strip_artist_tokens(final_prompt, [effective_artist_string])
    if not prompt_without_artist:
        prompt_without_artist = base_prompt or final_prompt

    # 创建新的交互按钮
    new_view = SlashNovelAIResultView(
        prompt=prompt_without_artist,
        artist_string=effective_artist_string,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        preset_name=preset_name,
        user_id=user_id,
        cost=cost,
    )

    await interaction.followup.send(embed=embed, file=image_file, view=new_view)
    log.info(f"斜杠命令 NovelAI 重新生成成功, 种子: {result.seed}")


class ImagenEditPromptModal(discord.ui.Modal, title="修改提示词重新生成"):
    """从 Imagen 切换结果修改提示词的模态框"""

    prompt_input = discord.ui.TextInput(
        label="提示词",
        style=discord.TextStyle.paragraph,
        placeholder="输入新的提示词...",
        max_length=2000,
        required=True,
    )

    def __init__(
        self,
        current_prompt: str,
        user_id: int,
        resolution: str = "default",
        content_rating: str = "nsfw",
    ):
        super().__init__()
        self.prompt_input.default = current_prompt
        self._user_id = user_id
        self._resolution = resolution
        self._content_rating = content_rating

    async def on_submit(self, interaction: discord.Interaction):
        new_prompt = self.prompt_input.value.strip()
        if not new_prompt:
            await interaction.response.send_message("提示词不能为空哦！", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)
        try:
            await _slash_regenerate_with_imagen(
                interaction=interaction,
                prompt=new_prompt,
                user_id=self._user_id,
                resolution=self._resolution,
                content_rating=self._content_rating,
            )
        except Exception as e:
            log.error(f"修改提示词 Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


def _build_imagen_model_options(current_resolution: str = "default", current_rating: str = "nsfw") -> list:
    """构建 Imagen 分辨率×内容分级选项列表"""
    options = []
    combinations = [
        ("default", "sfw", "标准 | SFW"),
        ("default", "nsfw", "标准 | NSFW"),
        ("2k", "sfw", "2K 高清 | SFW"),
        ("2k", "nsfw", "2K 高清 | NSFW"),
        ("4k", "sfw", "4K 超清 | SFW"),
        ("4k", "nsfw", "4K 超清 | NSFW"),
    ]
    for resolution, rating, label in combinations:
        value = f"{resolution}|{rating}"
        is_default = (resolution == current_resolution and rating == current_rating)
        options.append(
            discord.SelectOption(
                label=label,
                value=value,
                default=is_default,
                description=f"分辨率: {resolution.upper()}, 内容: {rating.upper()}"
            )
        )
    return options


class ImagenSwitchResultView(discord.ui.View):
    """从 NovelAI 切换到 Imagen 后的结果交互按钮"""

    def __init__(
        self,
        prompt: str,
        user_id: int,
        cost: int,
        resolution: str = "default",
        content_rating: str = "nsfw",
        timeout: float = 600,
    ):
        super().__init__(timeout=timeout)
        self._prompt = prompt
        self._user_id = user_id
        self._cost = cost
        self._resolution = resolution
        self._content_rating = content_rating

        # 添加模型选择下拉菜单
        model_options = _build_imagen_model_options(resolution, content_rating)
        if model_options:
            self._model_select = discord.ui.Select(
                placeholder="更换模型重新生成",
                options=model_options,
                min_values=1,
                max_values=1,
                row=1,
            )
            self._model_select.callback = self._on_model_select
            self.add_item(self._model_select)

    @discord.ui.button(label="重新生成", style=discord.ButtonStyle.primary, row=0)
    async def regenerate_imagen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用 Imagen 重新生成"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            await _slash_regenerate_with_imagen(
                interaction=interaction,
                prompt=self._prompt,
                user_id=self._user_id,
                resolution=self._resolution,
                content_rating=self._content_rating,
            )
        except Exception as e:
            log.error(f"Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="修改提示词", style=discord.ButtonStyle.secondary, row=0)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """弹出 Modal 让用户编辑提示词"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = ImagenEditPromptModal(
            current_prompt=self._prompt,
            user_id=self._user_id,
            resolution=self._resolution,
            content_rating=self._content_rating,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="切换到 NovelAI", style=discord.ButtonStyle.success, row=0)
    async def switch_to_novelai_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """切换回 NovelAI 重新生成"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            nai_prompt = await _convert_imagen_prompt_to_novelai_prompt(
                self._prompt,
                force_rewrite=True,
            )
            await _slash_regenerate_novelai(
                interaction=interaction,
                prompt=nai_prompt,
                negative_prompt=str(NOVELAI_CONFIG.get("DEFAULT_NEGATIVE_PROMPT", "") or "").strip() or None,
                width=int(NOVELAI_CONFIG.get("DEFAULT_WIDTH", 832)),
                height=int(NOVELAI_CONFIG.get("DEFAULT_HEIGHT", 1216)),
                steps=int(NOVELAI_CONFIG.get("DEFAULT_STEPS", 28)),
                scale=float(NOVELAI_CONFIG.get("DEFAULT_SCALE", 5.0)),
                sampler=str(NOVELAI_CONFIG.get("DEFAULT_SAMPLER", "k_euler_ancestral")),
                preset_name=None,
                user_id=self._user_id,
                cost=int(NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5)),
                title_suffix="（从 Imagen 切换）",
            )
            log.info("已从 Imagen 切换回 NovelAI 生成图片")
        except Exception as e:
            log.error(f"切换到 NovelAI 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="生成视频", style=discord.ButtonStyle.secondary, row=0)
    async def generate_video_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """基于当前结果图生成视频。"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = SlashGenerateVideoModal(image_prompt=self._prompt)
        await interaction.response.send_modal(modal)

    async def _on_model_select(self, interaction: discord.Interaction):
        """处理模型选择下拉菜单的回调"""
        if interaction.user.id != self._user_id:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)

        selected_value = self._model_select.values[0]  # 格式: "resolution|rating"
        resolution, rating = selected_value.split("|")

        try:
            await _slash_regenerate_with_imagen(
                interaction=interaction,
                prompt=self._prompt,
                user_id=self._user_id,
                resolution=resolution,
                content_rating=rating,
            )
        except Exception as e:
            log.error(f"更换模型 Imagen 重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    async def on_timeout(self):
        """超时后禁用所有按钮"""
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


async def _slash_regenerate_with_imagen(
    interaction: discord.Interaction,
    prompt: str,
    user_id: int,
    resolution: str = "default",
    content_rating: str = "nsfw",
    force_prompt_rewrite: bool = False,
):
    """内部函数：斜杠命令使用 Gemini Imagen 重新生成图片"""
    from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG

    cost_per_image = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1)

    if not gemini_imagen_service.is_available():
        await interaction.followup.send("Gemini Imagen 服务当前不可用。", ephemeral=True)
        return

    # 按钮实际操作者付费（管理员代点时扣管理员）
    billing_user_id = int(interaction.user.id)

    # 检查余额
    if cost_per_image > 0:
        balance = await coin_service.get_balance(billing_user_id)
        if balance < cost_per_image:
            await interaction.followup.send(
                f"月光币不足（需要 {cost_per_image}，当前 {balance}）",
                ephemeral=True,
            )
            return

    imagen_prompt = await _convert_tag_prompt_to_imagen_prompt(
        prompt,
        force_rewrite=force_prompt_rewrite,
    )
    result = await gemini_imagen_service.generate_single_image(
        prompt=imagen_prompt,
        negative_prompt=None,
        aspect_ratio="3:4",
        resolution=resolution,
        content_rating=content_rating,
    )

    if not result:
        await interaction.followup.send("Gemini Imagen 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if cost_per_image > 0:
        try:
            await coin_service.remove_coins(
                billing_user_id, cost_per_image, f"Imagen生图(切换): {imagen_prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    imagen_model_name = gemini_imagen_service._get_model_for_resolution(
        resolution=resolution, is_edit=False, content_rating=content_rating
    )

    # 构建 Embed
    embed = discord.Embed(
        title="Gemini Imagen 图片生成（从 NovelAI 切换）",
        color=0x4285F4,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    # 显示模型信息（提示词通过按钮查看）
    model_info = f"分辨率: {resolution.upper()}" if resolution != "default" else "标准分辨率"
    embed.set_footer(
        text=(
            f"消耗 {cost_per_image} 月光币 | 模型: {imagen_model_name} | "
            f"{model_info} | {content_rating.upper()} | 引擎: Gemini Imagen"
        )
    )

    image_file = discord.File(
        io.BytesIO(result),
        filename="imagen_generated.png",
        spoiler=True,
    )

    # 带交互按钮
    view = ImagenSwitchResultView(
        prompt=imagen_prompt,
        user_id=user_id,
        cost=cost_per_image,
        resolution=resolution,
        content_rating=content_rating,
    )

    msg = await interaction.followup.send(embed=embed, file=image_file, view=view)
    view.message = msg
    log.info(
        f"斜杠命令已切换到 Imagen 生成图片 (分辨率: {resolution}, 分级: {content_rating}, 模型: {imagen_model_name})"
    )


# ================================================================
# 预设操作视图
# ================================================================

class PresetActionView(discord.ui.View):
    """单个预设的操作按钮：应用到面板 / 删除"""

    def __init__(
        self,
        session: NovelAISession,
        parent_panel: NovelAIDrawPanel,
        preset: dict,
        user_id: int,
    ):
        super().__init__(timeout=60)
        self.session = session
        self.parent_panel = parent_panel
        self.preset = preset
        self.user_id = user_id

    @discord.ui.button(label="应用到面板", style=discord.ButtonStyle.primary, emoji="✅")
    async def btn_apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        """将预设应用到当前面板"""
        self.session.artist_prefix_mode = "preset"
        self.session.preset_name = self.preset["name"]
        self.session.preset_artist_string = self.preset["artist_string"]
        self.session.negative_prompt = str(self.preset.get("negative_prompt") or "").strip()

        await chat_db_manager.set_novelai_active_preset_state(
            self.user_id,
            "preset",
            self.preset["name"],
        )

        await interaction.response.send_message(
            f"已将预设「{self.preset['name']}」应用到面板！", ephemeral=True
        )

    @discord.ui.button(label="删除预设", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def btn_delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        """删除这个预设"""
        success = await chat_db_manager.delete_novelai_preset(
            self.user_id, self.preset["name"]
        )
        if success:
            if self.session.artist_prefix_mode == "preset" and self.session.preset_name == self.preset["name"]:
                self.session.artist_prefix_mode = "default"
                self.session.preset_name = None
                self.session.preset_artist_string = ""

            await interaction.response.send_message(
                f"已删除预设「{self.preset['name']}」。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "删除失败，请稍后重试。", ephemeral=True
            )
