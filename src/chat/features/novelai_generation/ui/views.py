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
from dataclasses import dataclass, field
from typing import Optional, List

import discord

from src.chat.config.chat_config import NOVELAI_CONFIG
from src.chat.features.novelai_generation.services.novelai_service import novelai_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)

# 尺寸预设映射
SIZE_PRESETS = {
    "竖图": (832, 1216),
    "横图": (1216, 832),
    "正方形": (1024, 1024),
    "大竖图": (1024, 1536),
    "大横图": (1536, 1024),
    "手机壁纸": (768, 1344),
    "宽屏壁纸": (1344, 768),
}

# AI 描述转 Tag 的提示词
AI_TAG_GENERATION_PROMPT = """You are an expert at creating NovelAI image generation tags. Convert the following user description into a comma-separated list of high-quality English tags suitable for NovelAI Diffusion.

Rules:
- Include quality tags like: masterpiece, best quality, amazing quality, very aesthetic, absurdres
- Include artistic style, character details, pose, expression, clothing, background, lighting
- Use danbooru-style tags
- Output ONLY the comma-separated tags, no explanation, no numbering
- Keep the output concise but detailed (aim for 30-60 tags)

User description:
{description}

Tags:"""


@dataclass
class NovelAISession:
    """存储一次绘图会话的参数状态"""
    mode: str = "tag"  # "tag" 或 "ai_describe"
    scene_prompt: str = ""  # 场景/描述文本（AI模式用中文，Tag模式用英文Tag）
    negative_prompt: str = ""
    preset_name: Optional[str] = None
    preset_artist_string: str = ""
    width: int = 832
    height: int = 1216
    size_label: str = "竖图"
    sampler: str = "k_euler_ancestral"
    steps: int = 28
    scale: float = 5.0
    seed: Optional[int] = None
    reference_image_b64: Optional[str] = None
    reference_strength: float = 0.6
    reference_info_extracted: float = 1.0


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
        artist_display = self.session.preset_name if self.session.preset_name else "默认"
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
        presets = await chat_db_manager.get_novelai_presets(self.user_id)
        if not presets:
            return await interaction.response.send_message(
                "你还没有保存预设。点击下方「保存预设」按钮来创建一个！",
                ephemeral=True,
            )
        view = PresetSelectView(self.session, self, presets)
        await interaction.response.send_message("选择一个画师串预设:", view=view, ephemeral=True)

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
        self.session.preset_name = None
        self.session.preset_artist_string = ""
        self.session.width = 832
        self.session.height = 1216
        self.session.size_label = "竖图"
        self.session.sampler = "k_euler_ancestral"
        self.session.steps = 28
        self.session.scale = 5.0
        self.session.seed = None
        self.session.reference_image_b64 = None
        self.session.reference_strength = 0.6
        self.session.reference_info_extracted = 1.0
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
            # === 根据模式构建最终提示词 ===
            final_prompt = await self._build_final_prompt()

            if not final_prompt:
                return await interaction.followup.send(
                    "提示词生成失败，请检查输入或稍后重试。",
                    ephemeral=True,
                )

            # 负面提示词
            negative = self.session.negative_prompt if self.session.negative_prompt else None

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

            # 构建结果 Embed
            embed = discord.Embed(title="🎨 NovelAI 图像生成", color=0x2B2D31)
            embed.set_author(
                name=interaction.user.display_name,
                icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
            )
            embed.add_field(
                name="提示词",
                value=f"```\n{final_prompt[:1016]}\n```",
                inline=False,
            )
            if self.session.preset_name:
                embed.add_field(name="预设", value=self.session.preset_name, inline=True)
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

            model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
            embed.set_footer(
                text=(
                    f"消耗 {cost} 月光币 | 余额: {new_balance} | "
                    f"尺寸: {result.width}x{result.height} | "
                    f"种子: {result.seed} | 模型: {model_name}"
                )
            )

            image_file = discord.File(
                io.BytesIO(result.image_data),
                filename="novelai_generated.png",
                spoiler=True,
            )
            embed.set_image(url="attachment://SPOILER_novelai_generated.png")

            await interaction.followup.send(embed=embed, file=image_file)

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

        # 添加画师串前缀
        if self.session.preset_artist_string:
            parts.append(self.session.preset_artist_string)

        if self.session.mode == "ai_describe" and self.session.scene_prompt:
            # AI 描述模式：调用 AI 将中文描述转换为英文 Tag
            try:
                from src.chat.services.gemini_service import gemini_service

                prompt = AI_TAG_GENERATION_PROMPT.format(description=self.session.scene_prompt)
                tags = await gemini_service.generate_simple_response(
                    prompt=prompt,
                    generation_config={
                        "temperature": 0.7,
                        "max_output_tokens": 1000,
                    },
                )
                if tags:
                    tags = tags.strip().strip('"').strip("'")
                    parts.append(tags)
                    log.info(f"AI描述模式生成Tag: {tags[:100]}...")
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
        label="步数 (1-50)",
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
            self.session.steps = max(1, min(50, steps))
        except ValueError:
            return await interaction.response.send_message("步数必须是整数。", ephemeral=True)
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
        await interaction.response.send_message(
            f"采样器已设置为 {value}",
            ephemeral=True,
        )


# ================================================================
# 预设选择视图
# ================================================================

class PresetSelectView(discord.ui.View):
    def __init__(self, session: NovelAISession, parent_panel: NovelAIDrawPanel, presets: list):
        super().__init__(timeout=60)
        self.session = session
        self.parent_panel = parent_panel

        options = [
            discord.SelectOption(label="清除预设", value="__clear__", description="不使用任何预设", emoji="🗑️")
        ]
        for p in presets[:24]:
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

        select = discord.ui.Select(placeholder="选择画师串预设", options=options)
        select.callback = self.on_select
        self.add_item(select)

    async def on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]

        if value == "__clear__":
            self.session.preset_name = None
            self.session.preset_artist_string = ""
            msg = "已清除预设"
        else:
            preset = await chat_db_manager.get_novelai_preset(interaction.user.id, value)
            if preset:
                self.session.preset_name = preset["name"]
                self.session.preset_artist_string = preset["artist_string"]
                if preset.get("negative_prompt") and not self.session.negative_prompt:
                    self.session.negative_prompt = preset["negative_prompt"]
                msg = f"已选择预设: {preset['name']}"
            else:
                msg = "预设不存在"

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
        self.session.preset_name = self.preset["name"]
        self.session.preset_artist_string = self.preset["artist_string"]
        if self.preset.get("negative_prompt") and not self.session.negative_prompt:
            self.session.negative_prompt = self.preset["negative_prompt"]

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
            await interaction.response.send_message(
                f"已删除预设「{self.preset['name']}」。", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "删除失败，请稍后重试。", ephemeral=True
            )
