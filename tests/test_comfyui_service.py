import asyncio
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.chat.config import chat_config
from src.chat.features.image_generation.services.comfyui_service import ComfyUIService


def test_save_workflow_text_writes_json_file(tmp_path):
    workflow_text = json.dumps({'1': {'inputs': {'text': 'hello'}}}, ensure_ascii=False)
    target_path = tmp_path / 'workflow.json'

    saved_path = ComfyUIService.save_workflow_text(workflow_text, str(target_path))

    assert saved_path == str(target_path)
    assert target_path.exists()
    loaded = json.loads(target_path.read_text(encoding='utf-8'))
    assert loaded['1']['inputs']['text'] == 'hello'


def test_prepare_workflow_applies_node_mapping_and_placeholders(tmp_path):
    workflow_template = {
        '1': {'inputs': {'text': '{{positive_prompt}}'}},
        '2': {'inputs': {'text': '{{negative_prompt}}'}},
        '3': {'inputs': {'steps': 20}},
        '4': {'inputs': {'cfg': '{{cfg}}'}},
        '5': {'inputs': {'width': 512}},
        '6': {'inputs': {'ckpt_name': '%MODEL_NAME%'}},
    }
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(
        json.dumps(workflow_template, ensure_ascii=False),
        encoding='utf-8',
    )

    original_placeholder_mapping = chat_config.COMFYUI_CONFIG.get('PLACEHOLDER_MAPPING')
    original_node_mapping = chat_config.COMFYUI_CONFIG.get('NODE_MAPPING')

    try:
        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = {
            'positive_prompt': '{{positive_prompt}}',
            'negative_prompt': '{{negative_prompt}}',
            'cfg': '{{cfg}}',
        }
        chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = {
            'steps': ['3', 'steps'],
            'width': ['5', 'width'],
        }

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )
        params = service._build_runtime_params(
            prompt='银狐少女',
            negative_prompt='lowres',
            width=896,
            steps=36,
            cfg=6.5,
            model_name='fooModel_v1.safetensors',
        )

        prepared_workflow = service._prepare_workflow(params)

        assert prepared_workflow['1']['inputs']['text'] == '银狐少女'
        assert prepared_workflow['2']['inputs']['text'] == 'lowres'
        assert prepared_workflow['3']['inputs']['steps'] == 36
        assert prepared_workflow['4']['inputs']['cfg'] == 6.5
        assert prepared_workflow['5']['inputs']['width'] == 896
        assert prepared_workflow['6']['inputs']['ckpt_name'] == 'fooModel_v1.safetensors'
    finally:
        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = original_placeholder_mapping
        chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = original_node_mapping




def test_prepare_workflow_supports_percent_style_alias_placeholders(tmp_path):
    workflow_template = {
        '1': {
            'inputs': {
                'cfg': '%cfg_scale%',
                'sampler_name': '%sampler_name%',
                'scheduler': '%scheduler_name%',
                'ckpt_name': '%ckpt_name%',
            }
        }
    }
    workflow_path = tmp_path / 'workflow_alias_template.json'
    workflow_path.write_text(
        json.dumps(workflow_template, ensure_ascii=False),
        encoding='utf-8',
    )

    original_placeholder_mapping = chat_config.COMFYUI_CONFIG.get('PLACEHOLDER_MAPPING')
    original_node_mapping = chat_config.COMFYUI_CONFIG.get('NODE_MAPPING')

    try:
        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = {}
        chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = {}

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )
        params = service._build_runtime_params(
            prompt='机械姬',
            cfg=7.2,
            sampler='dpmpp_2m',
            scheduler='karras',
            model_name='my_model_v3.safetensors',
        )

        prepared_workflow = service._prepare_workflow(params)

        assert prepared_workflow['1']['inputs']['cfg'] == 7.2
        assert prepared_workflow['1']['inputs']['sampler_name'] == 'dpmpp_2m'
        assert prepared_workflow['1']['inputs']['scheduler'] == 'karras'
        assert prepared_workflow['1']['inputs']['ckpt_name'] == 'my_model_v3.safetensors'
    finally:
        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = original_placeholder_mapping
        chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = original_node_mapping



def test_build_runtime_params_supports_user_fixed_prompts(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps({'1': {'inputs': {'text': '{{positive_prompt}}'}}}, ensure_ascii=False), encoding='utf-8')

    original_fixed_positive = chat_config.COMFYUI_CONFIG.get('FIXED_POSITIVE_PROMPT')
    original_fixed_negative = chat_config.COMFYUI_CONFIG.get('FIXED_NEGATIVE_PROMPT')

    try:
        chat_config.COMFYUI_CONFIG['FIXED_POSITIVE_PROMPT'] = 'global quality'
        chat_config.COMFYUI_CONFIG['FIXED_NEGATIVE_PROMPT'] = 'global lowres'

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )

        params = service._build_runtime_params(
            prompt='forest spirit',
            negative_prompt='blurry',
            user_fixed_positive_prompt='user style',
            user_fixed_negative_prompt='user no_nsfw',
        )

        assert params['positive_prompt'] == 'global quality, user style, forest spirit'
        assert params['negative_prompt'] == 'global lowres, user no_nsfw, blurry'
    finally:
        chat_config.COMFYUI_CONFIG['FIXED_POSITIVE_PROMPT'] = original_fixed_positive
        chat_config.COMFYUI_CONFIG['FIXED_NEGATIVE_PROMPT'] = original_fixed_negative
def test_build_runtime_params_merges_fixed_prompts_and_default_model(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps({'1': {'inputs': {'text': '{{positive_prompt}}'}}}, ensure_ascii=False), encoding='utf-8')

    original_fixed_positive = chat_config.COMFYUI_CONFIG.get('FIXED_POSITIVE_PROMPT')
    original_fixed_negative = chat_config.COMFYUI_CONFIG.get('FIXED_NEGATIVE_PROMPT')
    original_default_model_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_MODEL_NAME')

    try:
        chat_config.COMFYUI_CONFIG['FIXED_POSITIVE_PROMPT'] = 'masterpiece, best quality'
        chat_config.COMFYUI_CONFIG['FIXED_NEGATIVE_PROMPT'] = 'lowres, bad anatomy'
        chat_config.COMFYUI_CONFIG['DEFAULT_MODEL_NAME'] = 'global_default_model.safetensors'

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )

        params = service._build_runtime_params(
            prompt='1girl, sunset',
            negative_prompt='blur',
        )

        assert params['positive_prompt'] == 'masterpiece, best quality, 1girl, sunset'
        assert params['negative_prompt'] == 'lowres, bad anatomy, blur'
        assert params['model_name'] == 'global_default_model.safetensors'
    finally:
        chat_config.COMFYUI_CONFIG['FIXED_POSITIVE_PROMPT'] = original_fixed_positive
        chat_config.COMFYUI_CONFIG['FIXED_NEGATIVE_PROMPT'] = original_fixed_negative
        chat_config.COMFYUI_CONFIG['DEFAULT_MODEL_NAME'] = original_default_model_name


def test_install_custom_node_from_url_rejects_empty_url():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')

    result = asyncio.run(service.install_custom_node_from_url(''))

    assert result.get('success') is False
    assert '不能为空' in str(result.get('error') or '')


def test_install_custom_node_from_url_rejects_non_http_url():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')

    result = asyncio.run(service.install_custom_node_from_url('ftp://example.com/repo.git'))

    assert result.get('success') is False
    assert 'http://' in str(result.get('error') or '')


def test_install_custom_node_from_url_requires_server_address():
    service = ComfyUIService(server_address='', workflow_path='')

    result = asyncio.run(service.install_custom_node_from_url('https://github.com/comfyanonymous/ComfyUI-Manager'))

    assert result.get('success') is False
    assert 'SERVER_ADDRESS' in str(result.get('error') or '')


def test_build_lora_install_payload_includes_required_keys_with_defaults():
    payload = ComfyUIService._build_lora_install_payload(
        url='https://example.com/models/my_lora.safetensors',
        filename=None,
        save_path=None,
    )

    assert payload['type'] == 'loras'
    assert payload['url'] == 'https://example.com/models/my_lora.safetensors'
    assert payload['filename'] == 'my_lora.safetensors'
    assert payload['save_path'] == 'default'
    assert payload['base'] == 'none'


def test_build_lora_install_payload_supports_explicit_filename_and_save_path():
    payload = ComfyUIService._build_lora_install_payload(
        url='https://example.com/models/abc.bin',
        filename='custom_name.safetensors',
        save_path='models/loras',
    )

    assert payload['filename'] == 'custom_name.safetensors'
    assert payload['save_path'] == 'models/loras'
    assert payload['base'] == 'none'


def test_normalize_download_url_for_match_ignores_case_and_query():
    normalized = ComfyUIService._normalize_download_url_for_match(
        'HTTPS://Example.com/models/a.safetensors?token=abc#frag'
    )
    assert normalized == 'https://example.com/models/a.safetensors'

