# -*- coding: utf-8 -*-

import asyncio
import io
import logging
import random
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import discord
from discord import app_commands
from discord.ext import commands

from src.chat.config import chat_config
from src.chat.features.image_generation.services.comfyui_service import comfyui_service
from src.chat.features.odysseia_coin.service.coin_service import coin_service
from src.chat.utils.database import chat_db_manager

log = logging.getLogger(__name__)

SAMPLER_PRESETS = [
    'euler',
    'euler_a',
    'heun',
    'dpmpp_2m',
    'dpmpp_2m_sde',
    'dpmpp_sde',
    'dpm_fast',
    'ddim',
    'lcm',
]

SCHEDULER_PRESETS = [
    'normal',
    'karras',
    'exponential',
    'sgm_uniform',
    'simple',
    'ddim_uniform',
    'beta',
]

SAMPLER_SCHEDULER_PRESETS = [
    ('euler', 'normal', 'Euler · Normal'),
    ('euler_a', 'normal', 'Euler A · Normal'),
    ('heun', 'normal', 'Heun · Normal'),
    ('dpmpp_2m', 'karras', 'DPM++ 2M · Karras'),
    ('dpmpp_2m_sde', 'karras', 'DPM++ 2M SDE · Karras'),
    ('dpmpp_sde', 'karras', 'DPM++ SDE · Karras'),
    ('ddim', 'ddim_uniform', 'DDIM · DDIM Uniform'),
    ('lcm', 'sgm_uniform', 'LCM · SGM Uniform'),
]

LORA_FILE_EXTENSIONS = {'.safetensors', '.ckpt', '.pt', '.pth', '.bin'}
WORKFLOW_MAX_SIZE_BYTES = 10 * 1024 * 1024
LORA_MAX_SIZE_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_USER_LORA_UPLOADS = 5


def _sanitize_filename(raw_name: str, fallback_name: str) -> str:
    base_name = Path(str(raw_name or '').strip()).name
    if not base_name:
        base_name = fallback_name
    safe_name = re.sub(r'[^0-9A-Za-z._\-]+', '_', base_name)
    safe_name = safe_name.strip('._')
    return safe_name or fallback_name


def _extract_first_url(text: str) -> Optional[str]:
    match = re.search(r'https?://\S+', str(text or '').strip())
    if not match:
        return None
    return match.group(0).rstrip(').,]}>')


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


def _count_lora_files_in_dir(lora_dir: Path) -> int:
    try:
        return len(
            [
                item
                for item in lora_dir.iterdir()
                if item.is_file() and item.suffix.lower() in LORA_FILE_EXTENSIONS
            ]
        )
    except Exception:
        return 0


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
    fixed_positive_input = discord.ui.TextInput(
        label='个人固定正面提示词（可留空）',
        placeholder='会在每次生成时自动拼接到正面提示词前面',
        required=False,
        max_length=1200,
        style=discord.TextStyle.paragraph,
    )
    fixed_negative_input = discord.ui.TextInput(
        label='个人固定负面提示词（可留空）',
        placeholder='会在每次生成时自动拼接到负面提示词前面',
        required=False,
        max_length=1200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.panel_view = panel_view
        self.workflow_path_input.default = panel_view.user_workflow_path or ''
        self.default_lora_input.default = panel_view.user_default_lora or ''
        self.fixed_positive_input.default = panel_view.user_fixed_positive_prompt or ''
        self.fixed_negative_input.default = panel_view.user_fixed_negative_prompt or ''

    async def on_submit(self, interaction: discord.Interaction):
        workflow_path = str(self.workflow_path_input.value or '').strip()
        default_lora = str(self.default_lora_input.value or '').strip()
        fixed_positive_prompt = str(self.fixed_positive_input.value or '').strip()
        fixed_negative_prompt = str(self.fixed_negative_input.value or '').strip()

        success = await chat_db_manager.set_comfyui_user_settings(
            interaction.user.id,
            workflow_path=workflow_path,
            default_lora=default_lora,
            fixed_positive_prompt=fixed_positive_prompt,
            fixed_negative_prompt=fixed_negative_prompt,
        )
        if not success:
            await interaction.response.send_message('保存个人 ComfyUI 配置失败，请稍后重试。', ephemeral=True)
            return

        self.panel_view.user_workflow_path = workflow_path
        self.panel_view.user_default_lora = default_lora
        self.panel_view.user_fixed_positive_prompt = fixed_positive_prompt
        self.panel_view.user_fixed_negative_prompt = fixed_negative_prompt

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
        placeholder='seed=1,lora=animeA:0.7|<wlr:animeB:0.8>,workflow=D:\\a.json',
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
            'sampler': str(extra.get('sampler') or '').strip() or self.panel_view.selected_sampler,
            'scheduler': str(extra.get('scheduler') or '').strip() or self.panel_view.selected_scheduler,
            'lora': str(extra.get('lora') or extra.get('loras') or '').strip() or None,
            'lora_strength': _coerce_float(extra.get('lora_strength'), None),
            'workflow_path': str(extra.get('workflow') or '').strip() or None,
            'panel_user_workflow_path': self.panel_view.user_workflow_path,
            'panel_user_default_lora': self.panel_view.user_default_lora,
            'panel_user_fixed_positive_prompt': self.panel_view.user_fixed_positive_prompt,
            'panel_user_fixed_negative_prompt': self.panel_view.user_fixed_negative_prompt,
        }

        await self.cog.handle_panel_generation(interaction, payload)


class ComfyPromptModal(discord.ui.Modal, title='ComfyUI 提示词设置'):
    prompt_input = discord.ui.TextInput(
        label='提示词',
        placeholder='输入你要生成的画面内容',
        required=True,
        max_length=2000,
        style=discord.TextStyle.paragraph,
    )
    negative_input = discord.ui.TextInput(
        label='负面提示词（可选）',
        placeholder='可留空',
        required=False,
        max_length=1000,
        style=discord.TextStyle.paragraph,
    )
    fixed_positive_input = discord.ui.TextInput(
        label='个人固定正面提示词（可留空）',
        placeholder='会在每次生成时自动拼接在正面提示词前面',
        required=False,
        max_length=1200,
        style=discord.TextStyle.paragraph,
    )
    fixed_negative_input = discord.ui.TextInput(
        label='个人固定负面提示词（可留空）',
        placeholder='会在每次生成时自动拼接在负面提示词前面',
        required=False,
        max_length=1200,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.panel_view = panel_view
        self.prompt_input.default = panel_view.prompt
        self.negative_input.default = panel_view.negative_prompt
        self.fixed_positive_input.default = panel_view.user_fixed_positive_prompt
        self.fixed_negative_input.default = panel_view.user_fixed_negative_prompt

    async def on_submit(self, interaction: discord.Interaction):
        self.panel_view.prompt = str(self.prompt_input.value or '').strip()
        self.panel_view.negative_prompt = str(self.negative_input.value or '').strip()
        self.panel_view.user_fixed_positive_prompt = str(self.fixed_positive_input.value or '').strip()
        self.panel_view.user_fixed_negative_prompt = str(self.fixed_negative_input.value or '').strip()

        save_ok = await self.panel_view.persist_user_settings(
            fixed_positive_prompt=self.panel_view.user_fixed_positive_prompt,
            fixed_negative_prompt=self.panel_view.user_fixed_negative_prompt,
        )

        if save_ok:
            await interaction.response.send_message('已更新提示词，并保存个人固定提示词。', ephemeral=True)
        else:
            await interaction.response.send_message('已更新提示词，但保存个人固定提示词失败。', ephemeral=True)
        await self.panel_view.refresh_panel_message()


class ComfyParamsModal(discord.ui.Modal, title='ComfyUI 参数设置'):
    resolution_input = discord.ui.TextInput(
        label='分辨率（widthxheight）',
        placeholder='例如 832x1216',
        required=False,
        max_length=40,
        style=discord.TextStyle.short,
    )
    steps_cfg_input = discord.ui.TextInput(
        label='步数与 CFG（steps,cfg）',
        placeholder='例如 28,5.0',
        required=False,
        max_length=50,
        style=discord.TextStyle.short,
    )
    seed_input = discord.ui.TextInput(
        label='Seed（-1 为随机）',
        placeholder='例如 -1 或 12345',
        required=False,
        max_length=30,
        style=discord.TextStyle.short,
    )

    def __init__(self, panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.panel_view = panel_view
        self.resolution_input.default = f'{panel_view.width}x{panel_view.height}'
        self.steps_cfg_input.default = f'{panel_view.steps},{panel_view.cfg}'
        self.seed_input.default = '' if panel_view.seed is None else str(panel_view.seed)

    async def on_submit(self, interaction: discord.Interaction):
        resolution = str(self.resolution_input.value or '').strip().lower().replace('×', 'x').replace(',', 'x')
        if resolution:
            parts = [segment.strip() for segment in resolution.split('x') if segment.strip()]
            if len(parts) == 2:
                width = _coerce_int(parts[0], None)
                height = _coerce_int(parts[1], None)
                if width and height:
                    self.panel_view.width = width
                    self.panel_view.height = height

        steps_cfg = str(self.steps_cfg_input.value or '').strip().replace('，', ',')
        if steps_cfg:
            items = [segment.strip() for segment in steps_cfg.split(',') if segment.strip()]
            if items:
                parsed_steps = _coerce_int(items[0], None)
                if parsed_steps is not None and parsed_steps > 0:
                    self.panel_view.steps = parsed_steps
            if len(items) >= 2:
                parsed_cfg = _coerce_float(items[1], None)
                if parsed_cfg is not None and parsed_cfg >= 0:
                    self.panel_view.cfg = parsed_cfg

        seed_text = str(self.seed_input.value or '').strip()
        self.panel_view.seed = _coerce_int(seed_text, None) if seed_text else None

        await interaction.response.send_message('已更新参数设置。', ephemeral=True)
        await self.panel_view.refresh_panel_message()


class ComfyLoraManualModal(discord.ui.Modal, title='ComfyUI LoRA 文本设置'):
    lora_text_input = discord.ui.TextInput(
        label='LoRA 列表（支持多项）',
        placeholder='nameA:0.8|<wlr:nameB:0.7>|nameC',
        required=False,
        max_length=800,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, panel_view: 'ComfyUIPanelView'):
        super().__init__()
        self.panel_view = panel_view
        self.lora_text_input.default = panel_view.lora_text

    async def on_submit(self, interaction: discord.Interaction):
        self.panel_view.lora_text = str(self.lora_text_input.value or '').strip()
        self.panel_view.user_default_lora = self.panel_view.lora_text
        await self.panel_view.persist_user_settings(default_lora=self.panel_view.user_default_lora)
        await interaction.response.send_message('已更新 LoRA 文本并保存为个人默认。', ephemeral=True)
        await self.panel_view.refresh_panel_message()


class ComfyWorkflowSelect(discord.ui.Select):
    def __init__(self, panel_view: 'ComfyUIPanelView'):
        self.panel_view = panel_view
        super().__init__(
            placeholder='切换工作流（全局/个人上传）',
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label='加载中...', value='__loading__')],
            row=1,
        )
        self.refresh_options()

    def refresh_options(self) -> None:
        options, value_map = self.panel_view.build_workflow_select_options()
        self.panel_view.workflow_value_map = value_map
        self.options = options
        self.disabled = len(options) == 1 and options[0].value == '__none__'

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == '__none__':
            await interaction.response.send_message('当前没有可用工作流，请先上传 JSON 工作流。', ephemeral=True)
            return

        if selected == '__global__':
            self.panel_view.workflow_path = self.panel_view.global_workflow_path
            self.panel_view.user_workflow_path = ''
            await self.panel_view.persist_user_settings(workflow_path='')
        else:
            target_path = self.panel_view.workflow_value_map.get(selected)
            if not target_path:
                await interaction.response.send_message('切换工作流失败：目标不存在。', ephemeral=True)
                return
            self.panel_view.workflow_path = target_path
            self.panel_view.user_workflow_path = target_path
            await self.panel_view.persist_user_settings(workflow_path=target_path)

        self.panel_view.refresh_selects()
        await interaction.response.edit_message(
            embed=self.panel_view.cog.build_panel_embed(self.panel_view),
            view=self.panel_view,
        )


class ComfyLoraSelect(discord.ui.Select):
    def __init__(self, panel_view: 'ComfyUIPanelView'):
        self.panel_view = panel_view
        super().__init__(
            placeholder='选择 LoRA（支持多选）',
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label='加载中...', value='__loading__')],
            row=2,
        )
        self.refresh_options()

    def refresh_options(self) -> None:
        options, value_map = self.panel_view.build_lora_select_options()
        self.panel_view.lora_value_map = value_map
        self.options = options
        self.max_values = max(1, min(len(options), 10))

    async def callback(self, interaction: discord.Interaction):
        selected_values = list(self.values)
        if '__none__' in selected_values:
            self.panel_view.lora_text = ''
        else:
            selected_tokens: List[str] = []
            for value in selected_values:
                token = self.panel_view.lora_value_map.get(value)
                if token:
                    selected_tokens.append(token)
            self.panel_view.lora_text = '|'.join(selected_tokens)

        self.panel_view.user_default_lora = self.panel_view.lora_text
        await self.panel_view.persist_user_settings(default_lora=self.panel_view.user_default_lora)

        self.panel_view.refresh_selects()
        await interaction.response.edit_message(
            embed=self.panel_view.cog.build_panel_embed(self.panel_view),
            view=self.panel_view,
        )


class ComfyModelSelect(discord.ui.Select):
    def __init__(self, panel_view: 'ComfyUIPanelView'):
        self.panel_view = panel_view
        super().__init__(
            placeholder='选择底模（Checkpoint）',
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label='加载中...', value='__loading__')],
            row=3,
        )
        self.refresh_options()

    def refresh_options(self) -> None:
        options, value_map = self.panel_view.build_model_select_options()
        self.panel_view.model_value_map = value_map
        self.options = options
        self.disabled = len(options) == 1 and options[0].value == '__none__'

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == '__none__':
            await interaction.response.send_message('当前未发现可选底模，可用 `/comfy model_name:xxx` 手动指定。', ephemeral=True)
            return

        if selected == '__default__':
            self.panel_view.selected_model_name = ''
        else:
            target_name = self.panel_view.model_value_map.get(selected)
            if not target_name:
                await interaction.response.send_message('切换底模失败：目标不存在。', ephemeral=True)
                return
            self.panel_view.selected_model_name = target_name

        self.panel_view.refresh_selects()
        await interaction.response.edit_message(
            embed=self.panel_view.cog.build_panel_embed(self.panel_view),
            view=self.panel_view,
        )


class ComfySamplingPresetSelect(discord.ui.Select):
    def __init__(self, panel_view: 'ComfyUIPanelView'):
        self.panel_view = panel_view
        super().__init__(
            placeholder='采样器/调度器预设（常用）',
            min_values=1,
            max_values=1,
            options=[discord.SelectOption(label='加载中...', value='__loading__')],
            row=4,
        )
        self.refresh_options()

    def refresh_options(self) -> None:
        current_pair = f'{self.panel_view.selected_sampler}|{self.panel_view.selected_scheduler}'
        options: list[discord.SelectOption] = []

        for sampler, scheduler, label in SAMPLER_SCHEDULER_PRESETS:
            value = f'{sampler}|{scheduler}'
            options.append(
                discord.SelectOption(
                    label=label,
                    value=value,
                    default=current_pair == value,
                    description=f'{sampler} + {scheduler}',
                )
            )

        custom_value = current_pair
        if custom_value and not any(option.value == custom_value for option in options):
            options.insert(
                0,
                discord.SelectOption(
                    label='当前自定义组合',
                    value=custom_value,
                    default=True,
                    description=f'{self.panel_view.selected_sampler} + {self.panel_view.selected_scheduler}',
                )
            )

        if not any(option.default for option in options) and options:
            options[0].default = True

        self.options = options or [discord.SelectOption(label='无可用预设', value='__none__', default=True)]
        self.disabled = len(self.options) == 1 and self.options[0].value == '__none__'

    async def callback(self, interaction: discord.Interaction):
        selected = self.values[0]
        if selected == '__none__' or '|' not in selected:
            await interaction.response.send_message('采样预设不可用。', ephemeral=True)
            return

        sampler, scheduler = selected.split('|', 1)
        sampler = str(sampler).strip().lower()
        scheduler = str(scheduler).strip().lower()
        if sampler:
            self.panel_view.selected_sampler = sampler
        if scheduler:
            self.panel_view.selected_scheduler = scheduler

        self.panel_view.refresh_selects()
        await interaction.response.edit_message(
            embed=self.panel_view.cog.build_panel_embed(self.panel_view),
            view=self.panel_view,
        )


class ComfyUIPanelView(discord.ui.View):
    def __init__(
        self,
        cog: 'ComfyUICog',
        user_id: int,
        user_workflow_path: str,
        user_default_lora: str,
        user_fixed_positive_prompt: str = '',
        user_fixed_negative_prompt: str = '',
        initial_model_name: str = '',
        available_models: Optional[list[str]] = None,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.user_id = user_id

        self.workflow_value_map: Dict[str, str] = {}
        self.lora_value_map: Dict[str, str] = {}
        self.model_value_map: Dict[str, str] = {}

        self.workflow_dir, self.lora_dir = self._ensure_user_asset_dirs()
        self.global_workflow_path = str(chat_config.COMFYUI_CONFIG.get('WORKFLOW_PATH') or '').strip()
        self.user_workflow_path = str(user_workflow_path or '').strip()
        self.user_default_lora = str(user_default_lora or '').strip()
        self.user_fixed_positive_prompt = str(user_fixed_positive_prompt or '').strip()
        self.user_fixed_negative_prompt = str(user_fixed_negative_prompt or '').strip()

        self.prompt = ''
        self.negative_prompt = ''
        self.width = _coerce_int(chat_config.COMFYUI_CONFIG.get('DEFAULT_WIDTH'), 832) or 832
        self.height = _coerce_int(chat_config.COMFYUI_CONFIG.get('DEFAULT_HEIGHT'), 1216) or 1216
        self.steps = _coerce_int(chat_config.COMFYUI_CONFIG.get('DEFAULT_STEPS'), 28) or 28
        self.cfg = _coerce_float(chat_config.COMFYUI_CONFIG.get('DEFAULT_CFG'), 5.0) or 5.0
        self.seed = _coerce_int(chat_config.COMFYUI_CONFIG.get('DEFAULT_SEED'), 12345)
        self.lora_text = self.user_default_lora

        default_sampler = str(chat_config.COMFYUI_CONFIG.get('DEFAULT_SAMPLER') or '').strip().lower()
        default_scheduler = str(chat_config.COMFYUI_CONFIG.get('DEFAULT_SCHEDULER') or '').strip().lower()
        self.selected_sampler = default_sampler if default_sampler in SAMPLER_PRESETS else SAMPLER_PRESETS[0]
        self.selected_scheduler = default_scheduler if default_scheduler in SCHEDULER_PRESETS else SCHEDULER_PRESETS[0]
        config_default_model_name = str(chat_config.COMFYUI_CONFIG.get('DEFAULT_MODEL_NAME') or '').strip()
        self.selected_model_name = str(initial_model_name or '').strip() or config_default_model_name
        self.available_models = self._normalize_model_names(available_models or [])
        if self.selected_model_name:
            selected_key = self.selected_model_name.lower()
            if selected_key not in {name.lower() for name in self.available_models}:
                self.available_models.insert(0, self.selected_model_name)

        self.workflow_path = self.user_workflow_path or self.global_workflow_path

        self.workflow_select = ComfyWorkflowSelect(self)
        self.lora_select = ComfyLoraSelect(self)
        self.model_select = ComfyModelSelect(self)
        self.sampling_preset_select = ComfySamplingPresetSelect(self)

        self.panel_message: Optional[discord.Message] = None
        self.add_item(self.workflow_select)
        self.add_item(self.lora_select)
        self.add_item(self.model_select)
        self.add_item(self.sampling_preset_select)
        self.refresh_selects()

    def _ensure_user_asset_dirs(self) -> tuple[Path, Path]:
        project_root = Path(__file__).resolve().parents[5]
        user_root = project_root / 'data' / 'comfyui' / 'users' / str(self.user_id)
        workflow_dir = user_root / 'workflows'
        lora_dir = user_root / 'loras'
        workflow_dir.mkdir(parents=True, exist_ok=True)
        lora_dir.mkdir(parents=True, exist_ok=True)
        return workflow_dir, lora_dir

    @staticmethod
    def _split_raw_lora_items(text: str) -> list[str]:
        normalized = str(text or '').strip().replace('，', ',').replace('；', ';').replace('\n', '|')
        if not normalized:
            return []
        return [segment.strip() for segment in re.split(r'[|;,]+', normalized) if segment.strip()]

    @staticmethod
    def _normalize_model_names(model_names: list[str]) -> list[str]:
        deduped_names: list[str] = []
        seen = set()
        for model_name in model_names:
            model_text = str(model_name or '').strip()
            if not model_text:
                continue
            model_key = model_text.lower()
            if model_key in seen:
                continue
            seen.add(model_key)
            deduped_names.append(model_text)
        return deduped_names

    def _list_uploaded_workflow_paths(self) -> list[str]:
        if not self.workflow_dir.exists():
            return []
        files = sorted(self.workflow_dir.glob('*.json'), key=lambda item: item.stat().st_mtime, reverse=True)
        return [str(item) for item in files]

    def _list_uploaded_lora_tokens(self) -> list[str]:
        if not self.lora_dir.exists():
            return []
        files = sorted(
            [item for item in self.lora_dir.iterdir() if item.is_file() and item.suffix.lower() in LORA_FILE_EXTENSIONS],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return [item.name for item in files]

    def build_workflow_select_options(self) -> tuple[list[discord.SelectOption], Dict[str, str]]:
        options: list[discord.SelectOption] = []
        value_map: Dict[str, str] = {}

        current_path = str(self.workflow_path or '').strip()

        if self.global_workflow_path:
            options.append(
                discord.SelectOption(
                    label=f'全局默认: {Path(self.global_workflow_path).name}'[:100],
                    value='__global__',
                    default=current_path == self.global_workflow_path,
                )
            )

        candidate_paths: list[str] = []
        if self.user_workflow_path:
            candidate_paths.append(self.user_workflow_path)
        candidate_paths.extend(self._list_uploaded_workflow_paths())
        if current_path and current_path not in candidate_paths and current_path != self.global_workflow_path:
            candidate_paths.insert(0, current_path)

        deduped_paths: list[str] = []
        seen = set()
        for path_text in candidate_paths:
            normalized_path = str(path_text or '').strip()
            if not normalized_path:
                continue
            key = normalized_path.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped_paths.append(normalized_path)

        for index, path_text in enumerate(deduped_paths[:24]):
            value = f'wf_{index}'
            value_map[value] = path_text
            prefix = '个人' if path_text == self.user_workflow_path else '上传'
            options.append(
                discord.SelectOption(
                    label=f'{prefix}: {Path(path_text).name}'[:100],
                    value=value,
                    description='点击切换到该工作流',
                    default=current_path == path_text,
                )
            )

        if not options:
            options = [discord.SelectOption(label='暂无可用工作流（请先上传）', value='__none__', default=True)]

        if not any(option.default for option in options):
            options[0].default = True

        return options, value_map

    def build_lora_select_options(self) -> tuple[list[discord.SelectOption], Dict[str, str]]:
        current_tokens = self._split_raw_lora_items(self.lora_text)
        uploaded_tokens = self._list_uploaded_lora_tokens()

        candidates = list(uploaded_tokens)
        for token in current_tokens:
            if token not in candidates:
                candidates.append(token)

        value_map: Dict[str, str] = {}
        options: list[discord.SelectOption] = [
            discord.SelectOption(
                label='不使用 LoRA',
                value='__none__',
                default=not current_tokens,
            )
        ]

        for index, token in enumerate(candidates[:24]):
            value = f'lora_{index}'
            value_map[value] = token
            token_label = token if len(token) <= 95 else f'{token[:92]}...'
            options.append(
                discord.SelectOption(
                    label=token_label,
                    value=value,
                    default=token in current_tokens,
                )
            )

        if not any(option.default for option in options):
            options[0].default = True

        return options, value_map

    def build_model_select_options(self) -> tuple[list[discord.SelectOption], Dict[str, str]]:
        current_model_name = str(self.selected_model_name or '').strip()
        candidate_model_names = list(self.available_models)
        if current_model_name and current_model_name.lower() not in {name.lower() for name in candidate_model_names}:
            candidate_model_names.insert(0, current_model_name)

        deduped_names = self._normalize_model_names(candidate_model_names)
        value_map: Dict[str, str] = {}
        options: list[discord.SelectOption] = [
            discord.SelectOption(
                label='使用工作流默认底模',
                value='__default__',
                default=not current_model_name,
            )
        ]

        for index, model_name in enumerate(deduped_names[:24]):
            option_value = f'model_{index}'
            value_map[option_value] = model_name
            options.append(
                discord.SelectOption(
                    label=model_name[:100],
                    value=option_value,
                    default=current_model_name.lower() == model_name.lower(),
                )
            )

        if len(options) == 1 and not deduped_names:
            options = [discord.SelectOption(label='未发现可选底模', value='__none__', default=True)]

        if not any(option.default for option in options):
            options[0].default = True

        return options, value_map

    def refresh_selects(self) -> None:
        self.workflow_select.refresh_options()
        self.lora_select.refresh_options()
        self.model_select.refresh_options()
        self.sampling_preset_select.refresh_options()

    async def persist_user_settings(
        self,
        workflow_path: Optional[str] = None,
        default_lora: Optional[str] = None,
        fixed_positive_prompt: Optional[str] = None,
        fixed_negative_prompt: Optional[str] = None,
    ) -> bool:
        try:
            return await chat_db_manager.set_comfyui_user_settings(
                self.user_id,
                workflow_path=workflow_path,
                default_lora=default_lora,
                fixed_positive_prompt=fixed_positive_prompt,
                fixed_negative_prompt=fixed_negative_prompt,
            )
        except Exception as error:
            log.warning(f'保存用户 ComfyUI 设置失败: user_id={self.user_id}, error={error}')
            return False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('这个面板是他人的 /comfy 会话。', ephemeral=True)
            return False
        return True

    async def refresh_panel_message(self) -> None:
        if not self.panel_message:
            return
        try:
            self.refresh_selects()
            await self.panel_message.edit(embed=self.cog.build_panel_embed(self), view=self)
        except Exception:
            pass

    async def _wait_user_message(self, interaction: discord.Interaction, timeout_seconds: int = 120) -> Optional[discord.Message]:
        channel = interaction.channel
        if channel is None:
            return None

        def _check(message: discord.Message) -> bool:
            return message.author.id == self.user_id and message.channel.id == channel.id

        try:
            return await self.cog.bot.wait_for('message', timeout=timeout_seconds, check=_check)
        except asyncio.TimeoutError:
            return None

    async def _save_uploaded_workflow(self, attachment: discord.Attachment) -> str:
        safe_filename = _sanitize_filename(str(attachment.filename or '').strip(), 'workflow.json')
        if not safe_filename.lower().endswith('.json'):
            safe_filename = f'{safe_filename}.json'

        attachment_size = int(getattr(attachment, 'size', 0) or 0)
        if attachment_size > WORKFLOW_MAX_SIZE_BYTES:
            raise ValueError('工作流文件不能超过 10MB。')

        content_bytes = await attachment.read()
        if len(content_bytes) > WORKFLOW_MAX_SIZE_BYTES:
            raise ValueError('工作流文件不能超过 10MB。')

        decoded_text: Optional[str] = None
        for encoding in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                decoded_text = content_bytes.decode(encoding)
                break
            except Exception:
                continue

        if decoded_text is None:
            raise ValueError('工作流文件编码无法识别，请使用 UTF-8 JSON 文件。')

        target_path = self.workflow_dir / safe_filename
        saved_path = comfyui_service.save_workflow_text(decoded_text, str(target_path))
        return str(saved_path)

    async def _save_uploaded_lora(self, attachment: discord.Attachment) -> str:
        safe_filename = _sanitize_filename(str(attachment.filename or '').strip(), 'uploaded_lora.safetensors')
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in LORA_FILE_EXTENSIONS:
            raise ValueError('LoRA 文件扩展名不受支持，请上传 .safetensors/.ckpt/.pt/.pth/.bin。')

        attachment_size = int(getattr(attachment, 'size', 0) or 0)
        if attachment_size > LORA_MAX_SIZE_BYTES:
            raise ValueError('LoRA 文件不能超过 100MB。')

        current_count = _count_lora_files_in_dir(self.lora_dir)
        max_count = self.cog._get_max_user_lora_uploads()
        target_path = self.lora_dir / safe_filename
        if current_count >= max_count and not target_path.exists():
            raise ValueError(f'每人最多上传 {max_count} 个 LoRA，请先删除不用的 LoRA。')

        content_bytes = await attachment.read()
        if len(content_bytes) > LORA_MAX_SIZE_BYTES:
            raise ValueError('LoRA 文件不能超过 100MB。')

        target_path.write_bytes(content_bytes)
        return target_path.name

    def _append_lora_token(self, token: str) -> None:
        token_text = str(token or '').strip()
        if not token_text:
            return
        current = self._split_raw_lora_items(self.lora_text)
        lower_set = {item.lower() for item in current}
        if token_text.lower() in lower_set:
            return
        current.append(token_text)
        self.lora_text = '|'.join(current)

    @discord.ui.button(label='设置提示词', style=discord.ButtonStyle.secondary, emoji='📝', row=0)
    async def btn_prompt(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ComfyPromptModal(self))

    @discord.ui.button(label='参数设置', style=discord.ButtonStyle.secondary, emoji='⚙️', row=0)
    async def btn_params(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ComfyParamsModal(self))

    @discord.ui.button(label='上传工作流', style=discord.ButtonStyle.secondary, emoji='📂', row=0)
    async def btn_upload_workflow(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            '请在 120 秒内发送一个 JSON 工作流附件到当前频道（发送后会自动读取并切换）。',
            ephemeral=True,
        )

        message = await self._wait_user_message(interaction, timeout_seconds=120)
        if message is None:
            await interaction.followup.send('等待上传超时，请重新点击「上传工作流」。', ephemeral=True)
            return

        try:
            if not message.attachments:
                await interaction.followup.send('未检测到附件，请重新上传 JSON 工作流文件。', ephemeral=True)
                return

            saved_path = await self._save_uploaded_workflow(message.attachments[0])
            self.workflow_path = saved_path
            self.user_workflow_path = saved_path
            await self.persist_user_settings(workflow_path=saved_path)

            await interaction.followup.send(f'工作流上传成功，已切换为 `{Path(saved_path).name}`。', ephemeral=True)
            await self.refresh_panel_message()
        except Exception as error:
            await interaction.followup.send(f'上传工作流失败：{error}', ephemeral=True)
        finally:
            try:
                await message.delete()
            except Exception:
                pass

    @discord.ui.button(label='上传 LoRA', style=discord.ButtonStyle.secondary, emoji='🧩', row=0)
    async def btn_upload_lora(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message(
            '请在 120 秒内发送 LoRA 附件，或发送 LoRA 下载链接，或直接发送 LoRA 文本（如 name:0.8|<wlr:xx:0.7>）。',
            ephemeral=True,
        )

        message = await self._wait_user_message(interaction, timeout_seconds=120)
        if message is None:
            await interaction.followup.send('等待上传超时，请重新点击「上传 LoRA」。', ephemeral=True)
            return

        try:
            uploaded_tokens: list[str] = []

            if message.attachments:
                uploaded_token = await self._save_uploaded_lora(message.attachments[0])
                if uploaded_token:
                    uploaded_tokens.append(uploaded_token)
            else:
                url = _extract_first_url(message.content)
                if url:
                    download_result = await comfyui_service.download_lora_from_url(url=url)
                    if not download_result.get('success'):
                        error_message = str(download_result.get('error') or '未知错误')
                        await interaction.followup.send(
                            f'LoRA 下载失败：{error_message}',
                            ephemeral=True,
                        )
                        return

                    url_path = urlparse(url).path
                    inferred_name = Path(url_path).name if url_path else ''
                    uploaded_tokens.append(_sanitize_filename(inferred_name, 'downloaded_lora.safetensors'))
                else:
                    raw_tokens = self._split_raw_lora_items(message.content)
                    if not raw_tokens:
                        await interaction.followup.send('未检测到附件、有效链接或 LoRA 文本。', ephemeral=True)
                        return
                    uploaded_tokens.extend(raw_tokens)

            for token in uploaded_tokens:
                self._append_lora_token(token)
            self.user_default_lora = self.lora_text
            await self.persist_user_settings(default_lora=self.user_default_lora)

            await interaction.followup.send('LoRA 已加入列表并保存为个人默认。', ephemeral=True)
            await self.refresh_panel_message()
        except Exception as error:
            await interaction.followup.send(f'上传 LoRA 失败：{error}', ephemeral=True)
        finally:
            try:
                await message.delete()
            except Exception:
                pass

    @discord.ui.button(label='开始绘制', style=discord.ButtonStyle.success, emoji='🖌️', row=0)
    async def btn_generate(self, interaction: discord.Interaction, button: discord.ui.Button):
        payload: Dict[str, Any] = {
            'prompt': self.prompt,
            'negative_prompt': self.negative_prompt,
            'width': self.width,
            'height': self.height,
            'steps': self.steps,
            'cfg': self.cfg,
            'seed': self.seed,
            'sampler': self.selected_sampler,
            'scheduler': self.selected_scheduler,
            'model_name': self.selected_model_name,
            'lora': self.lora_text,
            'lora_strength': _coerce_float(chat_config.COMFYUI_CONFIG.get('DEFAULT_LORA_STRENGTH'), 1.0),
            'workflow_path': self.workflow_path,
            'panel_user_workflow_path': self.user_workflow_path,
            'panel_user_default_lora': self.user_default_lora,
            'panel_user_fixed_positive_prompt': self.user_fixed_positive_prompt,
            'panel_user_fixed_negative_prompt': self.user_fixed_negative_prompt,
        }
        await self.cog.handle_panel_generation(interaction, payload)


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
    def _get_max_user_lora_uploads() -> int:
        try:
            configured_value = int(chat_config.COMFYUI_CONFIG.get('MAX_USER_LORA_UPLOADS', DEFAULT_MAX_USER_LORA_UPLOADS))
        except (TypeError, ValueError):
            configured_value = DEFAULT_MAX_USER_LORA_UPLOADS
        return max(1, configured_value)

    @staticmethod
    def build_panel_embed(view: ComfyUIPanelView) -> discord.Embed:
        embed = discord.Embed(
            title='🎨 ComfyUI 绘图面板',
            description='像 /draw 一样分步设置，最后点「开始绘制」。',
            color=0x2B2D31,
        )

        prompt_preview = view.prompt[:200] + ('...' if len(view.prompt) > 200 else '') if view.prompt else '（未设置）'
        negative_preview = (
            view.negative_prompt[:120] + ('...' if len(view.negative_prompt) > 120 else '')
            if view.negative_prompt
            else '（未设置）'
        )

        workflow_name = Path(view.workflow_path).name if view.workflow_path else '未设置（需上传或使用全局）'
        lora_preview = view.lora_text[:150] + ('...' if len(view.lora_text) > 150 else '') if view.lora_text else '不使用'
        seed_display = str(view.seed) if view.seed is not None else '随机'
        model_display = str(view.selected_model_name or '').strip() or '工作流默认'

        user_fixed_positive_preview = (
            view.user_fixed_positive_prompt[:120] + ('...' if len(view.user_fixed_positive_prompt) > 120 else '')
            if view.user_fixed_positive_prompt
            else '（未设置）'
        )
        user_fixed_negative_preview = (
            view.user_fixed_negative_prompt[:120] + ('...' if len(view.user_fixed_negative_prompt) > 120 else '')
            if view.user_fixed_negative_prompt
            else '（未设置）'
        )

        embed.add_field(name='📝 提示词', value=prompt_preview, inline=False)
        embed.add_field(name='🚫 负面提示词', value=negative_preview, inline=False)
        embed.add_field(name='🧷 个人固定正面', value=user_fixed_positive_preview, inline=False)
        embed.add_field(name='🧷 个人固定负面', value=user_fixed_negative_preview, inline=False)
        embed.add_field(name='🧩 当前工作流', value=workflow_name, inline=True)
        embed.add_field(name='🧱 当前底模', value=model_display[:100], inline=True)
        embed.add_field(name='🎨 当前 LoRA', value=lora_preview, inline=True)
        embed.add_field(name='📐 分辨率', value=f'{view.width}x{view.height}', inline=True)
        embed.add_field(name='🔢 步数', value=str(view.steps), inline=True)
        embed.add_field(name='📊 CFG', value=str(view.cfg), inline=True)
        embed.add_field(name='🎲 Seed', value=seed_display, inline=True)
        embed.add_field(name='⚙️ 采样器', value=view.selected_sampler, inline=True)
        embed.add_field(name='🧭 调度器', value=view.selected_scheduler, inline=True)
        embed.add_field(
            name='💡 上传与切换说明',
            value='点「上传工作流 / 上传 LoRA」后，在频道发送附件（或 LoRA 下载链接）即可自动导入并切换。',
            inline=False,
        )

        cost = ComfyUICog._get_image_cost()
        embed.set_footer(text=f'生成成本: {cost} 月光币 | 支持多 LoRA 与 <wlr:...> 语法')
        return embed

    @staticmethod
    def _parse_lora_tokens(raw_text: str, default_strength: float) -> list[str]:
        text = str(raw_text or '').strip()
        if not text:
            return []

        normalized = text.replace('，', ',').replace('；', ';').replace('\n', ',')
        parts = [segment.strip() for segment in re.split(r'[|;,]+', normalized) if segment.strip()]
        tokens: list[str] = []

        for part in parts:
            if part.startswith('<') and part.endswith('>') and ':' in part:
                tokens.append(part)
                continue

            lora_name = part
            lora_strength = default_strength
            if ':' in part:
                maybe_name, maybe_strength = part.rsplit(':', 1)
                parsed_strength = _coerce_float(maybe_strength, None)
                if maybe_name.strip() and parsed_strength is not None:
                    lora_name = maybe_name.strip()
                    lora_strength = parsed_strength

            lora_name = lora_name.strip()
            if not lora_name:
                continue
            tokens.append(f'<lora:{lora_name}:{lora_strength:.2f}>')

        deduped: list[str] = []
        seen = set()
        for token in tokens:
            token_key = token.lower()
            if token_key in seen:
                continue
            seen.add(token_key)
            deduped.append(token)
        return deduped

    @staticmethod
    def _append_lora_tokens(prompt: str, lora_tokens: list[str]) -> str:
        base_prompt = str(prompt or '').strip()
        if not lora_tokens:
            return base_prompt

        existing_lower = base_prompt.lower()
        append_tokens = [token for token in lora_tokens if token.lower() not in existing_lower]
        if not append_tokens:
            return base_prompt

        lora_text = ', '.join(append_tokens)
        if not base_prompt:
            return lora_text
        return f'{base_prompt}, {lora_text}'

    async def _get_user_comfy_settings(self, user_id: int) -> Dict[str, Any]:
        try:
            return await chat_db_manager.get_comfyui_user_settings(user_id)
        except Exception as error:
            log.warning(f'读取用户 ComfyUI 设置失败: {error}')
            return {
                'workflow_path': '',
                'default_lora': '',
                'fixed_positive_prompt': '',
                'fixed_negative_prompt': '',
                '_from_user': False,
            }

    @staticmethod
    def _resolve_user_workflow_dir(user_id: int) -> Path:
        project_root = Path(__file__).resolve().parents[5]
        workflow_dir = project_root / 'data' / 'comfyui' / 'users' / str(user_id) / 'workflows'
        workflow_dir.mkdir(parents=True, exist_ok=True)
        return workflow_dir

    async def _save_user_workflow_from_attachment(self, user_id: int, attachment: discord.Attachment) -> str:
        safe_filename = _sanitize_filename(str(attachment.filename or '').strip(), 'workflow.json')
        if not safe_filename.lower().endswith('.json'):
            safe_filename = f'{safe_filename}.json'

        attachment_size = int(getattr(attachment, 'size', 0) or 0)
        if attachment_size > WORKFLOW_MAX_SIZE_BYTES:
            raise ValueError('工作流文件不能超过 10MB。')

        content_bytes = await attachment.read()
        if len(content_bytes) > WORKFLOW_MAX_SIZE_BYTES:
            raise ValueError('工作流文件不能超过 10MB。')

        decoded_text: Optional[str] = None
        for encoding in ('utf-8-sig', 'utf-8', 'gbk'):
            try:
                decoded_text = content_bytes.decode(encoding)
                break
            except Exception:
                continue

        if decoded_text is None:
            raise ValueError('工作流文件编码无法识别，请使用 UTF-8 JSON 文件。')

        workflow_dir = self._resolve_user_workflow_dir(user_id)
        target_path = workflow_dir / safe_filename
        return comfyui_service.save_workflow_text(decoded_text, str(target_path))

    def _save_user_workflow_from_text(self, user_id: int, workflow_json: str, filename_hint: str = 'workflow_pasted.json') -> str:
        workflow_text = str(workflow_json or '').strip()
        if not workflow_text:
            raise ValueError('workflow_json 不能为空。')

        if len(workflow_text.encode('utf-8')) > WORKFLOW_MAX_SIZE_BYTES:
            raise ValueError('工作流文本不能超过 10MB。')

        safe_filename = _sanitize_filename(filename_hint, 'workflow_pasted.json')
        if not safe_filename.lower().endswith('.json'):
            safe_filename = f'{safe_filename}.json'

        workflow_dir = self._resolve_user_workflow_dir(user_id)
        target_path = workflow_dir / safe_filename
        return comfyui_service.save_workflow_text(workflow_text, str(target_path))

    @staticmethod
    def _resolve_user_lora_dir(user_id: int) -> Path:
        project_root = Path(__file__).resolve().parents[5]
        lora_dir = project_root / 'data' / 'comfyui' / 'users' / str(user_id) / 'loras'
        lora_dir.mkdir(parents=True, exist_ok=True)
        return lora_dir

    async def _save_user_lora_from_attachment(self, user_id: int, attachment: discord.Attachment) -> str:
        safe_filename = _sanitize_filename(str(attachment.filename or '').strip(), 'uploaded_lora.safetensors')
        suffix = Path(safe_filename).suffix.lower()
        if suffix not in LORA_FILE_EXTENSIONS:
            raise ValueError('LoRA 文件扩展名不受支持，请上传 .safetensors/.ckpt/.pt/.pth/.bin。')

        attachment_size = int(getattr(attachment, 'size', 0) or 0)
        if attachment_size > LORA_MAX_SIZE_BYTES:
            raise ValueError('LoRA 文件不能超过 100MB。')

        lora_dir = self._resolve_user_lora_dir(user_id)
        current_count = _count_lora_files_in_dir(lora_dir)
        max_count = self._get_max_user_lora_uploads()
        target_path = lora_dir / safe_filename

        if current_count >= max_count and not target_path.exists():
            raise ValueError(f'每人最多上传 {max_count} 个 LoRA，请先删除不用的 LoRA。')

        content_bytes = await attachment.read()
        if len(content_bytes) > LORA_MAX_SIZE_BYTES:
            raise ValueError('LoRA 文件不能超过 100MB。')

        target_path.write_bytes(content_bytes)
        return target_path.name

    @staticmethod
    def _split_lora_items(text: str) -> list[str]:
        normalized = str(text or '').strip().replace('，', ',').replace('；', ';').replace('\n', '|')
        if not normalized:
            return []
        return [segment.strip() for segment in re.split(r'[|;,]+', normalized) if segment.strip()]

    @classmethod
    def _append_lora_item(cls, existing_text: str, token: str) -> str:
        token_text = str(token or '').strip()
        current = cls._split_lora_items(existing_text)
        if not token_text:
            return '|'.join(current)

        lower_set = {item.lower() for item in current}
        if token_text.lower() in lower_set:
            return '|'.join(current)

        current.append(token_text)
        return '|'.join(current)

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
        panel_user_fixed_positive_prompt = str(payload.get('panel_user_fixed_positive_prompt') or '').strip()
        panel_user_fixed_negative_prompt = str(payload.get('panel_user_fixed_negative_prompt') or '').strip()

        workflow_path = str(payload.get('workflow_path') or '').strip() or panel_user_workflow_path
        lora_value = payload.get('lora')
        raw_lora_text = str(lora_value or '').strip() if lora_value is not None else panel_user_default_lora
        lora_strength = _coerce_float(payload.get('lora_strength'), 1.0) or 1.0
        lora_tokens = self._parse_lora_tokens(raw_lora_text, lora_strength)
        prompt_with_lora = self._append_lora_tokens(prompt, lora_tokens)

        seed = _coerce_int(payload.get('seed'), None)
        random_seed = False
        if seed == -1:
            seed = random.randint(0, 4294967295)
            random_seed = True

        if not workflow_path and comfyui_service.workflow_template is None:
            await interaction.response.send_message(
                '未找到可用工作流。请在 Dashboard 设置全局工作流，或先在面板里点击「上传工作流」。',
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
            service_lora_override = '' if lora_tokens else None
            image_bytes = await comfyui_service.generate_image(
                prompt=prompt_with_lora,
                negative_prompt=str(payload.get('negative_prompt') or '').strip(),
                width=_coerce_int(payload.get('width'), None),
                height=_coerce_int(payload.get('height'), None),
                steps=_coerce_int(payload.get('steps'), None),
                cfg=_coerce_float(payload.get('cfg'), None),
                sampler=str(payload.get('sampler') or '').strip() or None,
                scheduler=str(payload.get('scheduler') or '').strip() or None,
                model_name=str(payload.get('model_name') or '').strip() or None,
                seed=seed,
                lora=service_lora_override,
                lora_strength=None,
                workflow_path=workflow_path or None,
                user_fixed_positive_prompt=panel_user_fixed_positive_prompt,
                user_fixed_negative_prompt=panel_user_fixed_negative_prompt,
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
        embed.add_field(name='提示词', value=f'```\n{prompt_with_lora[:1016]}\n```', inline=False)

        footer_parts = [f'消耗 {cost} 月光币', f'余额 {new_balance}']
        steps_value = payload.get('steps')
        cfg_value = payload.get('cfg')
        width_value = payload.get('width')
        height_value = payload.get('height')
        model_value = str(payload.get('model_name') or '').strip()

        if steps_value:
            footer_parts.append(f'steps={steps_value}')
        if cfg_value is not None:
            footer_parts.append(f'cfg={cfg_value}')
        if width_value and height_value:
            footer_parts.append(f'{width_value}x{height_value}')
        if model_value:
            footer_parts.append(f'model={model_value}')
        if seed is not None:
            if random_seed:
                footer_parts.append(f'seed=随机({seed})')
            else:
                footer_parts.append(f'seed={seed}')
        if lora_tokens:
            lora_preview = ', '.join(lora_tokens)
            if len(lora_preview) > 80:
                lora_preview = f'{lora_preview[:80]}...'
            footer_parts.append(f'lora={lora_preview}')
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

    @app_commands.command(name='comfy', description='ComfyUI 绘图面板（支持工作流/LoRA/插件节点导入）')
    @app_commands.describe(
        workflow_file='可选：直接上传 ComfyUI 工作流 JSON 文件',
        workflow_json='可选：直接粘贴工作流 JSON 文本（长 JSON 建议用文件）',
        lora_file='可选：直接上传 LoRA 文件（支持 safetensors/ckpt/pt/pth/bin）',
        lora_url='可选：填写 LoRA 下载链接（提交到 ComfyUI-Manager）',
        custom_node_url='可选：填写插件节点 Git 链接（自动提交到 ComfyUI-Manager）',
        fixed_positive_prompt='可选：设置并保存你的个人固定正面提示词',
        fixed_negative_prompt='可选：设置并保存你的个人固定负面提示词',
        model_name='可选：直接指定本次面板默认底模（用于 %MODEL_NAME%）',
    )
    async def comfy(
        self,
        interaction: discord.Interaction,
        workflow_file: Optional[discord.Attachment] = None,
        workflow_json: Optional[str] = None,
        lora_file: Optional[discord.Attachment] = None,
        lora_url: Optional[str] = None,
        custom_node_url: Optional[str] = None,
        fixed_positive_prompt: Optional[str] = None,
        fixed_negative_prompt: Optional[str] = None,
        model_name: Optional[str] = None,
    ):
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

        user_id = interaction.user.id
        user_settings = await self._get_user_comfy_settings(user_id)
        imported_workflow_name: Optional[str] = None
        imported_lora_tokens: list[str] = []
        installed_custom_nodes: list[str] = []
        custom_node_warnings: list[str] = []
        workflow_path_update: Optional[str] = None
        default_lora_update: Optional[str] = None
        fixed_positive_prompt_update: Optional[str] = None
        fixed_negative_prompt_update: Optional[str] = None

        if workflow_file is not None or (workflow_json is not None and str(workflow_json).strip()):
            try:
                if workflow_file is not None:
                    saved_path = await self._save_user_workflow_from_attachment(user_id, workflow_file)
                else:
                    saved_path = self._save_user_workflow_from_text(user_id, str(workflow_json or ''), 'workflow_from_slash.json')

                workflow_path_update = saved_path
                imported_workflow_name = Path(saved_path).name
            except Exception as error:
                await interaction.response.send_message(f'导入工作流失败：{error}', ephemeral=True)
                return

        normalized_lora_url = str(lora_url or '').strip()
        if lora_file is not None or normalized_lora_url:
            try:
                current_lora_text = str(user_settings.get('default_lora') or '').strip()

                if lora_file is not None:
                    uploaded_lora = await self._save_user_lora_from_attachment(user_id, lora_file)
                    if uploaded_lora:
                        imported_lora_tokens.append(uploaded_lora)
                        current_lora_text = self._append_lora_item(current_lora_text, uploaded_lora)

                if normalized_lora_url:
                    download_result = await comfyui_service.download_lora_from_url(url=normalized_lora_url)
                    if not download_result.get('success'):
                        error_message = str(download_result.get('error') or '未知错误')
                        await interaction.response.send_message(f'导入 LoRA 失败：{error_message}', ephemeral=True)
                        return

                    url_path = urlparse(normalized_lora_url).path
                    inferred_name = Path(url_path).name if url_path else ''
                    downloaded_lora = _sanitize_filename(inferred_name, 'downloaded_lora.safetensors')
                    imported_lora_tokens.append(downloaded_lora)
                    current_lora_text = self._append_lora_item(current_lora_text, downloaded_lora)

                default_lora_update = current_lora_text
            except Exception as error:
                await interaction.response.send_message(f'导入 LoRA 失败：{error}', ephemeral=True)
                return

        if fixed_positive_prompt is not None:
            fixed_positive_prompt_update = str(fixed_positive_prompt or '').strip()
        if fixed_negative_prompt is not None:
            fixed_negative_prompt_update = str(fixed_negative_prompt or '').strip()

        normalized_custom_node_url = str(custom_node_url or '').strip()
        if normalized_custom_node_url:
            try:
                install_result = await comfyui_service.install_custom_node_from_url(normalized_custom_node_url)
                if not install_result.get('success'):
                    error_message = str(install_result.get('error') or '未知错误')
                    await interaction.response.send_message(f'安装插件节点失败：{error_message}', ephemeral=True)
                    return

                repo_path = urlparse(normalized_custom_node_url).path
                inferred_repo = Path(repo_path).name if repo_path else ''
                if inferred_repo.lower().endswith('.git'):
                    inferred_repo = inferred_repo[:-4]
                display_name = _sanitize_filename(inferred_repo, 'custom_node')
                installed_custom_nodes.append(display_name)

                queue_warning = str(install_result.get('queue_start_warning') or '').strip()
                if queue_warning:
                    custom_node_warnings.append(queue_warning)
            except Exception as error:
                await interaction.response.send_message(f'安装插件节点失败：{error}', ephemeral=True)
                return

        if (
            workflow_path_update is not None
            or default_lora_update is not None
            or fixed_positive_prompt_update is not None
            or fixed_negative_prompt_update is not None
        ):
            save_ok = await chat_db_manager.set_comfyui_user_settings(
                user_id,
                workflow_path=workflow_path_update,
                default_lora=default_lora_update,
                fixed_positive_prompt=fixed_positive_prompt_update,
                fixed_negative_prompt=fixed_negative_prompt_update,
            )
            if not save_ok:
                await interaction.response.send_message('保存 ComfyUI 用户配置失败，请稍后重试。', ephemeral=True)
                return

            if workflow_path_update is not None:
                user_settings['workflow_path'] = workflow_path_update
            if default_lora_update is not None:
                user_settings['default_lora'] = default_lora_update
            if fixed_positive_prompt_update is not None:
                user_settings['fixed_positive_prompt'] = fixed_positive_prompt_update
            if fixed_negative_prompt_update is not None:
                user_settings['fixed_negative_prompt'] = fixed_negative_prompt_update
            user_settings['_from_user'] = True

        from_user = bool(user_settings.get('_from_user'))

        selected_model_name = str(model_name or '').strip()
        available_models: list[str] = []
        try:
            available_models = await comfyui_service.get_available_model_names()
        except Exception as error:
            log.warning(f'读取 ComfyUI 底模列表失败: {error}')

        panel = ComfyUIPanelView(
            cog=self,
            user_id=user_id,
            user_workflow_path=str(user_settings.get('workflow_path') or '').strip() if from_user else '',
            user_default_lora=str(user_settings.get('default_lora') or '').strip() if from_user else '',
            user_fixed_positive_prompt=str(user_settings.get('fixed_positive_prompt') or '').strip() if from_user else '',
            user_fixed_negative_prompt=str(user_settings.get('fixed_negative_prompt') or '').strip() if from_user else '',
            initial_model_name=selected_model_name,
            available_models=available_models,
        )

        if imported_workflow_name:
            panel.workflow_path = str(user_settings.get('workflow_path') or '').strip()

        if imported_lora_tokens:
            panel.lora_text = str(user_settings.get('default_lora') or '').strip()
            panel.user_default_lora = panel.lora_text
            panel.refresh_selects()

        if selected_model_name:
            panel.selected_model_name = selected_model_name
            panel.refresh_selects()

        embed = self.build_panel_embed(panel)
        await interaction.response.send_message(embed=embed, view=panel, ephemeral=True)
        panel.panel_message = await interaction.original_response()

        notice_parts: list[str] = []
        if imported_workflow_name:
            notice_parts.append(f'已导入并切换工作流：`{imported_workflow_name}`')
        if imported_lora_tokens:
            deduped_tokens: list[str] = []
            seen_tokens = set()
            for token in imported_lora_tokens:
                token_text = str(token or '').strip()
                if not token_text:
                    continue
                token_key = token_text.lower()
                if token_key in seen_tokens:
                    continue
                seen_tokens.add(token_key)
                deduped_tokens.append(token_text)

            if deduped_tokens:
                if len(deduped_tokens) == 1:
                    notice_parts.append(f'已导入 LoRA：`{deduped_tokens[0]}`')
                else:
                    joined_lora = '、'.join(f'`{name}`' for name in deduped_tokens[:5])
                    suffix = '' if len(deduped_tokens) <= 5 else f' 等 {len(deduped_tokens)} 个'
                    notice_parts.append(f'已导入 LoRA：{joined_lora}{suffix}')

        if installed_custom_nodes:
            deduped_nodes: list[str] = []
            seen_nodes = set()
            for node_name in installed_custom_nodes:
                node_text = str(node_name or '').strip()
                if not node_text:
                    continue
                node_key = node_text.lower()
                if node_key in seen_nodes:
                    continue
                seen_nodes.add(node_key)
                deduped_nodes.append(node_text)

            if deduped_nodes:
                if len(deduped_nodes) == 1:
                    notice_parts.append(f'已提交插件节点安装：`{deduped_nodes[0]}`')
                else:
                    joined_nodes = '、'.join(f'`{name}`' for name in deduped_nodes[:5])
                    suffix = '' if len(deduped_nodes) <= 5 else f' 等 {len(deduped_nodes)} 个'
                    notice_parts.append(f'已提交插件节点安装：{joined_nodes}{suffix}')

        if custom_node_warnings:
            notice_parts.append(f'⚠️ 插件安装提示：{custom_node_warnings[-1]}')

        if fixed_positive_prompt_update is not None:
            if fixed_positive_prompt_update:
                notice_parts.append('已更新个人固定正面提示词。')
            else:
                notice_parts.append('已清空个人固定正面提示词。')

        if fixed_negative_prompt_update is not None:
            if fixed_negative_prompt_update:
                notice_parts.append('已更新个人固定负面提示词。')
            else:
                notice_parts.append('已清空个人固定负面提示词。')

        if selected_model_name:
            notice_parts.append(f'已预设底模：`{selected_model_name}`')

        if notice_parts:
            await interaction.followup.send('\n'.join(notice_parts), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ComfyUICog(bot))
