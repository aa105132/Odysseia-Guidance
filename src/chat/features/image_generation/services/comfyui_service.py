# -*- coding: utf-8 -*-

import asyncio
import copy
import json
import logging
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)

_PLACEHOLDER_RE = re.compile(r'\{\{\s*([a-zA-Z0-9_]+)\s*\}\}')


class ComfyUIService:
    '''处理与 ComfyUI API 通信的业务逻辑（支持工作流导入与占位符替换）。'''

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

    def _load_workflow_template_from_path(self, workflow_path_text: str) -> Optional[Dict[str, Any]]:
        normalized_path = self._normalize_workflow_path(workflow_path_text)
        if not normalized_path:
            return None

        try:
            workflow_path = Path(normalized_path)
            with workflow_path.open('r', encoding='utf-8') as file:
                workflow_data = json.load(file)

            if not isinstance(workflow_data, dict):
                log.error(f'ComfyUI 工作流格式无效，需为 JSON 对象: {normalized_path}')
                return None

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
        if not isinstance(parsed, dict):
            raise ValueError('工作流 JSON 必须是对象（object）')

        save_path = Path(target_path)
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )
        return str(save_path)

    def _build_runtime_params(self, **kwargs: Any) -> Dict[str, Any]:
        config = app_config.COMFYUI_CONFIG

        positive_prompt = kwargs.get('positive_prompt')
        if positive_prompt is None:
            positive_prompt = kwargs.get('prompt')

        if 'lora' in kwargs:
            lora_value = kwargs.get('lora')
        elif 'lora_name' in kwargs:
            lora_value = kwargs.get('lora_name')
        else:
            lora_value = config.get('DEFAULT_LORA')

        fixed_positive_prompt = str(config.get('FIXED_POSITIVE_PROMPT') or '').strip()
        fixed_negative_prompt = str(config.get('FIXED_NEGATIVE_PROMPT') or '').strip()

        merged_positive_prompt = self._merge_fixed_prompt(positive_prompt, fixed_positive_prompt)
        merged_negative_prompt = self._merge_fixed_prompt(kwargs.get('negative_prompt'), fixed_negative_prompt)

        params: Dict[str, Any] = {
            'positive_prompt': merged_positive_prompt,
            'negative_prompt': merged_negative_prompt,
            'width': self._coerce_int(kwargs.get('width'), self._coerce_int(config.get('DEFAULT_WIDTH'), 832)),
            'height': self._coerce_int(kwargs.get('height'), self._coerce_int(config.get('DEFAULT_HEIGHT'), 1216)),
            'steps': self._coerce_int(kwargs.get('steps'), self._coerce_int(config.get('DEFAULT_STEPS'), 28)),
            'cfg': self._coerce_float(kwargs.get('cfg'), self._coerce_float(config.get('DEFAULT_CFG'), 5.0)),
            'sampler': str(kwargs.get('sampler') or config.get('DEFAULT_SAMPLER') or '').strip(),
            'scheduler': str(kwargs.get('scheduler') or config.get('DEFAULT_SCHEDULER') or '').strip(),
            'seed': self._coerce_int(kwargs.get('seed'), self._coerce_int(config.get('DEFAULT_SEED'), 12345)),
            'lora': str(lora_value or '').strip(),
            'lora_strength': self._coerce_float(
                kwargs.get('lora_strength'),
                self._coerce_float(config.get('DEFAULT_LORA_STRENGTH'), 1.0),
            ),
            'model_name': str(kwargs.get('model_name') or config.get('DEFAULT_MODEL_NAME') or '').strip(),
            'vae_name': str(kwargs.get('vae_name') or '').strip(),
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
            'cfg': ['%cfg%'],
            'sampler': ['%sampler%'],
            'scheduler': ['%scheduler%'],
            'seed': ['%seed%'],
            'lora': ['%lora%'],
            'lora_strength': ['%lora_strength%'],
            'model_name': ['%MODEL_NAME%', '%model_name%'],
            'vae_name': ['%VAE_NAME%', '%vae_name%'],
        }

        for key_text, tokens in common_aliases.items():
            value = params.get(key_text)
            if value is None:
                continue
            for token_text in tokens:
                token_values[token_text] = value

        return token_values

    async def get_available_model_names(self) -> list[str]:
        return await self._get_available_choice_names(('ckpt_name', 'model_name'))

    async def get_available_lora_names(self) -> list[str]:
        return await self._get_available_choice_names(('lora_name',))

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
        **kwargs: Any,
    ) -> Optional[bytes]:
        try:
            if not self.is_server_ready():
                log.warning('ComfyUI 服务当前不可用，请检查开关或服务地址配置。')
                return None

            workflow_path_override = self._normalize_workflow_path(kwargs.get('workflow_path'))
            workflow_template_override: Optional[Dict[str, Any]] = None

            if workflow_path_override:
                workflow_template_override = self._load_workflow_template_from_path(workflow_path_override)
                if workflow_template_override is None:
                    log.error(f'用户指定工作流加载失败: {workflow_path_override}')
                    return None
            elif self.workflow_template is None:
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

            params = self._build_runtime_params(**runtime_kwargs)
            workflow_payload = self._prepare_workflow(
                params,
                workflow_template=workflow_template_override,
            )

            image_meta = await self._queue_prompt_and_wait_result(workflow_payload)
            if not image_meta:
                return None

            return await self._download_image(image_meta)
        except Exception as error:
            log.error(f'ComfyUI 生成图片失败: {error}', exc_info=True)
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

                return await self._poll_history_for_image(
                    session=session,
                    prompt_id=prompt_id,
                    timeout_seconds=timeout_seconds,
                    poll_interval_seconds=poll_interval_seconds,
                )

    def _extract_image_meta_from_outputs(self, outputs: Dict[str, Any]) -> Optional[Dict[str, str]]:
        preferred_output_node_id = str(app_config.COMFYUI_CONFIG.get('IMAGE_OUTPUT_NODE_ID') or '').strip()
        if preferred_output_node_id and preferred_output_node_id in outputs:
            preferred_meta = self._extract_image_meta_from_output_node(outputs.get(preferred_output_node_id))
            if preferred_meta:
                return preferred_meta

        for output_node_data in outputs.values():
            meta = self._extract_image_meta_from_output_node(output_node_data)
            if meta:
                return meta

        return None

    @staticmethod
    def _extract_image_meta_from_output_node(output_node_data: Any) -> Optional[Dict[str, str]]:
        if not isinstance(output_node_data, dict):
            return None

        images = output_node_data.get('images')
        if not isinstance(images, list) or not images:
            return None

        first_image = images[0]
        if not isinstance(first_image, dict):
            return None

        filename = str(first_image.get('filename') or '').strip()
        if not filename:
            return None

        subfolder = str(first_image.get('subfolder') or '').strip()
        image_type = str(first_image.get('type') or 'output').strip() or 'output'
        return {
            'filename': filename,
            'subfolder': subfolder,
            'type': image_type,
        }

    async def _poll_history_for_image(
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
                image_meta = self._extract_image_meta_from_outputs(outputs)
                if image_meta:
                    return image_meta

            status = prompt_record.get('status')
            if isinstance(status, dict):
                status_text = str(status.get('status_str') or '').strip().lower()
                if status_text in {'error', 'failed'}:
                    log.error(f'ComfyUI 任务执行失败: prompt_id={prompt_id}, status={status}')
                    return None

            await asyncio.sleep(poll_interval_seconds)

        log.error(f'ComfyUI 任务超时: prompt_id={prompt_id}, timeout={timeout_seconds}s')
        return None

    async def _download_image(self, image_meta: Dict[str, str]) -> Optional[bytes]:
        filename = str(image_meta.get('filename') or '').strip()
        if not filename:
            return None

        params: Dict[str, str] = {'filename': filename}
        subfolder = str(image_meta.get('subfolder') or '').strip()
        image_type = str(image_meta.get('type') or '').strip()
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
                            f'ComfyUI 下载图片失败: status={response.status}, body={response_text}'
                        )
                        return None
                    return await response.read()
        except Exception as error:
            log.error(f'ComfyUI 下载图片异常: {error}', exc_info=True)
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

        payload: Dict[str, Any] = {
            'type': 'loras',
            'url': normalized_url,
        }

        normalized_filename = str(filename or '').strip()
        normalized_save_path = str(save_path or '').strip()
        if normalized_filename:
            payload['filename'] = normalized_filename
        if normalized_save_path:
            payload['save_path'] = normalized_save_path

        timeout_seconds = self._coerce_int(
            app_config.COMFYUI_CONFIG.get('REQUEST_TIMEOUT_SECONDS'),
            180,
        )
        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        install_url = f'{self.server_address}/manager/queue/install_model'
        start_url = f'{self.server_address}/manager/queue/start'

        try:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                async with session.post(install_url, json=payload) as response:
                    install_result = await self._read_response_payload(response)
                    if response.status < 200 or response.status >= 300:
                        return {
                            'success': False,
                            'error': '调用 ComfyUI-Manager 安装 LoRA 失败，请确认已安装 Manager 插件。',
                            'status': response.status,
                            'response': install_result,
                        }

                queue_start_result: Any = None
                queue_start_status: Optional[int] = None
                queue_start_warning: Optional[str] = None

                try:
                    async with session.post(start_url, json={}) as start_response:
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
                }
        except Exception as error:
            log.error(f'下载 LoRA 到 ComfyUI 失败: {error}', exc_info=True)
            return {
                'success': False,
                'error': f'下载 LoRA 时发生异常: {error}',
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
