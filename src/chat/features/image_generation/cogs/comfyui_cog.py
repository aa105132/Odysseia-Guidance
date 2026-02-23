# -*- coding: utf-8 -*-

import io
import logging
from typing import Any, Dict, Optional

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config import chat_config
from src.chat.features.image_generation.services.comfyui_service import comfyui_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)


def _coerce_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if value is None or str(value).strip() == '':
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None or str(value).strip() == '':
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


class ComfyUIUserConfigModal(discord.ui.Modal, title='ComfyUI 个人默认配置'):
    workflow_path_input = discord.ui.TextInput(
        label='个人工作流路径（可留空回退全局）',
        placeholder='例如 D:\\Downloads\\my_workflow.json',
        required=False,
        max_length=500,
        style=discord.TextStyle.short,
    )
    default_lora_input = discord.ui.TextInput(
        label='个人默认 LoRA（可留空）',
        placeholder='例如 my_lora.safetensors',
        required=False,
        max_length=200,
        style=discord.TextStyle.short,
    )

    def __init__(self, panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.panel_view = panel_view
        self.workflow_path_input.default = panel_view.user_workflow_path or ''
        self.default_lora_input.default = panel_view.user_default_lora or ''

    async def on_submit(self, interaction: discord.Interaction):
        workflow_path = str(self.workflow_path_input.value or '').strip()
        default_lora = str(self.default_lora_input.value or '').strip()

        success = await chat_db_manager.set_comfyui_user_settings(
            interaction.user.id,
            workflow_path=workflow_path,
            default_lora=default_lora,
        )
        if not success:
            await interaction.response.send_message('保存个人 ComfyUI 配置失败，请稍后重试。', ephemeral=True)
            return

        self.panel_view.user_workflow_path = workflow_path
        self.panel_view.user_default_lora = default_lora

        await interaction.response.send_message('已保存你的个人 ComfyUI 默认配置。', ephemeral=True)
        await self.panel_view.refresh_panel_message()


class ComfyUIGenerateModal(discord.ui.Modal, title='ComfyUI 快速生成'):
    prompt_input = discord.ui.TextInput(
        label='提示词',
        placeholder='请输入你想生成的内容',
        required=True,
        max_length=2000,
        style=discord.TextStyle.paragraph,
    )
    negative_input = discord.ui.TextInput(
        label='负面提示词',
        placeholder='可选',
        required=False,
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )
    resolution_input = discord.ui.TextInput(
        label='分辨率（widthxheight）',
        placeholder='例如 832x1216，留空用默认值',
        required=False,
        max_length=30,
        style=discord.TextStyle.short,
    )
    steps_cfg_input = discord.ui.TextInput(
        label='步数与 CFG（steps,cfg）',
        placeholder='例如 28,5.0，留空用默认值',
        required=False,
        max_length=40,
        style=discord.TextStyle.short,
    )
    extra_input = discord.ui.TextInput(
        label='额外参数（key=value, 逗号分隔）',
        placeholder='seed=1,sampler=euler,scheduler=normal,lora=xxx,workflow=D:\\a.json',
        required=False,
        max_length=600,
        style=discord.TextStyle.short,
    )

    def __init__(self, cog: 'ComfyUICog', panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.cog = cog
        self.panel_view = panel_view

    @staticmethod
    def _parse_resolution(text: str) -> Dict[str, Optional[int]]:
        raw = str(text or '').strip().lower().replace('×', 'x').replace(',', 'x')
        if not raw:
            return {'width': None, 'height': None}

        parts = [segment.strip() for segment in raw.split('x') if segment.strip()]
        if len(parts) != 2:
            return {'width': None, 'height': None}

        width = _coerce_int(parts[0], None)
        height = _coerce_int(parts[1], None)
        return {'width': width, 'height': height}

    @staticmethod
    def _parse_steps_cfg(text: str) -> Dict[str, Optional[Any]]:
        raw = str(text or '').strip().replace('，', ',')
        if not raw:
            return {'steps': None, 'cfg': None}

        parts = [segment.strip() for segment in raw.split(',') if segment.strip()]
        if len(parts) == 1:
            return {'steps': _coerce_int(parts[0], None), 'cfg': None}
        return {
            'steps': _coerce_int(parts[0], None),
            'cfg': _coerce_float(parts[1], None),
        }

    @staticmethod
    def _parse_extra(text: str) -> Dict[str, Any]:
        raw = str(text or '').strip().replace('，', ',')
        parsed: Dict[str, Any] = {}
        if not raw:
            return parsed

        for pair in raw.split(','):
            item = pair.strip()
            if not item or '=' not in item:
                continue
            key, value = item.split('=', 1)
            key_text = str(key or '').strip().lower()
            value_text = str(value or '').strip()
            if not key_text:
                continue
            parsed[key_text] = value_text
        return parsed

    async def on_submit(self, interaction: discord.Interaction):
        resolution = self._parse_resolution(self.resolution_input.value)
        steps_cfg = self._parse_steps_cfg(self.steps_cfg_input.value)
        extra = self._parse_extra(self.extra_input.value)

        payload: Dict[str, Any] = {
            'prompt': str(self.prompt_input.value or '').strip(),
            'negative_prompt': str(self.negative_input.value or '').strip(),
            'width': resolution.get('width'),
            'height': resolution.get('height'),
            'steps': steps_cfg.get('steps'),
            'cfg': steps_cfg.get('cfg'),
            'seed': _coerce_int(extra.get('seed'), None),
            'sampler': str(extra.get('sampler') or '').strip() or None,
            'scheduler': str(extra.get('scheduler') or '').strip() or None,
            'lora': str(extra.get('lora') or '').strip() or None,
            'lora_strength': _coerce_float(extra.get('lora_strength'), None),
            'workflow_path': str(extra.get('workflow') or '').strip() or None,
            'panel_user_workflow_path': self.panel_view.user_workflow_path,
            'panel_user_default_lora': self.panel_view.user_default_lora,
        }

        await self.cog.handle_panel_generation(interaction, payload)


class ComfyUIPanelView(discord.ui.View):
    def __init__(
        self,
        cog: 'ComfyUICog',
        user_id: int,
        user_workflow_path: str,
        user_default_lora: str,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = user_id
        self.user_workflow_path = user_workflow_path
        self.user_default_lora = user_default_lora
        self.panel_message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('这个面板是他人的 /comfy 会话。', ephemeral=True)
            return False
        return True

    async def refresh_panel_message(self) -> None:
        if not self.panel_message:
            return
        try:
            await self.panel_message.edit(embed=self.cog.build_panel_embed(self), view=self)
        except Exception:
            pass

    @discord.ui.button(label='输入参数并生成', style=discord.ButtonStyle.primary, emoji='🎨')
    async def btn_generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ComfyUIGenerateModal(self.cog, self))

    @discord.ui.button(label='保存个人默认配置', style=discord.ButtonStyle.secondary, emoji='⚙️')
    async def btn_save_user_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ComfyUIUserConfigModal(self))

    @discord.ui.button(label='清空个人配置', style=discord.ButtonStyle.danger, emoji='🗑️')
    async def btn_clear_user_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        success = await chat_db_manager.clear_comfyui_user_settings(interaction.user.id)
        if not success:
            await interaction.response.send_message('清空个人配置失败，请稍后重试。', ephemeral=True)
            return
        self.user_workflow_path = ''
        self.user_default_lora = ''
        await interaction.response.send_message('已清空你的个人 ComfyUI 默认配置。', ephemeral=True)
        await self.refresh_panel_message()


class ComfyUICog(commands.Cog):
    '''ComfyUI slash command panel.'''

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @staticmethod
    def _get_image_cost() -> int:
        try:
            return max(0, int(chat_config.COMFYUI_CONFIG.get('IMAGE_GENERATION_COST', 5)))
        except (TypeError, ValueError):
            return 5

    @staticmethod
    def build_panel_embed(view: ComfyUIPanelView) -> discord.Embed:
        embed = discord.Embed(title='ComfyUI 交互绘图面板', color=0x2B2D31)
        workflow_text = view.user_workflow_path or '未设置（使用 Dashboard 全局工作流）'
        lora_text = view.user_default_lora or '未设置（使用全局默认或手动输入）'
        embed.add_field(name='个人默认工作流', value=f'`{workflow_text[:900]}`', inline=False)
        embed.add_field(name='个人默认 LoRA', value=f'`{lora_text[:900]}`', inline=False)
        embed.add_field(
            name='使用说明',
            value='点击「输入参数并生成」后会弹窗，可填写 prompt、分辨率、steps/cfg、lora、workflow 等参数。',
            inline=False,
        )
        embed.set_footer(text='你可以在这里保存自己的 workflow 与 lora。')
        return embed

    async def _get_user_comfy_settings(self, user_id: int) -> Dict[str, Any]:
        try:
            return await chat_db_manager.get_comfyui_user_settings(user_id)
        except Exception as error:
            log.warning(f'读取用户 ComfyUI 设置失败: {error}')
            return {
                'workflow_path': '',
                'default_lora': '',
                '_from_user': False,
            }

    async def handle_panel_generation(self, interaction: discord.Interaction, payload: Dict[str, Any]) -> None:
        if not comfyui_service.is_server_ready():
            await interaction.response.send_message('ComfyUI 服务不可用，请先检查 Dashboard 服务地址。', ephemeral=True)
            return

        prompt = str(payload.get('prompt') or '').strip()
        if not prompt:
            await interaction.response.send_message('提示词不能为空。', ephemeral=True)
            return

        panel_user_workflow_path = str(payload.get('panel_user_workflow_path') or '').strip()
        panel_user_default_lora = str(payload.get('panel_user_default_lora') or '').strip()

        workflow_path = str(payload.get('workflow_path') or '').strip() or panel_user_workflow_path
        lora_value = payload.get('lora')
        lora = str(lora_value or '').strip() if lora_value is not None else panel_user_default_lora
        lora = lora or None

        if not workflow_path and comfyui_service.workflow_template is None:
            await interaction.response.send_message(
                '未找到可用工作流。请在 Dashboard 设置默认工作流，或先点「保存个人默认配置」填写个人工作流路径。',
                ephemeral=True,
            )
            return

        cost = self._get_image_cost()
        user_id = interaction.user.id
        balance = await coin_service.get_balance(user_id)
        if balance < cost:
            await interaction.response.send_message(
                f'你的月光币余额不足，生成一张图片需要 {cost}，当前余额 {balance}。',
                ephemeral=True,
            )
            return

        await interaction.response.defer(thinking=True, ephemeral=True)

        new_balance = await coin_service.remove_coins(user_id, cost, 'ComfyUI 交互面板生成图片')
        if new_balance is None:
            await interaction.followup.send('扣费失败，请稍后重试。', ephemeral=True)
            return

        try:
            image_bytes = await comfyui_service.generate_image(
                prompt=prompt,
                negative_prompt=str(payload.get('negative_prompt') or '').strip(),
                width=_coerce_int(payload.get('width'), None),
                height=_coerce_int(payload.get('height'), None),
                steps=_coerce_int(payload.get('steps'), None),
                cfg=_coerce_float(payload.get('cfg'), None),
                sampler=str(payload.get('sampler') or '').strip() or None,
                scheduler=str(payload.get('scheduler') or '').strip() or None,
                seed=_coerce_int(payload.get('seed'), None),
                lora=lora,
                lora_strength=_coerce_float(payload.get('lora_strength'), None),
                workflow_path=workflow_path or None,
            )
        except Exception as error:
            log.error(f'/comfy 面板执行异常: {error}', exc_info=True)
            image_bytes = None

        if not image_bytes:
            await coin_service.add_coins(user_id, cost, 'ComfyUI 生成失败返还')
            await interaction.followup.send('图片生成失败，已返还月光币。', ephemeral=True)
            return

        file = discord.File(io.BytesIO(image_bytes), filename='comfyui_image.png', spoiler=True)
        embed = discord.Embed(title='ComfyUI 图片生成', color=0x2B2D31)
        embed.set_author(
            name=interaction.user.display_name,
            icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
        )
        embed.add_field(name='提示词', value=f'```\n{prompt[:1016]}\n```', inline=False)

        footer_parts = [f'消耗 {cost} 月光币', f'余额 {new_balance}']
        steps_value = payload.get('steps')
        cfg_value = payload.get('cfg')
        width_value = payload.get('width')
        height_value = payload.get('height')

        if steps_value:
            footer_parts.append(f'steps={steps_value}')
        if cfg_value is not None:
            footer_parts.append(f'cfg={cfg_value}')
        if width_value and height_value:
            footer_parts.append(f'{width_value}x{height_value}')
        if lora:
            footer_parts.append(f'lora={lora}')
        embed.set_footer(text=' | '.join(footer_parts))

        target_channel = interaction.channel
        if target_channel is None:
            await coin_service.add_coins(user_id, cost, 'ComfyUI 频道不可用返还')
            await interaction.followup.send('当前频道不可用，无法发送图片。', ephemeral=True)
            return

        try:
            sent_message = await target_channel.send(embed=embed, file=file)
            await chat_db_manager.register_generated_image_message(
                message_id=sent_message.id,
                user_id=user_id,
                guild_id=sent_message.guild.id if sent_message.guild else None,
                channel_id=sent_message.channel.id,
            )
        except Exception as error:
            await coin_service.add_coins(user_id, cost, 'ComfyUI 发送失败返还')
            log.error(f'ComfyUI 面板发送图片失败: {error}', exc_info=True)
            await interaction.followup.send('图片生成成功但发送失败，已返还月光币。', ephemeral=True)
            return

        await interaction.followup.send('生成完成，图片已发送到当前频道。', ephemeral=True)

    @app_commands.command(name='comfy', description='ComfyUI 绘图面板（支持个人工作流与 LoRA）')
    async def comfy(self, interaction: discord.Interaction):
        comfy_enabled = bool(chat_config.COMFYUI_CONFIG.get('ENABLED', False))
        slash_enabled = bool(chat_config.COMFYUI_CONFIG.get('ENABLE_SLASH_COMMAND', True))
        if not comfy_enabled or not slash_enabled:
            await interaction.response.send_message('ComfyUI 命令当前已关闭。', ephemeral=True)
            return

        if not comfyui_service.is_server_ready():
            await interaction.response.send_message(
                'ComfyUI 服务当前不可用，请检查 Dashboard 的服务地址和开关设置。',
                ephemeral=True,
            )
            return

        user_settings = await self._get_user_comfy_settings(interaction.user.id)
        panel = ComfyUIPanelView(
            cog=self,
            user_id=interaction.user.id,
            user_workflow_path=str(user_settings.get('workflow_path') or '').strip(),
            user_default_lora=str(user_settings.get('default_lora') or '').strip(),
        )
        embed = self.build_panel_embed(panel)
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)
        panel.panel_message = await interaction.original_response()


async def setup(bot: commands.Bot):
    await bot.add_cog(ComfyUICog(bot))
