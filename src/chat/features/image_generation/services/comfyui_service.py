# -*- coding: utf-8 -*-

import asyncio
import copy
import json
import logging
import random
import re
import unicodedata
import os
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, unquote, urlparse

import aiohttp

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')


class ComfyUIService:
    '''处理与 ComfyUI API 通信的业务逻辑（支持工作流导入与占位符替换）。'''

    _REALISTIC_STYLE_KEYWORDS = (
        '真人',
        '写实',
        '现实',
        '写真',
        '摄影',
        '实拍',
        'photoreal',
        'photorealistic',
        'realistic',
        'portrait',
        'cinematic',
        'zimage',
        'z_image',
        'qwen',
        'zib',
        'zit',
    )
    _ANIME_STYLE_KEYWORDS = (
        '二次元',
        '动漫',
        '动画',
        'anime',
        'manga',
        'waifu',
        'niji',
        '插画',
        '萌系',
        '赛璐璐',
        '卡通',
        'galgame',
    )
    _NODE_MAPPING_TOKEN_ALIASES: Dict[str, str] = {
        'prompt': 'positive_prompt',
        'positive_prompt': 'positive_prompt',
        'negative_prompt': 'negative_prompt',
        'width': 'width',
        'height': 'height',
        'steps': 'steps',
        'cfg': 'cfg',
        'cfg_scale': 'cfg',
        'sampler': 'sampler',
        'sampler_name': 'sampler',
        'scheduler': 'scheduler',
        'scheduler_name': 'scheduler',
        'seed': 'seed',
        'lora': 'lora',
        'lora_strength': 'lora_strength',
        'model_name': 'model_name',
        'ckpt_name': 'model_name',
        'model': 'model_name',
        'vae_name': 'vae_name',
        'vae': 'vae_name',
        'clip_name': 'clip_name',
        'clip': 'clip_name',
        'input_image': 'input_image',
        'reference_image': 'reference_image',
        'init_image': 'init_image',
        'image': 'input_image',
    }
    _NODE_MAPPING_FIELD_ALIASES: Dict[str, str] = {
        'width': 'width',
        'height': 'height',
        'steps': 'steps',
        'cfg': 'cfg',
        'cfg_scale': 'cfg',
        'sampler_name': 'sampler',
        'sampler': 'sampler',
        'scheduler': 'scheduler',
        'scheduler_name': 'scheduler',
        'seed': 'seed',
        'noise_seed': 'seed',
        'random_seed': 'seed',
        'lora': 'lora',
        'lora_name': 'lora',
        'lora_strength': 'lora_strength',
        'strength_model': 'lora_strength',
        'strength_clip': 'lora_strength',
        'unet_name': 'model_name',
        'ckpt_name': 'model_name',
        'model_name': 'model_name',
        'vae_name': 'vae_name',
        'clip_name': 'clip_name',
        'input_image': 'input_image',
        'reference_image': 'reference_image',
        'init_image': 'init_image',
        'image': 'input_image',
        'prompt': 'positive_prompt',
        'positive': 'positive_prompt',
        'negative': 'negative_prompt',
        'negative_prompt': 'negative_prompt',
    }
    _NODE_MAPPING_NEGATIVE_HINTS = (
        'negative',
        '负面',
        '反向',
        'lowres',
        'worst',
        'bad',
        'bad anatomy',
        'nsfw',
    )
    _PERCENT_TOKEN_RE = re.compile(r'%\s*([A-Za-z0-9_]+)\s*%')

    def __init__(self, server_address: Optional[str] = None, workflow_path: Optional[str] = None):
        self.server_address = ''
        self.workflow_path = ''
        self.prompt_url = ''
        self.history_url_base = ''
        self.view_url = ''
        self.workflow_template: Optional[Dict[str, Any]] = None
        self._request_semaphore = asyncio.Semaphore(1)
        self.reinitialize(server_address=server_address, workflow_path=workflow_path)

    @staticmethod
    def _sanitize_invisible_chars(raw_text: Optional[str]) -> str:
        text = str(raw_text or '').strip()
        if not text:
            return ''

        return ''.join(
            ch
            for ch in text
            if unicodedata.category(ch) != 'Cf'
        ).strip()

    @classmethod
    def _normalize_server_address(cls, raw_server_address: Optional[str]) -> str:
        address = cls._sanitize_invisible_chars(raw_server_address)
        if not address:
            return ''

        if address.startswith('file://'):
            address = address.replace('file://', '', 1).strip()

        if '://' not in address:
            address = f'http://{address}'

        return address.rstrip('/')

    @classmethod
    def _normalize_workflow_path(cls, raw_path: Optional[str]) -> str:
        path_text = cls._sanitize_invisible_chars(raw_path)
        if not path_text:
            return ''

        path_text = path_text.strip().strip('\'').strip(chr(34))
        if path_text.lower().startswith('file:///'):
            path_text = path_text[8:]
        elif path_text.lower().startswith('file://'):
            path_text = path_text[7:]

        return path_text.strip()

    @staticmethod
    def _normalize_prompt_style(raw_style: Optional[str]) -> str:
        style_text = str(raw_style or '').strip().lower()
        if not style_text:
            return ''
        if style_text in {'realistic', 'real', 'photo', 'photoreal', '真人', '写实'}:
            return 'realistic'
        if style_text in {'anime', '2d', 'cartoon', '二次元', '动漫'}:
            return 'anime'
        return ''

    @classmethod
    def _count_style_keyword_hits(cls, normalized_text: str, keywords: tuple[str, ...]) -> int:
        if not normalized_text:
            return 0
        return sum(1 for keyword in keywords if keyword in normalized_text)

    @classmethod
    def _detect_prompt_style(
        cls,
        prompt: Optional[str] = None,
        positive_prompt: Optional[str] = None,
    ) -> str:
        merged_text = ' '.join(
            part.strip().lower()
            for part in (
                str(prompt or ''),
                str(positive_prompt or ''),
            )
            if part and str(part).strip()
        )
        if not merged_text:
            return ''

        realistic_hits = cls._count_style_keyword_hits(merged_text, cls._REALISTIC_STYLE_KEYWORDS)
        anime_hits = cls._count_style_keyword_hits(merged_text, cls._ANIME_STYLE_KEYWORDS)
        if realistic_hits == anime_hits:
            return ''
        return 'realistic' if realistic_hits > anime_hits else 'anime'

    def resolve_prompt_style(
        self,
        prompt: Optional[str] = None,
        positive_prompt: Optional[str] = None,
        prompt_style: Optional[str] = None,
    ) -> str:
        normalized_style = self._normalize_prompt_style(prompt_style)
        if normalized_style:
            return normalized_style
        return self._detect_prompt_style(prompt=prompt, positive_prompt=positive_prompt)

    def resolve_default_model_name(
        self,
        prompt: Optional[str] = None,
        positive_prompt: Optional[str] = None,
        prompt_style: Optional[str] = None,
    ) -> str:
        config = app_config.COMFYUI_CONFIG
        resolved_style = self.resolve_prompt_style(
            prompt=prompt,
            positive_prompt=positive_prompt,
            prompt_style=prompt_style,
        )
        if resolved_style == 'realistic':
            style_model_name = str(config.get('DEFAULT_REALISTIC_MODEL_NAME') or '').strip()
            if style_model_name:
                return style_model_name
        elif resolved_style == 'anime':
            style_model_name = str(config.get('DEFAULT_ANIME_MODEL_NAME') or '').strip()
            if style_model_name:
                return style_model_name
        return str(config.get('DEFAULT_MODEL_NAME') or '').strip()

    def resolve_default_workflow_path(
        self,
        prompt: Optional[str] = None,
        positive_prompt: Optional[str] = None,
        prompt_style: Optional[str] = None,
    ) -> str:
        config = app_config.COMFYUI_CONFIG
        resolved_style = self.resolve_prompt_style(
            prompt=prompt,
            positive_prompt=positive_prompt,
            prompt_style=prompt_style,
        )
        if resolved_style == 'realistic':
            return self._normalize_workflow_path(config.get('DEFAULT_REALISTIC_WORKFLOW_PATH'))
        if resolved_style == 'anime':
            return self._normalize_workflow_path(config.get('DEFAULT_ANIME_WORKFLOW_PATH'))
        return ''

    @staticmethod
    def _coerce_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _coerce_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _sanitize_upload_filename(raw_name: Optional[str], fallback: str = 'reference_image.png') -> str:
        base_name = Path(str(raw_name or '').strip()).name
        if not base_name:
            base_name = fallback

        safe_name = re.sub(r'[^0-9A-Za-z._\-]+', '_', base_name).strip('._')
        if not safe_name:
            safe_name = fallback

        if '.' not in safe_name:
            safe_name = f'{safe_name}.png'

        return safe_name

    @staticmethod
    def _normalize_sampler_name(raw_sampler: Any) -> str:
        sampler_text = str(raw_sampler or '').strip().lower()
        alias_map = {
            'euler_a': 'euler_ancestral',
            'euler ancestral': 'euler_ancestral',
        }
        return alias_map.get(sampler_text, sampler_text)

    @staticmethod
    def _normalize_scheduler_name(raw_scheduler: Any) -> str:
        return str(raw_scheduler or '').strip().lower()

    @staticmethod
    def _merge_fixed_prompt(base_prompt: Any, fixed_prompt: Any) -> str:
        base_text = str(base_prompt or '').strip()
        fixed_text = str(fixed_prompt or '').strip()
        if not fixed_text:
            return base_text
        if not base_text:
            return fixed_text

        base_lower = base_text.lower()
        fixed_lower = fixed_text.lower()
        if fixed_lower in base_lower:
            return base_text
        if base_lower in fixed_lower:
            return fixed_text
        return f'{fixed_text}, {base_text}'

    def _refresh_endpoints(self) -> None:
        if not self.server_address:
            self.prompt_url = ''
            self.history_url_base = ''
            self.view_url = ''
            return

        self.prompt_url = f'{self.server_address}/prompt'
        self.history_url_base = f'{self.server_address}/history'
        self.view_url = f'{self.server_address}/view'

    def reinitialize(self, server_address: Optional[str] = None, workflow_path: Optional[str] = None) -> None:
        config = app_config.COMFYUI_CONFIG

        resolved_server_address = self._normalize_server_address(
            server_address if server_address is not None else config.get('SERVER_ADDRESS', '')
        )
        resolved_workflow_path = self._normalize_workflow_path(
            workflow_path if workflow_path is not None else config.get('WORKFLOW_PATH', '')
        )

        self.server_address = resolved_server_address
        self.workflow_path = resolved_workflow_path

        config['SERVER_ADDRESS'] = resolved_server_address
        config['WORKFLOW_PATH'] = resolved_workflow_path

        self._refresh_endpoints()
        self.workflow_template = self._load_workflow_template()

    def is_enabled(self) -> bool:
        return bool(app_config.COMFYUI_CONFIG.get('ENABLED', False))

    def is_server_ready(self) -> bool:
        return bool(
            self.is_enabled()
            and self.server_address
            and self.prompt_url
            and self.history_url_base
            and self.view_url
        )

    def is_available(self) -> bool:
        return bool(
            self.is_server_ready()
            and self.workflow_template is not None
        )

    def _load_workflow_template(self) -> Optional[Dict[str, Any]]:
        if not self.workflow_path:
            log.warning('ComfyUI 工作流路径为空，等待 Dashboard 导入。')
            return None

        return self._load_workflow_template_from_path(self.workflow_path)

    @staticmethod
    def _looks_like_comfy_workflow(payload: Any) -> bool:
        if not isinstance(payload, dict) or not payload:
            return False

        for node in payload.values():
            if isinstance(node, dict) and ('class_type' in node or 'inputs' in node):
                return True
        return False

    @classmethod
    def _normalize_workflow_payload(cls, payload: Any) -> Dict[str, Any]:
        current_payload: Any = payload

        for _ in range(3):
            if isinstance(current_payload, str):
                text = current_payload.strip()
                if not text:
                    break
                current_payload = json.loads(text)
                continue

            if isinstance(current_payload, dict):
                if cls._looks_like_comfy_workflow(current_payload):
                    return current_payload

                if len(current_payload) == 1:
                    nested_value = next(iter(current_payload.values()))
                    if isinstance(nested_value, (str, dict)):
                        current_payload = nested_value
                        continue
                break

            break

        if not isinstance(current_payload, dict):
            raise ValueError('工作流 JSON 必须是对象（object）')
        if not cls._looks_like_comfy_workflow(current_payload):
            raise ValueError('工作流 JSON 未识别到有效节点结构（需包含 class_type/inputs）')
        return current_payload

    def _load_workflow_template_from_path(self, workflow_path_text: str) -> Optional[Dict[str, Any]]:
        normalized_path = self._normalize_workflow_path(workflow_path_text)
        if not normalized_path:
            return None

        try:
            workflow_path = Path(normalized_path)
            with workflow_path.open('r', encoding='utf-8') as file:
                workflow_data = json.load(file)

            workflow_data = self._normalize_workflow_payload(workflow_data)

            log.info(f'已加载 ComfyUI 工作流: {normalized_path}')
            return workflow_data
        except FileNotFoundError:
            log.error(f'ComfyUI 工作流文件不存在: {normalized_path}')
            return None
        except json.JSONDecodeError as error:
            log.error(f'ComfyUI 工作流 JSON 解析失败: {normalized_path}, error={error}')
            return None
        except Exception as error:
            log.error(f'加载 ComfyUI 工作流失败: {normalized_path}, error={error}')
            return None

    @staticmethod
    def save_workflow_text(workflow_text: str, target_path: str) -> str:
        parsed = json.loads(workflow_text)
        parsed = ComfyUIService._normalize_workflow_payload(parsed)

        save_path = Path(target_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        return str(save_path)

    @classmethod
    def _extract_param_key_from_text_tokens(cls, raw_text: Optional[str]) -> str:
        text = str(raw_text or '').strip()
        if not text:
            return ''

        for match in cls._PERCENT_TOKEN_RE.finditer(text):
            token_key = str(match.group(1) or '').strip().lower()
            if token_key in cls._NODE_MAPPING_TOKEN_ALIASES:
                return cls._NODE_MAPPING_TOKEN_ALIASES[token_key]

        for match in _PLACEHOLDER_RE.finditer(text):
            token_key = str(match.group(1) or '').strip().lower()
            if token_key in cls._NODE_MAPPING_TOKEN_ALIASES:
                return cls._NODE_MAPPING_TOKEN_ALIASES[token_key]

        return ''

    @classmethod
    def _infer_param_key_from_field(
        cls,
        field_name: str,
        class_type: str,
        field_value: Any,
        current_mapping: Dict[str, list[str]],
    ) -> str:
        normalized_field_name = str(field_name or '').strip().lower()
        if not normalized_field_name:
            return ''

        direct_key = cls._NODE_MAPPING_FIELD_ALIASES.get(normalized_field_name, '')
        if direct_key and normalized_field_name != 'text':
            return direct_key

        if normalized_field_name != 'text':
            return direct_key

        class_type_text = str(class_type or '').strip().lower()
        if 'cliptextencode' not in class_type_text:
            return ''

        value_text = str(field_value or '').strip().lower()
        has_negative_hint = any(hint in value_text for hint in cls._NODE_MAPPING_NEGATIVE_HINTS)
        if has_negative_hint and 'negative_prompt' not in current_mapping:
            return 'negative_prompt'

        if 'positive_prompt' not in current_mapping:
            return 'positive_prompt'
        if 'negative_prompt' not in current_mapping:
            return 'negative_prompt'
        return ''

    @classmethod
    def infer_node_mapping_from_workflow_payload(cls, workflow_payload: Any) -> Dict[str, list[str]]:
        workflow = cls._normalize_workflow_payload(workflow_payload)
        mapping: Dict[str, list[str]] = {}

        def sort_key(raw_node_id: Any) -> tuple[int, Any]:
            node_id_text = str(raw_node_id or '').strip()
            if node_id_text.isdigit():
                return (0, int(node_id_text))
            return (1, node_id_text)

        for raw_node_id in sorted(workflow.keys(), key=sort_key):
            node_id = str(raw_node_id).strip()
            node_obj = workflow.get(raw_node_id)
            if not isinstance(node_obj, dict):
                continue

            class_type = str(node_obj.get('class_type') or '').strip()
            inputs = node_obj.get('inputs')
            if not isinstance(inputs, dict):
                continue

            for raw_field_name, field_value in inputs.items():
                field_name = str(raw_field_name or '').strip()
                if not field_name:
                    continue
                if isinstance(field_value, (list, dict)):
                    continue

                inferred_param_key = ''
                if isinstance(field_value, str):
                    inferred_param_key = cls._extract_param_key_from_text_tokens(field_value)

                if not inferred_param_key:
                    inferred_param_key = cls._infer_param_key_from_field(
                        field_name=field_name,
                        class_type=class_type,
                        field_value=field_value,
                        current_mapping=mapping,
                    )

                if not inferred_param_key or inferred_param_key in mapping:
                    continue

                mapping[inferred_param_key] = [node_id, field_name]

        return mapping

    @classmethod
    def _build_effective_placeholder_mapping_for_parameterize(
        cls,
        placeholder_mapping: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        default_mapping: Dict[str, str] = {
            'positive_prompt': '{{positive_prompt}}',
            'negative_prompt': '{{negative_prompt}}',
            'width': '{{width}}',
            'height': '{{height}}',
            'steps': '{{steps}}',
            'cfg': '{{cfg}}',
            'sampler': '{{sampler}}',
            'scheduler': '{{scheduler}}',
            'seed': '{{seed}}',
            'lora': '{{lora}}',
            'lora_strength': '{{lora_strength}}',
            'model_name': '{{model_name}}',
            'vae_name': '{{vae_name}}',
            'clip_name': '{{clip_name}}',
            'input_image': '{{input_image}}',
            'reference_image': '{{reference_image}}',
            'init_image': '{{init_image}}',
        }

        runtime_mapping = app_config.COMFYUI_CONFIG.get('PLACEHOLDER_MAPPING') or {}
        if isinstance(runtime_mapping, dict):
            for raw_key, raw_value in runtime_mapping.items():
                key_text = str(raw_key or '').strip()
                value_text = str(raw_value or '').strip()
                if key_text and value_text:
                    default_mapping[key_text] = value_text

        if isinstance(placeholder_mapping, dict):
            for raw_key, raw_value in placeholder_mapping.items():
                key_text = str(raw_key or '').strip()
                value_text = str(raw_value or '').strip()
                if key_text and value_text:
                    default_mapping[key_text] = value_text

        return default_mapping

    @classmethod
    def parameterize_workflow_payload(
        cls,
        workflow_payload: Any,
        placeholder_mapping: Optional[Dict[str, Any]] = None,
        only_parameter_keys: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        workflow = copy.deepcopy(cls._normalize_workflow_payload(workflow_payload))
        node_mapping = cls.infer_node_mapping_from_workflow_payload(workflow)
        effective_placeholder_mapping = cls._build_effective_placeholder_mapping_for_parameterize(
            placeholder_mapping
        )
        normalized_only_keys: Optional[set[str]] = None
        if only_parameter_keys:
            normalized_only_keys = {
                str(key).strip()
                for key in only_parameter_keys
                if str(key).strip()
            }
            if not normalized_only_keys:
                normalized_only_keys = None

        replaced_keys: list[str] = []
        skipped_keys: list[str] = []

        for raw_key, mapping_value in node_mapping.items():
            key_text = str(raw_key or '').strip()
            if not key_text:
                continue
            if normalized_only_keys is not None and key_text not in normalized_only_keys:
                continue

            token_text = str(
                effective_placeholder_mapping.get(key_text) or f'{{{{{key_text}}}}}'
            ).strip()
            if not token_text:
                token_text = f'{{{{{key_text}}}}}'

            if not isinstance(mapping_value, (list, tuple)) or len(mapping_value) < 2:
                skipped_keys.append(key_text)
                continue

            node_id = str(mapping_value[0] or '').strip()
            input_field = str(mapping_value[1] or '').strip()
            if not node_id or not input_field:
                skipped_keys.append(key_text)
                continue

            node_obj = workflow.get(node_id)
            if not isinstance(node_obj, dict):
                skipped_keys.append(key_text)
                continue

            inputs = node_obj.get('inputs')
            if not isinstance(inputs, dict) or input_field not in inputs:
                skipped_keys.append(key_text)
                continue

            current_value = inputs.get(input_field)
            if isinstance(current_value, (list, dict)):
                skipped_keys.append(key_text)
                continue

            inputs[input_field] = token_text
            replaced_keys.append(key_text)

        return {
            'workflow': workflow,
            'node_mapping': node_mapping,
            'placeholder_mapping': effective_placeholder_mapping,
            'replaced_keys': sorted(set(replaced_keys)),
            'skipped_keys': sorted(set(skipped_keys)),
        }

    def _build_runtime_params(self, **kwargs: Any) -> Dict[str, Any]:
        config = app_config.COMFYUI_CONFIG
        default_seed = self._coerce_int(config.get('DEFAULT_SEED'), 12345)
        seed_value = self._coerce_int(kwargs.get('seed'), default_seed)
        if seed_value < 0:
            seed_value = random.randint(0, 4294967295)

        positive_prompt = kwargs.get('positive_prompt')
        if positive_prompt is None:
            positive_prompt = kwargs.get('prompt')
        prompt_text = kwargs.get('prompt')

        resolved_prompt_style = self.resolve_prompt_style(
            prompt=prompt_text,
            positive_prompt=positive_prompt,
            prompt_style=kwargs.get('prompt_style'),
        )
        resolved_default_model_name = self.resolve_default_model_name(
            prompt=prompt_text,
            positive_prompt=positive_prompt,
            prompt_style=resolved_prompt_style,
        )

        if 'lora' in kwargs:
            lora_value = kwargs.get('lora')
        elif 'lora_name' in kwargs:
            lora_value = kwargs.get('lora_name')
        else:
            lora_value = config.get('DEFAULT_LORA')

        fixed_positive_prompt = str(config.get('FIXED_POSITIVE_PROMPT') or '').strip()
        fixed_negative_prompt = str(config.get('FIXED_NEGATIVE_PROMPT') or '').strip()
        user_fixed_positive_prompt = str(kwargs.get('user_fixed_positive_prompt') or '').strip()
        user_fixed_negative_prompt = str(kwargs.get('user_fixed_negative_prompt') or '').strip()

        effective_fixed_positive_prompt = self._merge_fixed_prompt(user_fixed_positive_prompt, fixed_positive_prompt)
        effective_fixed_negative_prompt = self._merge_fixed_prompt(user_fixed_negative_prompt, fixed_negative_prompt)

        merged_positive_prompt = self._merge_fixed_prompt(positive_prompt, effective_fixed_positive_prompt)
        merged_negative_prompt = self._merge_fixed_prompt(kwargs.get('negative_prompt'), effective_fixed_negative_prompt)

        params: Dict[str, Any] = {
            'positive_prompt': merged_positive_prompt,
            'negative_prompt': merged_negative_prompt,
            'width': self._coerce_int(kwargs.get('width'), self._coerce_int(config.get('DEFAULT_WIDTH'), 832)),
            'height': self._coerce_int(kwargs.get('height'), self._coerce_int(config.get('DEFAULT_HEIGHT'), 1216)),
            'steps': self._coerce_int(kwargs.get('steps'), self._coerce_int(config.get('DEFAULT_STEPS'), 28)),
            'cfg': self._coerce_float(kwargs.get('cfg'), self._coerce_float(config.get('DEFAULT_CFG'), 5.0)),
            'sampler': self._normalize_sampler_name(kwargs.get('sampler') or config.get('DEFAULT_SAMPLER') or ''),
            'scheduler': self._normalize_scheduler_name(kwargs.get('scheduler') or config.get('DEFAULT_SCHEDULER') or ''),
            'seed': seed_value,
            'lora': str(lora_value or '').strip(),
            'lora_strength': self._coerce_float(
                kwargs.get('lora_strength'),
                self._coerce_float(config.get('DEFAULT_LORA_STRENGTH'), 1.0),
            ),
            'model_name': str(kwargs.get('model_name') or resolved_default_model_name or '').strip(),
            'vae_name': str(kwargs.get('vae_name') or config.get('DEFAULT_VAE_NAME') or '').strip(),
            'clip_name': str(kwargs.get('clip_name') or config.get('DEFAULT_CLIP_NAME') or '').strip(),
            'prompt_style': resolved_prompt_style,
        }

        for key, value in kwargs.items():
            if key in params or value is None:
                continue
            params[str(key)] = value

        placeholder_mapping = app_config.COMFYUI_CONFIG.get('PLACEHOLDER_MAPPING') or {}
        node_mapping = app_config.COMFYUI_CONFIG.get('NODE_MAPPING') or {}
        has_lora_placeholder = isinstance(placeholder_mapping, dict) and 'lora' in placeholder_mapping
        has_lora_node_mapping = isinstance(node_mapping, dict) and 'lora' in node_mapping

        current_prompt = str(params.get('positive_prompt') or '').strip()
        lora_name = str(params.get('lora') or '').strip()
        lora_strength = self._coerce_float(params.get('lora_strength'), 1.0)

        if current_prompt and lora_name and not has_lora_placeholder and not has_lora_node_mapping:
            params['positive_prompt'] = f'{current_prompt}, <lora:{lora_name}:{lora_strength:.2f}>'

        return params

    def _build_placeholder_token_values(self, params: Dict[str, Any]) -> Dict[str, Any]:
        token_values: Dict[str, Any] = {}
        placeholder_mapping = app_config.COMFYUI_CONFIG.get('PLACEHOLDER_MAPPING') or {}

        for key, value in params.items():
            if value is None:
                continue

            key_text = str(key).strip()
            if not key_text:
                continue

            token_values[f'{{{{{key_text}}}}}'] = value

            if isinstance(placeholder_mapping, dict):
                mapped_token = placeholder_mapping.get(key_text)
                if mapped_token is not None:
                    mapped_token_text = str(mapped_token).strip()
                    if mapped_token_text:
                        token_values[mapped_token_text] = value

        common_aliases: Dict[str, list[str]] = {
            'positive_prompt': ['%prompt%', '%positive_prompt%'],
            'negative_prompt': ['%negative_prompt%'],
            'width': ['%width%'],
            'height': ['%height%'],
            'steps': ['%steps%'],
            'cfg': ['%cfg%', '%cfg_scale%', '%CFG%', '%CFG_SCALE%'],
            'sampler': ['%sampler%', '%sampler_name%', '%SAMPLER%', '%SAMPLER_NAME%'],
            'scheduler': ['%scheduler%', '%scheduler_name%', '%SCHEDULER%', '%SCHEDULER_NAME%'],
            'seed': ['%seed%'],
            'lora': ['%lora%'],
            'lora_strength': ['%lora_strength%'],
            'model_name': ['%MODEL_NAME%', '%model_name%', '%CKPT_NAME%', '%ckpt_name%', '%MODEL%', '%model%'],
            'vae_name': ['%VAE_NAME%', '%vae_name%', '%vae%'],
            'clip_name': ['%CLIP_NAME%', '%clip_name%', '%clip%'],
            'input_image': [
                '%input_image%', '%INPUT_IMAGE%',
                '%reference_image%', '%REFERENCE_IMAGE%',
                '%init_image%', '%INIT_IMAGE%',
                '%image%', '%IMAGE%',
            ],
            'reference_image': ['%reference_image%', '%REFERENCE_IMAGE%'],
            'init_image': ['%init_image%', '%INIT_IMAGE%'],
        }

        for key_text, tokens in common_aliases.items():
            value = params.get(key_text)
            if value is None:
                continue
            for token_text in tokens:
                token_values[token_text] = value

        return token_values

    async def get_available_model_names(self) -> list[str]:
        folder_names = ('checkpoints', 'diffusion_models', 'unet')
        folder_results = await asyncio.gather(
            *(self._fetch_safetensors_from_model_folder(folder_name) for folder_name in folder_names),
            return_exceptions=True,
        )

        merged_names: list[str] = []
        seen = set()
        for folder_name, result in zip(folder_names, folder_results):
            if isinstance(result, Exception):
                log.warning(f'读取 ComfyUI 模型目录失败: folder={folder_name}, error={result}')
                continue

            for name in result:
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                merged_names.append(name)

        return merged_names

    async def get_available_vae_names(self) -> list[str]:
        names = await self._fetch_safetensors_from_model_folder('vae')
        if names:
            return names
        return await self._get_available_choice_names(('vae_name',))

    async def get_available_clip_names(self) -> list[str]:
        names = await self._fetch_safetensors_from_model_folder('clip')
        if names:
            return names
        return await self._get_available_choice_names(('clip_name',))

    async def get_available_lora_names(self) -> list[str]:
        return await self._fetch_safetensors_from_model_folder('loras')

    async def _fetch_safetensors_from_model_folder(self, folder_name: str) -> list[str]:
        if not self.server_address:
            return []

        normalized_folder = str(folder_name or '').strip()
        if not normalized_folder:
            return []

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=min(timeout_seconds, 30))
        model_folder_url = f'{self.server_address}/models/{normalized_folder}'

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(model_folder_url) as response:
                    if response.status < 200 or response.status >= 300:
                        return []
                    payload = await self._read_response_payload(response)
        except Exception as error:
            log.warning(f'读取 ComfyUI 模型目录异常: folder={normalized_folder}, error={error}')
            return []

        return self._normalize_safetensors_names(payload)

    @staticmethod
    def _normalize_safetensors_names(payload: Any) -> list[str]:
        if not isinstance(payload, list):
            return []

        names: list[str] = []
        seen = set()
        for item in payload:
            item_text = str(item or '').strip()
            if not item_text or not item_text.lower().endswith('.safetensors'):
                continue
            key = item_text.lower()
            if key in seen:
                continue
            seen.add(key)
            names.append(item_text)

        return sorted(names, key=lambda value: value.lower())

    async def _fetch_object_info(self) -> Dict[str, Any]:
        if not self.server_address:
            return {}

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=min(timeout_seconds, 30))
        object_info_url = f'{self.server_address}/object_info'

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(object_info_url) as response:
                    if response.status < 200 or response.status >= 300:
                        return {}
                    payload = await self._read_response_payload(response)
        except Exception as error:
            log.warning(f'获取 ComfyUI object_info 失败: {error}')
            return {}

        if not isinstance(payload, dict):
            return {}

        return payload

    async def _get_available_choice_names(self, field_names: tuple[str, ...]) -> list[str]:
        payload = await self._fetch_object_info()
        if not payload:
            return []

        names: list[str] = []
        seen = set()

        def collect_names_from_input_map(input_map: Any) -> None:
            if not isinstance(input_map, dict):
                return

            for field_name in field_names:
                field_obj = input_map.get(field_name)
                if not isinstance(field_obj, (list, tuple)) or not field_obj:
                    continue

                choices = field_obj[0]
                if not isinstance(choices, (list, tuple)):
                    continue

                for item in choices:
                    item_text = str(item or '').strip()
                    if not item_text:
                        continue
                    if item_text.lower() in {'none', '无', 'null'}:
                        continue
                    item_key = item_text.lower()
                    if item_key in seen:
                        continue
                    seen.add(item_key)
                    names.append(item_text)

        for _, node_info in payload.items():
            if not isinstance(node_info, dict):
                continue

            input_obj = node_info.get('input')
            if not isinstance(input_obj, dict):
                continue

            collect_names_from_input_map(input_obj.get('required'))
            collect_names_from_input_map(input_obj.get('optional'))

        return names

    def _replace_placeholders_in_string(
        self,
        text: str,
        token_values: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Any:
        stripped_text = text.strip()

        if stripped_text in token_values:
            return token_values[stripped_text]

        if stripped_text == text and stripped_text in token_values:
            return token_values[stripped_text]

        if stripped_text == text and stripped_text.startswith('{{') and stripped_text.endswith('}}'):
            key_text = stripped_text[2:-2].strip()
            if key_text in params and params[key_text] is not None:
                return params[key_text]

        replaced = text
        for token, value in token_values.items():
            replaced = replaced.replace(token, str(value))

        def replace_match(match: re.Match[str]) -> str:
            key_text = str(match.group(1) or '').strip()
            value = params.get(key_text)
            return str(value) if value is not None else match.group(0)

        return _PLACEHOLDER_RE.sub(replace_match, replaced)

    def _replace_placeholders_recursive(
        self,
        node: Any,
        token_values: Dict[str, Any],
        params: Dict[str, Any],
    ) -> Any:
        if isinstance(node, dict):
            return {
                key: self._replace_placeholders_recursive(value, token_values, params)
                for key, value in node.items()
            }

        if isinstance(node, list):
            return [self._replace_placeholders_recursive(item, token_values, params) for item in node]

        if isinstance(node, str):
            return self._replace_placeholders_in_string(node, token_values, params)

        return node

    def _apply_node_mapping(self, workflow: Dict[str, Any], params: Dict[str, Any]) -> None:
        raw_mapping = app_config.COMFYUI_CONFIG.get('NODE_MAPPING') or {}
        if not isinstance(raw_mapping, dict):
            return

        for key, mapping_value in raw_mapping.items():
            key_text = str(key).strip()
            if not key_text or key_text not in params:
                continue

            new_value = params.get(key_text)
            if new_value is None:
                continue

            if not isinstance(mapping_value, (list, tuple)) or len(mapping_value) < 2:
                continue

            node_id = str(mapping_value[0]).strip()
            input_field = str(mapping_value[1]).strip()
            if not node_id or not input_field:
                continue

            node_obj = workflow.get(node_id)
            if not isinstance(node_obj, dict):
                log.warning(f'ComfyUI 节点映射未命中节点: key={key_text}, node_id={node_id}')
                continue

            inputs = node_obj.get('inputs')
            if not isinstance(inputs, dict):
                log.warning(f'ComfyUI 节点缺少 inputs: node_id={node_id}')
                continue

            if input_field not in inputs:
                log.warning(
                    f'ComfyUI 节点输入不存在: key={key_text}, node_id={node_id}, input={input_field}'
                )
                continue

            inputs[input_field] = new_value

    def _prepare_workflow(
        self,
        params: Dict[str, Any],
        workflow_template: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected_template = workflow_template if workflow_template is not None else self.workflow_template
        if not selected_template:
            raise ValueError('ComfyUI 工作流模板未加载')

        workflow = copy.deepcopy(selected_template)
        self._apply_node_mapping(workflow, params)
        token_values = self._build_placeholder_token_values(params)
        workflow = self._replace_placeholders_recursive(workflow, token_values, params)
        return workflow

    @staticmethod
    def _workflow_contains_any_token(workflow_template: Dict[str, Any], tokens: tuple[str, ...]) -> bool:
        if not workflow_template or not tokens:
            return False

        try:
            workflow_text = json.dumps(workflow_template, ensure_ascii=False).lower()
        except Exception:
            return False

        for token in tokens:
            token_text = str(token or '').strip().lower()
            if token_text and token_text in workflow_text:
                return True
        return False

    @staticmethod
    def _pick_best_name_candidate(
        candidates: list[str],
        hints: list[str],
        preferred_keywords: Optional[list[str]] = None,
        avoid_keywords: Optional[list[str]] = None,
    ) -> str:
        normalized_candidates = [str(item or '').strip() for item in candidates if str(item or '').strip()]
        if not normalized_candidates:
            return ''

        lowered_candidates = [(name, name.lower()) for name in normalized_candidates]

        avoid_list = [str(keyword or '').strip().lower() for keyword in (avoid_keywords or []) if str(keyword or '').strip()]
        if avoid_list:
            filtered_candidates = [
                item
                for item in lowered_candidates
                if not any(avoid_word in item[1] for avoid_word in avoid_list)
            ]
            if filtered_candidates:
                lowered_candidates = filtered_candidates

        keyword_hints: list[str] = []
        for hint in hints:
            hint_text = str(hint or '').strip().lower()
            if not hint_text:
                continue

            hint_stem = Path(hint_text).stem
            parts = [part for part in re.split(r'[\\/_.\-\s]+', hint_stem) if len(part) >= 3]
            keyword_hints.extend(parts)

            for key in ('qwen', 'wan', 'sdxl', 'flux'):
                if key in hint_text:
                    keyword_hints.insert(0, key)

        deduped_hints: list[str] = []
        seen_hints = set()
        for hint in keyword_hints:
            hint_key = hint.lower()
            if hint_key in seen_hints:
                continue
            seen_hints.add(hint_key)
            deduped_hints.append(hint)

        preferred_list = [str(keyword or '').strip().lower() for keyword in (preferred_keywords or []) if str(keyword or '').strip()]
        if preferred_list:
            for preferred in preferred_list:
                for candidate_name, candidate_lower in lowered_candidates:
                    if preferred in candidate_lower:
                        return candidate_name

        for hint in deduped_hints:
            for candidate_name, candidate_lower in lowered_candidates:
                if hint in candidate_lower:
                    return candidate_name

        return lowered_candidates[0][0]

    async def _fill_missing_runtime_names(
        self,
        params: Dict[str, Any],
        workflow_template: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        selected_template = workflow_template if workflow_template is not None else self.workflow_template
        if not isinstance(selected_template, dict) or not selected_template:
            return params

        try:
            workflow_text = json.dumps(selected_template, ensure_ascii=False).lower()
        except Exception:
            workflow_text = ''

        def _is_name_in_candidates(name: str, candidates: list[str]) -> bool:
            target = str(name or '').strip().lower()
            if not target:
                return False
            return any(str(item or '').strip().lower() == target for item in candidates)

        model_name = str(params.get('model_name') or '').strip()
        requires_model = self._workflow_contains_any_token(
            selected_template,
            ('%model_name%', '%ckpt_name%', '%model%', '{{model_name}}'),
        )
        if requires_model or model_name:
            available_models = await self.get_available_model_names()
            if model_name:
                if available_models and not _is_name_in_candidates(model_name, available_models):
                    auto_model_name = self._pick_best_name_candidate(
                        available_models,
                        [model_name, workflow_text],
                    )
                    if auto_model_name:
                        params['model_name'] = auto_model_name
                        model_name = auto_model_name
                        log.warning(f'ComfyUI model_name 不可用，已自动回退: {auto_model_name}')
            elif requires_model:
                auto_model_name = self._pick_best_name_candidate(available_models, [workflow_text])
                if auto_model_name:
                    params['model_name'] = auto_model_name
                    model_name = auto_model_name
                    log.info(f'ComfyUI 自动填充 model_name: {auto_model_name}')

        clip_name = str(params.get('clip_name') or '').strip()
        requires_clip = self._workflow_contains_any_token(
            selected_template,
            ('%clip_name%', '%clip%', '{{clip_name}}'),
        )
        if requires_clip or clip_name:
            available_clips = await self.get_available_clip_names()
            clip_preferred_keywords: list[str] = []
            combined_hint_text = f'{model_name} {workflow_text}'.lower()
            if 'qwen' in combined_hint_text:
                clip_preferred_keywords.append('qwen')

            if clip_name:
                if available_clips and not _is_name_in_candidates(clip_name, available_clips):
                    auto_clip_name = self._pick_best_name_candidate(
                        available_clips,
                        [clip_name, model_name, workflow_text],
                        preferred_keywords=clip_preferred_keywords,
                    )
                    if auto_clip_name:
                        params['clip_name'] = auto_clip_name
                        clip_name = auto_clip_name
                        log.warning(f'ComfyUI clip_name 不可用，已自动回退: {auto_clip_name}')
            elif requires_clip:
                auto_clip_name = self._pick_best_name_candidate(
                    available_clips,
                    [model_name, workflow_text],
                    preferred_keywords=clip_preferred_keywords,
                )
                if auto_clip_name:
                    params['clip_name'] = auto_clip_name
                    clip_name = auto_clip_name
                    log.info(f'ComfyUI 自动填充 clip_name: {auto_clip_name}')

        vae_name = str(params.get('vae_name') or '').strip()
        requires_vae = self._workflow_contains_any_token(
            selected_template,
            ('%vae_name%', '%vae%', '{{vae_name}}'),
        )
        if requires_vae or vae_name:
            available_vaes = await self.get_available_vae_names()

            vae_preferred_keywords: list[str] = []
            vae_avoid_keywords: list[str] = []
            combined_hint_text = f'{clip_name} {model_name} {workflow_text}'.lower()

            if 'qwen' in combined_hint_text:
                vae_preferred_keywords.append('qwen')
            if 'sdxl' in combined_hint_text:
                vae_preferred_keywords.append('sdxl')
            if 'ae' in combined_hint_text:
                vae_preferred_keywords.append('ae')

            is_image_latent_workflow = (
                'emptylatentimage' in workflow_text
                and 'emptylatentvideo' not in workflow_text
                and 'video' not in workflow_text
            )
            if is_image_latent_workflow:
                vae_avoid_keywords.extend(['wan', 'video'])

            if vae_name:
                if available_vaes and not _is_name_in_candidates(vae_name, available_vaes):
                    auto_vae_name = self._pick_best_name_candidate(
                        available_vaes,
                        [vae_name, clip_name, model_name, workflow_text],
                        preferred_keywords=vae_preferred_keywords,
                        avoid_keywords=vae_avoid_keywords,
                    )
                    if auto_vae_name:
                        params['vae_name'] = auto_vae_name
                        log.warning(f'ComfyUI vae_name 不可用，已自动回退: {auto_vae_name}')
            elif requires_vae:
                auto_vae_name = self._pick_best_name_candidate(
                    available_vaes,
                    [clip_name, model_name, workflow_text],
                    preferred_keywords=vae_preferred_keywords,
                    avoid_keywords=vae_avoid_keywords,
                )
                if auto_vae_name:
                    params['vae_name'] = auto_vae_name
                    log.info(f'ComfyUI 自动填充 vae_name: {auto_vae_name}')

        return params


    # ========================================================
    # CNB Workspace 按需自动启动（小芸 2026-07-06）
    # ========================================================
    # 当 ComfyUI 不可达时，自动调用 CNB API 启动 workspace，
    # 等待构建完成 + ComfyUI 就绪，然后更新 server_address。
    # 30分钟无访问后 CNB 会自动关机（keepAliveTimeout: 30m）。

    _CNB_TOKEN = os.getenv('CNB_TOKEN', '')
    _CNB_REPO = os.getenv('CNB_WORKSPACE_REPO', 'bufan.live/krea-2')
    _CNB_API_BASE = 'https://api.cnb.cool'

    async def _check_comfyui_online(self, timeout_seconds: float = 5.0) -> bool:
        """快速检测 ComfyUI 是否在线（ping /system_stats）。"""
        if not self.server_address:
            return False
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as session:
                async with session.get(f'{self.server_address}/system_stats') as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _find_running_workspace(self, headers: dict) -> Optional[str]:
        """
        检查 workspace list 中是否已有 running 状态的 workspace。
        匹配条件：slug == self._CNB_REPO 且 status 不是 closed。
        返回公网 URL 或 None。
        """
        try:
            list_url = f'{self._CNB_API_BASE}/workspace/list?page=1&pageSize=50'
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(list_url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    list_data = await resp.json(content_type=None)

            for item in list_data.get('list', []):
                slug = str(item.get('slug') or '').strip()
                status = str(item.get('status') or '').strip()
                bid = str(item.get('business_id') or '').strip()
                if slug == self._CNB_REPO and status not in (
                    'closed', 'building', 'pending', 'queued', '', None
                ) and bid:
                    return f'https://{bid}-8188.cnb.run'
            return None
        except Exception as e:
            log.warning(f'查找已有 workspace 异常: {e}')
            return None

    async def _wait_comfyui_ready(
        self, url: str, max_attempts: int = 72, interval: float = 5.0
    ) -> bool:
        """轮询指定 URL 的 /system_stats 直到 ComfyUI 就绪。"""
        for attempt in range(max_attempts):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as session:
                    async with session.get(f'{url}/system_stats') as resp:
                        if resp.status == 200:
                            log.info(f'ComfyUI 已就绪! (轮询 {attempt + 1}/{max_attempts})')
                            return True
            except Exception:
                pass
            if (attempt + 1) % 3 == 0:
                log.info(f'等待 ComfyUI 启动中... (轮询 {attempt + 1}/{max_attempts})')
            await asyncio.sleep(interval)
        return False

    async def _start_cnb_workspace(self) -> Optional[str]:
        """
        自动启动 CNB workspace 并等待 ComfyUI 就绪。
        返回新的公网 URL，失败返回 None。
        流程：
        1. POST /{repo}/-/workspace/start → 获取 sn
        2. 轮询 workspace list → 等 status 变为 running，取 business_id
        3. 轮询新 URL /system_stats → 等 ComfyUI 就绪
        4. 更新 self.server_address + config + endpoints
        """
        if not self._CNB_TOKEN:
            log.error('CNB_TOKEN 未设置，无法自动启动 workspace')
            return None

        headers = {
            'Authorization': f'Bearer {self._CNB_TOKEN}',
            'Accept': 'application/vnd.cnb.api+json',
            'Content-Type': 'application/json',
        }

        try:
            # --- Step 0: 先检查是否已有 running 的 workspace（避免重复启动）---
            existing_url = await self._find_running_workspace(headers)
            if existing_url:
                log.info(f'发现已有 running 的 workspace: {existing_url}')
                # 验证该 workspace 的 ComfyUI 是否已就绪
                if await self._wait_comfyui_ready(existing_url):
                    self.server_address = self._normalize_server_address(existing_url)
                    app_config.COMFYUI_CONFIG['SERVER_ADDRESS'] = self.server_address
                    self._refresh_endpoints()
                    log.info(f'ComfyUI 服务地址已更新为: {self.server_address}')
                    return existing_url
                else:
                    log.warning(f'已有 workspace 的 ComfyUI 未就绪，将启动新 workspace')

            # --- Step 1: 提交启动请求 ---
            start_url = f'{self._CNB_API_BASE}/{self._CNB_REPO}/-/workspace/start'
            log.info(f'正在启动 CNB workspace: POST {start_url}')
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.post(
                    start_url, json={'branch': 'main'}, headers=headers
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error(f'启动 workspace 失败: status={resp.status}, body={body[:200]}')
                        return None
                    start_data = await resp.json(content_type=None)
                    workspace_sn = str(start_data.get('sn') or '').strip()
                    if not workspace_sn:
                        log.error(f'启动 workspace 返回缺少 sn: {start_data}')
                        return None
                    log.info(f'Workspace 启动请求已提交, sn={workspace_sn}')

            # --- Step 2: 轮询 workspace list 直到 status 变为 running ---
            # 优化：先等60秒再开始轮询（workspace构建至少需要~2分钟，早期轮询浪费时间）
            business_id = None
            initial_delay = 60  # 先等60秒
            max_poll = 48       # 60s + 48*5s = 300s max
            poll_interval = 5   # 5秒一次，更快发现就绪

            log.info(f'等待 {initial_delay}s 后开始轮询 workspace 状态（构建约需2-3分钟）...')
            await asyncio.sleep(initial_delay)

            for attempt in range(max_poll):
                await asyncio.sleep(poll_interval)
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as session:
                        list_url = f'{self._CNB_API_BASE}/workspace/list?page=1&pageSize=20'
                        async with session.get(list_url, headers=headers) as resp:
                            if resp.status != 200:
                                continue
                            list_data = await resp.json(content_type=None)

                        for item in list_data.get('list', []):
                            if item.get('sn') == workspace_sn:
                                status = str(item.get('status') or '').strip()
                                bid = str(item.get('business_id') or '').strip()
                                log.info(
                                    f'Workspace 轮询 {attempt + 1}/{max_poll}: '
                                    f'status={status}, business_id={bid}'
                                )
                                # 只要不是 closed/building/pending 就算就绪
                                if status and status not in (
                                    'closed', 'building', 'pending', 'queued', ''
                                ) and bid:
                                    business_id = bid
                                    break
                    if business_id:
                        break
                except Exception as e:
                    log.warning(f'轮询 workspace 状态异常: {e}')

            if not business_id:
                log.error(f'Workspace 启动超时 (sn={workspace_sn}, 等待 {max_poll * poll_interval}s)')
                return None

            # --- Step 3: 构造新公网 URL ---
            new_url = f'https://{business_id}-8188.cnb.run'
            log.info(f'Workspace 已就绪, 新公网地址: {new_url}')

            # --- Step 4: 等待 ComfyUI 服务启动 ---
            if not await self._wait_comfyui_ready(new_url):
                log.error(f'ComfyUI 在新 workspace 上未就绪: {new_url}')
                return None

            # --- Step 5: 更新服务地址 ---
            self.server_address = self._normalize_server_address(new_url)
            app_config.COMFYUI_CONFIG['SERVER_ADDRESS'] = self.server_address
            self._refresh_endpoints()
            log.info(f'ComfyUI 服务地址已更新为: {self.server_address}')

            return new_url

        except Exception as e:
            log.error(f'启动 CNB workspace 异常: {e}', exc_info=True)
            return None

    async def generate_media(
        self,
        prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        positive_prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        sampler: Optional[str] = None,
        scheduler: Optional[str] = None,
        lora: Optional[str] = None,
        lora_strength: Optional[float] = None,
        model_name: Optional[str] = None,
        vae_name: Optional[str] = None,
        clip_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]:
        try:
            if not self.is_server_ready():
                log.warning('ComfyUI 服务当前不可用，请检查开关或服务地址配置。')
                return None

            # --- 按需模式：检测 ComfyUI 是否在线，不在线则自动启动 CNB workspace ---
            if not await self._check_comfyui_online():
                log.info('ComfyUI 不可达，正在自动启动 CNB workspace（约需3分钟）...')
                # 尝试通过 Discord 消息通知用户画板正在预热
                try:
                    notify_msg = kwargs.get('discord_message')
                    if notify_msg and hasattr(notify_msg, 'channel') and hasattr(notify_msg.channel, 'send'):
                        await notify_msg.channel.send('🎨 画板正在预热中，约需3分钟，请稍等一下哦~')
                except Exception:
                    pass  # 通知失败不影响主流程
                new_url = await self._start_cnb_workspace()
                if not new_url:
                    log.error('无法启动 CNB workspace，请手动检查')
                    return None
                log.info(f'CNB workspace 已就绪: {new_url}')
            # --- 按需模式结束 ---

            workflow_path_override = self._normalize_workflow_path(kwargs.get('workflow_path'))
            workflow_template_override: Optional[Dict[str, Any]] = None
            resolved_prompt_style = self.resolve_prompt_style(
                prompt=prompt,
                positive_prompt=positive_prompt,
                prompt_style=kwargs.get('prompt_style'),
            )
            default_style_workflow_path = ''
            if not workflow_path_override:
                default_style_workflow_path = self.resolve_default_workflow_path(
                    prompt=prompt,
                    positive_prompt=positive_prompt,
                    prompt_style=resolved_prompt_style,
                )

            if workflow_path_override:
                workflow_template_override = self._load_workflow_template_from_path(workflow_path_override)
                if workflow_template_override is None:
                    log.warning(f'用户指定工作流加载失败: {workflow_path_override}，回退到默认工作流')
                    workflow_template_override = None
            elif default_style_workflow_path:
                workflow_template_override = self._load_workflow_template_from_path(default_style_workflow_path)
                if workflow_template_override is None:
                    log.warning(f'画风分流工作流加载失败，回退全局默认工作流: {default_style_workflow_path}')

            if workflow_template_override is None and self.workflow_template is None:
                log.warning('ComfyUI 默认工作流未加载，且未提供用户工作流。')
                return None

            runtime_kwargs = dict(kwargs)
            runtime_kwargs.pop('workflow_path', None)
            runtime_kwargs['prompt'] = prompt
            runtime_kwargs['positive_prompt'] = positive_prompt
            runtime_kwargs['negative_prompt'] = negative_prompt
            runtime_kwargs['seed'] = seed
            runtime_kwargs['width'] = width
            runtime_kwargs['height'] = height
            runtime_kwargs['steps'] = steps
            runtime_kwargs['cfg'] = cfg
            runtime_kwargs['sampler'] = sampler
            runtime_kwargs['scheduler'] = scheduler
            runtime_kwargs['lora'] = lora
            runtime_kwargs['lora_strength'] = lora_strength
            runtime_kwargs['model_name'] = model_name
            runtime_kwargs['vae_name'] = vae_name
            runtime_kwargs['clip_name'] = clip_name
            runtime_kwargs['prompt_style'] = resolved_prompt_style

            params = self._build_runtime_params(**runtime_kwargs)
            params = await self._fill_missing_runtime_names(
                params,
                workflow_template=workflow_template_override,
            )
            workflow_payload = self._prepare_workflow(
                params,
                workflow_template=workflow_template_override,
            )

            # OOM/error retry: keep retrying for up to 300s
            retry_max_attempts = 15
            retry_delay_seconds = 10
            media_meta = None
            for retry_attempt in range(retry_max_attempts):
                media_meta = await self._queue_prompt_and_wait_result(workflow_payload)
                if media_meta:
                    break
                if retry_attempt < retry_max_attempts - 1:
                    log.warning(
                        f'ComfyUI 生成失败 (尝试 {retry_attempt + 1}/{retry_max_attempts})，'
                        f'等待 {retry_delay_seconds}s 后重试'
                    )
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    log.error(f'ComfyUI 生成失败，已达到最大重试次数 {retry_max_attempts}')
            if not media_meta:
                return None

            media_bytes = await self._download_media(media_meta)
            if not media_bytes:
                return None

            return {
                'bytes': media_bytes,
                'filename': str(media_meta.get('filename') or '').strip() or 'comfyui_output.bin',
                'mime_type': str(media_meta.get('mime_type') or '').strip() or 'application/octet-stream',
                'media_kind': str(media_meta.get('media_kind') or '').strip() or 'image',
                'subfolder': str(media_meta.get('subfolder') or '').strip(),
                'type': str(media_meta.get('type') or '').strip() or 'output',
            }
        except Exception as error:
            log.error(f'ComfyUI 生成媒体失败: {error}', exc_info=True)
            return None

    async def generate_image(
        self,
        prompt: Optional[str] = None,
        negative_prompt: Optional[str] = None,
        seed: Optional[int] = None,
        positive_prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        steps: Optional[int] = None,
        cfg: Optional[float] = None,
        sampler: Optional[str] = None,
        scheduler: Optional[str] = None,
        lora: Optional[str] = None,
        lora_strength: Optional[float] = None,
        model_name: Optional[str] = None,
        vae_name: Optional[str] = None,
        clip_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[bytes]:
        media_result = await self.generate_media(
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            positive_prompt=positive_prompt,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            scheduler=scheduler,
            lora=lora,
            lora_strength=lora_strength,
            model_name=model_name,
            vae_name=vae_name,
            clip_name=clip_name,
            **kwargs,
        )
        if not media_result:
            return None

        media_kind = str(media_result.get('media_kind') or '').strip().lower()
        if media_kind != 'image':
            log.warning(f'ComfyUI 返回非图片媒体，generate_image 忽略: kind={media_kind}')
            return None

        media_bytes = media_result.get('bytes')
        if isinstance(media_bytes, bytes):
            return media_bytes
        return None

    async def _queue_prompt_and_wait_result(self, workflow_payload: Dict[str, Any]) -> Optional[Dict[str, str]]:
        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        poll_interval_seconds = self._coerce_float(
            app_config.COMFYUI_CONFIG.get('POLL_INTERVAL_SECONDS'),
            1.0,
        )
        poll_interval_seconds = max(0.2, poll_interval_seconds)

        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds + 30)
        payload = {
            'prompt': workflow_payload,
            'client_id': str(uuid.uuid4()),
        }

        async with self._request_semaphore:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                try:
                    async with session.post(self.prompt_url, json=payload) as response:
                        if response.status != 200:
                            response_text = await response.text()
                            log.error(
                                f'ComfyUI 提交任务失败: status={response.status}, body={response_text}'
                            )
                            return None

                        response_data = await response.json(content_type=None)
                except Exception as error:
                    log.error(f'ComfyUI 提交任务异常: {error}', exc_info=True)
                    return None

                prompt_id = str(response_data.get('prompt_id') or '').strip()
                if not prompt_id:
                    log.error(f'ComfyUI 返回缺少 prompt_id: {response_data}')
                    return None

                return await self._poll_history_for_media(
                    session=session,
                    prompt_id=prompt_id,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )

    def _extract_media_meta_from_outputs(self, outputs: Dict[str, Any]) -> Optional[Dict[str, str]]:
        preferred_output_node_id = str(app_config.COMFYUI_CONFIG.get('MEDIA_OUTPUT_NODE_ID') or '').strip()
        if not preferred_output_node_id:
            preferred_output_node_id = str(app_config.COMFYUI_CONFIG.get('IMAGE_OUTPUT_NODE_ID') or '').strip()

        if preferred_output_node_id and preferred_output_node_id in outputs:
            preferred_meta = self._extract_media_meta_from_output_node(outputs.get(preferred_output_node_id))
            if preferred_meta:
                return preferred_meta

        last_meta: Optional[Dict[str, str]] = None
        for output_node_data in outputs.values():
            meta = self._extract_media_meta_from_output_node(output_node_data)
            if meta:
                last_meta = meta

        return last_meta

    @staticmethod
    def _infer_media_kind_from_filename(filename: str, fallback_kind: str) -> tuple[str, str]:
        suffix = Path(str(filename or '').strip()).suffix.lower()
        if suffix in {'.png'}:
            return 'image', 'image/png'
        if suffix in {'.jpg', '.jpeg'}:
            return 'image', 'image/jpeg'
        if suffix in {'.webp'}:
            return 'image', 'image/webp'
        if suffix in {'.bmp'}:
            return 'image', 'image/bmp'
        if suffix in {'.avif'}:
            return 'image', 'image/avif'
        if suffix in {'.gif'}:
            return 'image', 'image/gif'
        if suffix in {'.mp4'}:
            return 'video', 'video/mp4'
        if suffix in {'.webm'}:
            return 'video', 'video/webm'
        if suffix in {'.mov'}:
            return 'video', 'video/quicktime'
        if suffix in {'.avi'}:
            return 'video', 'video/x-msvideo'
        if suffix in {'.mkv'}:
            return 'video', 'video/x-matroska'

        fallback_text = str(fallback_kind or '').strip().lower()
        if fallback_text == 'video':
            return 'video', 'video/mp4'
        return 'image', 'application/octet-stream'

    @classmethod
    def _normalize_declared_media_mime(cls, declared_format: Any, filename: str, fallback_kind: str) -> tuple[str, str]:
        format_text = str(declared_format or '').strip().lower()
        inferred_kind, inferred_mime = cls._infer_media_kind_from_filename(filename, fallback_kind)

        if format_text.startswith('video/'):
            if 'webm' in format_text:
                return 'video', 'video/webm'
            if 'quicktime' in format_text or 'mov' in format_text:
                return 'video', 'video/quicktime'
            return 'video', 'video/mp4'

        if format_text.startswith('image/'):
            return 'image', format_text

        return inferred_kind, inferred_mime

    @classmethod
    def _extract_media_meta_from_output_node(cls, output_node_data: Any) -> Optional[Dict[str, str]]:
        if not isinstance(output_node_data, dict):
            return None

        media_fields: list[tuple[str, str]] = [
            ('videos', 'video'),
            ('gifs', 'image'),
            ('images', 'image'),
        ]

        media_item: Optional[Dict[str, Any]] = None
        fallback_kind = 'image'
        for field_name, field_kind in media_fields:
            items = output_node_data.get(field_name)
            if isinstance(items, list) and items:
                last_item = items[-1]
                if isinstance(last_item, dict):
                    media_item = last_item
                    fallback_kind = field_kind
                    break

        if media_item is None:
            return None

        filename = str(media_item.get('filename') or '').strip()
        if not filename:
            return None

        subfolder = str(media_item.get('subfolder') or '').strip()
        media_type = str(media_item.get('type') or 'output').strip() or 'output'
        media_kind, mime_type = cls._normalize_declared_media_mime(
            declared_format=media_item.get('format'),
            filename=filename,
            fallback_kind=fallback_kind,
        )

        return {
            'filename': filename,
            'subfolder': subfolder,
            'type': media_type,
            'mime_type': mime_type,
            'media_kind': media_kind,
        }

    async def _poll_history_for_media(
        self,
        session: aiohttp.ClientSession,
        prompt_id: str,
        timeout_seconds: int,
        poll_interval_seconds: float,
    ) -> Optional[Dict[str, str]]:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        history_url = f'{self.history_url_base}/{prompt_id}'

        while asyncio.get_running_loop().time() < deadline:
            try:
                async with session.get(history_url) as response:
                    if response.status != 200:
                        await asyncio.sleep(poll_interval_seconds)
                        continue

                    history_payload = await response.json(content_type=None)
            except Exception as error:
                log.warning(f'轮询 ComfyUI history 异常: prompt_id={prompt_id}, error={error}')
                await asyncio.sleep(poll_interval_seconds)
                continue

            prompt_record = None
            if isinstance(history_payload, dict):
                prompt_record = history_payload.get(prompt_id)

            if not isinstance(prompt_record, dict):
                await asyncio.sleep(poll_interval_seconds)
                continue

            outputs = prompt_record.get('outputs')
            if isinstance(outputs, dict):
                media_meta = self._extract_media_meta_from_outputs(outputs)
                if media_meta:
                    return media_meta

            status = prompt_record.get('status')
            if isinstance(status, dict):
                status_text = str(status.get('status_str') or '').strip().lower()
                if status_text in {'error', 'failed'}:
                    log.error(f'ComfyUI 任务执行失败: prompt_id={prompt_id}, status={status}')
                    return None

            await asyncio.sleep(poll_interval_seconds)

        log.error(f'ComfyUI 任务超时: prompt_id={prompt_id}, timeout={timeout_seconds}s')
        return None

    async def _download_media(self, media_meta: Dict[str, str]) -> Optional[bytes]:
        filename = str(media_meta.get('filename') or '').strip()
        if not filename:
            return None

        params: Dict[str, str] = {'filename': filename}
        subfolder = str(media_meta.get('subfolder') or '').strip()
        image_type = str(media_meta.get('type') or '').strip()
        if subfolder:
            params['subfolder'] = subfolder
        if image_type:
            params['type'] = image_type

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(self.view_url, params=params) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        log.error(
                            f'ComfyUI 下载媒体失败: status={response.status}, body={response_text}'
                        )
                        return None
                    return await response.read()
        except Exception as error:
            log.error(f'ComfyUI 下载媒体异常: {error}', exc_info=True)
            return None

    async def upload_input_image(self, image_bytes: bytes, filename: Optional[str] = None) -> Optional[str]:
        if not self.server_address:
            return None

        if not isinstance(image_bytes, (bytes, bytearray)) or not image_bytes:
            return None

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=min(timeout_seconds, 60))
        upload_url = f'{self.server_address}/upload/image'
        safe_filename = self._sanitize_upload_filename(filename)

        form = aiohttp.FormData()
        form.add_field(
            'image',
            image_bytes,
            filename=safe_filename,
            content_type='application/octet-stream',
        )
        form.add_field('type', 'input')
        form.add_field('overwrite', 'false')

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(upload_url, data=form) as response:
                    payload = await self._read_response_payload(response)
                    if response.status < 200 or response.status >= 300:
                        log.error(f'ComfyUI 上传输入图失败: status={response.status}, body={payload}')
                        return None

                    if isinstance(payload, dict):
                        upload_name = str(payload.get('name') or payload.get('filename') or '').strip()
                        upload_subfolder = str(payload.get('subfolder') or '').strip()
                        if upload_name and upload_subfolder:
                            return f'{upload_subfolder}/{upload_name}'
                        if upload_name:
                            return upload_name

                    if isinstance(payload, str):
                        payload_text = payload.strip()
                        if payload_text:
                            return payload_text

                    return safe_filename
        except Exception as error:
            log.error(f'ComfyUI 上传输入图异常: {error}', exc_info=True)
            return None

    @staticmethod
    async def _read_response_payload(response: aiohttp.ClientResponse) -> Any:
        try:
            return await response.json(content_type=None)
        except Exception:
            try:
                return await response.text()
            except Exception:
                return ''

    @staticmethod
    def _normalize_download_url_for_match(raw_url: str) -> str:
        url_text = str(raw_url or '').strip()
        if not url_text:
            return ''

        parsed = urlparse(url_text)
        scheme = str(parsed.scheme or '').lower()
        netloc = str(parsed.netloc or '').lower()
        path = str(parsed.path or '').strip()
        if not scheme or not netloc:
            return url_text.rstrip('/').lower()
        return f"{scheme}://{netloc}{path.rstrip('/')}"

    @staticmethod
    def _extract_filename_from_download_url(raw_url: str) -> str:
        url_text = str(raw_url or '').strip()
        if not url_text:
            return ''

        parsed = urlparse(url_text)
        query = parse_qs(parsed.query)
        for key, values in query.items():
            if str(key or '').strip().lower() != 'response-content-disposition':
                continue

            for value in values:
                decoded = unquote(str(value or '').strip())
                if not decoded:
                    continue
                match = re.search(
                    r"filename\*?=(?:UTF-8''|utf-8''|)?\"?([^\";]+)",
                    decoded,
                    flags=re.IGNORECASE,
                )
                if match:
                    filename = Path(match.group(1).strip()).name
                    if filename:
                        return filename

        return Path(str(parsed.path or '').strip()).name

    @staticmethod
    def _build_lora_install_payload(
        url: str,
        filename: Optional[str] = None,
        save_path: Optional[str] = None,
        base: Optional[str] = None,
        model_type: Optional[str] = None,
        ui_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_url = str(url or '').strip()
        normalized_filename = str(filename or '').strip()
        normalized_save_path = str(save_path or '').strip()
        normalized_base = str(base or '').strip().lower()
        normalized_model_type = str(model_type or '').strip().lower()

        inferred_filename = Path(urlparse(normalized_url).path).name if normalized_url else ''
        if not normalized_filename:
            normalized_filename = inferred_filename or 'downloaded_lora.safetensors'

        normalized_filename = ComfyUIService._sanitize_lora_filename(normalized_filename)

        if normalized_model_type in {'', 'lora', 'loras'}:
            normalized_model_type = 'lora'
        if normalized_base in {'', 'none', 'lora', 'loras'}:
            normalized_base = 'lora'

        model_display_name = Path(normalized_filename).stem.replace('_', ' ').strip() or 'LoRA'
        normalized_ui_id = str(ui_id or '').strip() or 'odysseia-bot'

        return {
            'type': normalized_model_type,
            'url': normalized_url,
            'filename': normalized_filename,
            'save_path': normalized_save_path or 'default',
            'base': normalized_base,
            'name': model_display_name,
            'ui_id': normalized_ui_id,
        }

    @staticmethod
    def _sanitize_lora_filename(raw_name: str, fallback_name: str = 'downloaded_lora.safetensors') -> str:
        base_name = Path(str(raw_name or '').strip()).name
        if not base_name:
            base_name = fallback_name

        safe_name = re.sub(r'[^0-9A-Za-z._\-]+', '_', base_name)
        safe_name = safe_name.strip('._') or fallback_name
        stem = Path(safe_name).stem or 'downloaded_lora'
        return f'{stem}.safetensors'

    def _resolve_shared_lora_dir(self) -> Optional[Path]:
        directory_text = str(app_config.COMFYUI_CONFIG.get('SHARED_LORA_DIR') or '').strip()
        if not directory_text:
            return None

        try:
            shared_dir = Path(directory_text)
            shared_dir.mkdir(parents=True, exist_ok=True)
            return shared_dir
        except Exception as error:
            log.warning(f'创建共享 LoRA 目录失败: {directory_text}, error={error}')
            return None

    @staticmethod
    def _build_unique_target_path(base_dir: Path, filename: str) -> Path:
        candidate = base_dir / filename
        if not candidate.exists():
            return candidate

        stem = candidate.stem
        suffix = candidate.suffix
        for index in range(1, 10000):
            temp_candidate = base_dir / f'{stem}_{index}{suffix}'
            if not temp_candidate.exists():
                return temp_candidate

        return base_dir / f'{stem}_{uuid.uuid4().hex[:8]}{suffix}'

    def _resolve_lora_download_limit_bytes(self) -> int:
        max_mb = self._coerce_int(app_config.COMFYUI_CONFIG.get('LORA_DOWNLOAD_MAX_MB'), 100)
        if max_mb <= 0:
            max_mb = 100
        return max_mb * 1024 * 1024

    async def _download_lora_to_shared_dir(
        self,
        url: str,
        filename: Optional[str] = None,
    ) -> Dict[str, Any]:
        shared_dir = self._resolve_shared_lora_dir()
        if shared_dir is None:
            return {
                'success': False,
                'error': '未配置共享 LoRA 目录（COMFYUI_SHARED_LORA_DIR），无法启用回退下载。',
            }

        safe_filename = self._sanitize_lora_filename(
            str(filename or '').strip() or self._extract_filename_from_download_url(url),
        )
        final_path = self._build_unique_target_path(shared_dir, safe_filename)
        temp_path = final_path.with_suffix(f'{final_path.suffix}.part')

        max_bytes = self._resolve_lora_download_limit_bytes()
        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=max(timeout_seconds, 30))

        written_bytes = 0
        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.get(url) as response:
                    if response.status < 200 or response.status >= 300:
                        body_text = await response.text()
                        return {
                            'success': False,
                            'error': f'回退下载失败，HTTP {response.status}: {body_text[:300]}',
                        }

                    content_length = self._coerce_int(response.headers.get('Content-Length'), -1)
                    if content_length > max_bytes:
                        return {
                            'success': False,
                            'error': f'回退下载失败：文件超过大小限制（>{max_bytes // (1024 * 1024)}MB）。',
                        }

                    with temp_path.open('wb') as output_file:
                        async for chunk in response.content.iter_chunked(1024 * 512):
                            if not chunk:
                                continue
                            written_bytes += len(chunk)
                            if written_bytes > max_bytes:
                                raise ValueError(f'回退下载失败：文件超过大小限制（>{max_bytes // (1024 * 1024)}MB）。')
                            output_file.write(chunk)

            temp_path.replace(final_path)
            return {
                'success': True,
                'saved_filename': final_path.name,
                'saved_path': str(final_path),
                'bytes': written_bytes,
            }
        except Exception as error:
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except Exception:
                pass
            return {
                'success': False,
                'error': str(error),
            }

    async def download_lora_from_url(
        self,
        url: str,
        filename: Optional[str] = None,
        save_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_url = str(url or '').strip()
        if not normalized_url:
            return {'success': False, 'error': 'LoRA 下载链接不能为空'}

        if not (normalized_url.startswith('http://') or normalized_url.startswith('https://')):
            return {'success': False, 'error': 'LoRA 下载链接必须以 http:// 或 https:// 开头'}

        if not self.server_address:
            return {'success': False, 'error': 'ComfyUI SERVER_ADDRESS 未配置'}

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        install_urls = [
            f'{self.server_address}/manager/queue/install_model',
            f'{self.server_address}/manager/install_model',
        ]
        model_list_url = f'{self.server_address}/externalmodel/getlist'
        start_url = f'{self.server_address}/manager/queue/start'

        normalized_target_url = self._normalize_download_url_for_match(normalized_url)
        inferred_filename = str(filename or self._extract_filename_from_download_url(normalized_url)).strip()

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                matched_whitelist_item: Optional[Dict[str, Any]] = None
                matched_by_filename_item: Optional[Dict[str, Any]] = None

                for mode in ('cache', 'local'):
                    try:
                        async with session.get(model_list_url, params={'mode': mode}) as model_response:
                            if model_response.status < 200 or model_response.status >= 300:
                                continue
                            model_payload = await self._read_response_payload(model_response)
                    except Exception:
                        continue

                    if not isinstance(model_payload, dict):
                        continue

                    models = model_payload.get('models')
                    if not isinstance(models, list):
                        continue

                    for item in models:
                        if not isinstance(item, dict):
                            continue

                        item_url = str(item.get('url') or '').strip()
                        item_filename = str(item.get('filename') or '').strip()
                        item_type = str(item.get('type') or '').strip().lower()

                        if item_url and self._normalize_download_url_for_match(item_url) == normalized_target_url:
                            matched_whitelist_item = item
                            break

                        if (
                            matched_by_filename_item is None
                            and inferred_filename
                            and item_filename
                            and item_filename.lower() == inferred_filename.lower()
                            and ('lora' in item_type or item_type in {'', 'loras'})
                        ):
                            matched_by_filename_item = item

                    if matched_whitelist_item is not None:
                        break

                if matched_whitelist_item is None and matched_by_filename_item is not None:
                    matched_whitelist_item = matched_by_filename_item

                if matched_whitelist_item is not None:
                    payload = self._build_lora_install_payload(
                        url=normalized_url,
                        filename=str(matched_whitelist_item.get('filename') or inferred_filename or '').strip(),
                        save_path=str(matched_whitelist_item.get('save_path') or save_path or '').strip(),
                        base=str(matched_whitelist_item.get('base') or '').strip(),
                        model_type=str(matched_whitelist_item.get('type') or 'loras').strip(),
                    )
                else:
                    payload = self._build_lora_install_payload(
                        url=normalized_url,
                        filename=inferred_filename,
                        save_path=save_path,
                    )

                install_result: Any = None
                install_status: Optional[int] = None
                install_endpoint = ''

                for index, install_url in enumerate(install_urls):
                    async with session.post(install_url, json=payload) as response:
                        install_status = response.status
                        install_result = await self._read_response_payload(response)
                        install_endpoint = install_url

                    if install_status is None:
                        continue

                    if 200 <= install_status < 300:
                        break

                    has_next_endpoint = index < len(install_urls) - 1
                    if has_next_endpoint and install_status in {404, 405}:
                        continue

                    break

                if install_status is None or install_status < 200 or install_status >= 300:
                    detail_text = str(install_result or '').strip()
                    if 'Invalid model install request' in detail_text:
                        fallback_result = await self._download_lora_to_shared_dir(
                            url=normalized_url,
                            filename=inferred_filename,
                        )
                        if fallback_result.get('success'):
                            return {
                                'success': True,
                                'message': 'LoRA 不在白名单，已回退为共享目录直链下载。',
                                'fallback_mode': 'shared_lora_dir',
                                'saved_filename': str(fallback_result.get('saved_filename') or ''),
                                'saved_path': str(fallback_result.get('saved_path') or ''),
                                'whitelist_matched': False,
                            }

                        fallback_error = str(fallback_result.get('error') or '').strip()
                        detail_text = (
                            '当前 ComfyUI-Manager 仅允许安装 model-list 白名单中的模型；'
                            f'共享目录回退也失败：{fallback_error or "未知错误"}。'
                            '请在 Bot 端配置 COMFYUI_SHARED_LORA_DIR，或将该模型加入白名单。'
                        )

                    return {
                        'success': False,
                        'error': detail_text or '调用 ComfyUI-Manager 安装 LoRA 失败，请确认已安装 Manager 插件。',
                        'status': install_status,
                        'response': install_result,
                        'endpoint': install_endpoint,
                        'payload': payload,
                        'whitelist_matched': matched_whitelist_item is not None,
                    }

                queue_start_result: Any = None
                queue_start_status: Optional[int] = None
                queue_start_warning: Optional[str] = None

                try:
                    async with session.get(start_url) as start_response:
                        queue_start_status = start_response.status
                        queue_start_result = await self._read_response_payload(start_response)
                        if start_response.status < 200 or start_response.status >= 300:
                            queue_start_warning = '安装任务已提交，但启动队列失败，请检查 ComfyUI-Manager 队列状态。'
                except Exception as error:
                    queue_start_warning = f'安装任务已提交，但启动队列请求异常: {error}'

                return {
                    'success': True,
                    'message': 'LoRA 下载任务已提交到 ComfyUI-Manager 队列。',
                    'install_result': install_result,
                    'queue_start_status': queue_start_status,
                    'queue_start_result': queue_start_result,
                    'queue_start_warning': queue_start_warning,
                    'endpoint': install_endpoint,
                    'whitelist_matched': matched_whitelist_item is not None,
                    'saved_filename': str(payload.get('filename') or ''),
                }
        except Exception as error:
            log.error(f'下载 LoRA 到 ComfyUI 失败: {error}', exc_info=True)
            return {
                'success': False,
                'error': f'下载 LoRA 时发生异常: {error}',
            }


    async def install_custom_node_from_url(self, git_url: str) -> Dict[str, Any]:
        normalized_url = self._sanitize_invisible_chars(git_url)
        if not normalized_url:
            return {'success': False, 'error': '插件节点链接不能为空'}

        if not (normalized_url.startswith('http://') or normalized_url.startswith('https://')):
            return {'success': False, 'error': '插件节点链接必须以 http:// 或 https:// 开头'}

        if not self.server_address:
            return {'success': False, 'error': 'ComfyUI SERVER_ADDRESS 未配置'}

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        install_url = f'{self.server_address}/customnode/install/git_url'
        queue_start_url = f'{self.server_address}/manager/queue/start'

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(
                    install_url,
                    data=normalized_url.encode('utf-8'),
                    headers={'Content-Type': 'text/plain; charset=utf-8'},
                ) as response:
                    install_result = await self._read_response_payload(response)
                    if response.status < 200 or response.status >= 300:
                        return {
                            'success': False,
                            'error': '调用 ComfyUI-Manager 安装插件节点失败，请确认已安装 Manager 插件。',
                            'status': response.status,
                            'response': install_result,
                        }

                queue_start_result: Any = None
                queue_start_status: Optional[int] = None
                queue_start_warning: Optional[str] = None

                try:
                    async with session.get(queue_start_url) as start_response:
                        queue_start_status = start_response.status
                        queue_start_result = await self._read_response_payload(start_response)
                        if start_response.status < 200 or start_response.status >= 300:
                            queue_start_warning = '插件安装任务已提交，但启动队列失败，请检查 ComfyUI-Manager 队列状态。'
                except Exception as error:
                    queue_start_warning = f'插件安装任务已提交，但启动队列请求异常: {error}'

                return {
                    'success': True,
                    'message': '插件节点安装任务已提交到 ComfyUI-Manager。',
                    'install_result': install_result,
                    'queue_start_status': queue_start_status,
                    'queue_start_result': queue_start_result,
                    'queue_start_warning': queue_start_warning,
                }
        except Exception as error:
            log.error(f'安装 ComfyUI 插件节点失败: {error}', exc_info=True)
            return {
                'success': False,
                'error': f'安装插件节点时发生异常: {error}',
            }

    async def test_connection(self) -> Dict[str, Any]:
        if not self.server_address:
            return {'success': False, 'error': 'ComfyUI SERVER_ADDRESS 未配置'}

        timeout = aiohttp.ClientTimeout(total=10)
        check_urls = [
            f'{self.server_address}/system_stats',
            f'{self.server_address}/queue',
        ]

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for target_url in check_urls:
                try:
                    async with session.get(target_url) as response:
                        if response.status == 200:
                            return {
                                'success': True,
                                'message': 'ComfyUI 连接成功',
                                'url': target_url,
                                'workflow_loaded': self.workflow_template is not None,
                                'workflow_path': self.workflow_path,
                            }
                except Exception:
                    continue

        return {
            'success': False,
            'error': '无法连接 ComfyUI 服务，请检查地址与网络',
            'workflow_loaded': self.workflow_template is not None,
            'workflow_path': self.workflow_path,
        }


comfyui_service = ComfyUIService()
