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


def test_save_workflow_text_supports_wrapped_z_json_string(tmp_path):
    inner_workflow = {'1': {'inputs': {'text': 'wrapped'}, 'class_type': 'CLIPTextEncode'}}
    wrapped_workflow_text = json.dumps({'z': json.dumps(inner_workflow, ensure_ascii=False)}, ensure_ascii=False)
    target_path = tmp_path / 'workflow_wrapped.json'

    saved_path = ComfyUIService.save_workflow_text(wrapped_workflow_text, str(target_path))

    assert saved_path == str(target_path)
    loaded = json.loads(target_path.read_text(encoding='utf-8'))
    assert loaded['1']['class_type'] == 'CLIPTextEncode'
    assert loaded['1']['inputs']['text'] == 'wrapped'


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


def test_infer_node_mapping_from_workflow_payload_supports_placeholder_tokens():
    workflow_template = {
        '1': {'class_type': 'UNETLoader', 'inputs': {'unet_name': '%MODEL_NAME%'}},
        '2': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%prompt%'}},
        '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': '%negative_prompt%'}},
        '4': {'class_type': 'KSampler', 'inputs': {
            'steps': '%steps%',
            'cfg': '%cfg_scale%',
            'sampler_name': '%sampler_name%',
            'scheduler': '%scheduler%',
            'seed': '%seed%',
        }},
        '5': {'class_type': 'EmptyLatentImage', 'inputs': {'width': '%width%', 'height': '%height%'}},
        '6': {'class_type': 'VAELoader', 'inputs': {'vae_name': '%vae%'}},
        '7': {'class_type': 'CLIPLoader', 'inputs': {'clip_name': '%clip_name%'}},
    }

    mapping = ComfyUIService.infer_node_mapping_from_workflow_payload(workflow_template)

    assert mapping['model_name'] == ['1', 'unet_name']
    assert mapping['positive_prompt'] == ['2', 'text']
    assert mapping['negative_prompt'] == ['3', 'text']
    assert mapping['steps'] == ['4', 'steps']
    assert mapping['cfg'] == ['4', 'cfg']
    assert mapping['sampler'] == ['4', 'sampler_name']
    assert mapping['scheduler'] == ['4', 'scheduler']
    assert mapping['seed'] == ['4', 'seed']
    assert mapping['width'] == ['5', 'width']
    assert mapping['height'] == ['5', 'height']
    assert mapping['vae_name'] == ['6', 'vae_name']
    assert mapping['clip_name'] == ['7', 'clip_name']


def test_infer_node_mapping_from_workflow_payload_supports_field_name_fallback():
    workflow_template = {
        '10': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'masterpiece, best quality'}},
        '11': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'worst quality, lowres'}},
        '12': {'class_type': 'KSampler', 'inputs': {
            'steps': 30,
            'cfg': 5.5,
            'sampler_name': 'euler',
            'scheduler': 'normal',
            'seed': 12345,
        }},
    }

    mapping = ComfyUIService.infer_node_mapping_from_workflow_payload(workflow_template)

    assert mapping['positive_prompt'] == ['10', 'text']
    assert mapping['negative_prompt'] == ['11', 'text']
    assert mapping['steps'] == ['12', 'steps']
    assert mapping['cfg'] == ['12', 'cfg']
    assert mapping['sampler'] == ['12', 'sampler_name']
    assert mapping['scheduler'] == ['12', 'scheduler']
    assert mapping['seed'] == ['12', 'seed']



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


def test_build_runtime_params_prefers_style_specific_default_model(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(
        json.dumps({'1': {'inputs': {'text': '{{positive_prompt}}'}}}, ensure_ascii=False),
        encoding='utf-8',
    )

    original_default_model_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_MODEL_NAME')
    original_default_realistic_model_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_REALISTIC_MODEL_NAME')
    original_default_anime_model_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_ANIME_MODEL_NAME')

    try:
        chat_config.COMFYUI_CONFIG['DEFAULT_MODEL_NAME'] = 'global_default.safetensors'
        chat_config.COMFYUI_CONFIG['DEFAULT_REALISTIC_MODEL_NAME'] = 'zimage_realistic.safetensors'
        chat_config.COMFYUI_CONFIG['DEFAULT_ANIME_MODEL_NAME'] = 'anime_default.safetensors'

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )

        realistic_params = service._build_runtime_params(prompt='真人写实摄影风格的肖像')
        anime_params = service._build_runtime_params(prompt='二次元 anime 少女插画')
        neutral_params = service._build_runtime_params(prompt='清晨森林风景')

        assert realistic_params['model_name'] == 'zimage_realistic.safetensors'
        assert anime_params['model_name'] == 'anime_default.safetensors'
        assert neutral_params['model_name'] == 'global_default.safetensors'
    finally:
        chat_config.COMFYUI_CONFIG['DEFAULT_MODEL_NAME'] = original_default_model_name
        chat_config.COMFYUI_CONFIG['DEFAULT_REALISTIC_MODEL_NAME'] = original_default_realistic_model_name
        chat_config.COMFYUI_CONFIG['DEFAULT_ANIME_MODEL_NAME'] = original_default_anime_model_name


def test_resolve_default_workflow_path_supports_style_split():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')

    original_default_realistic_workflow_path = chat_config.COMFYUI_CONFIG.get('DEFAULT_REALISTIC_WORKFLOW_PATH')
    original_default_anime_workflow_path = chat_config.COMFYUI_CONFIG.get('DEFAULT_ANIME_WORKFLOW_PATH')

    try:
        chat_config.COMFYUI_CONFIG['DEFAULT_REALISTIC_WORKFLOW_PATH'] = r'D:\workflows\realistic.json'
        chat_config.COMFYUI_CONFIG['DEFAULT_ANIME_WORKFLOW_PATH'] = r'D:\workflows\anime.json'

        assert service.resolve_default_workflow_path(prompt='写实真人电影感人像') == r'D:\workflows\realistic.json'
        assert service.resolve_default_workflow_path(prompt='二次元 anime 角色立绘') == r'D:\workflows\anime.json'
        assert service.resolve_default_workflow_path(prompt='普通风景速写') == ''
    finally:
        chat_config.COMFYUI_CONFIG['DEFAULT_REALISTIC_WORKFLOW_PATH'] = original_default_realistic_workflow_path
        chat_config.COMFYUI_CONFIG['DEFAULT_ANIME_WORKFLOW_PATH'] = original_default_anime_workflow_path


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

    assert payload['type'] == 'lora'
    assert payload['url'] == 'https://example.com/models/my_lora.safetensors'
    assert payload['filename'] == 'my_lora.safetensors'
    assert payload['save_path'] == 'default'
    assert payload['base'] == 'lora'
    assert payload['name'] == 'my lora'
    assert payload['ui_id'] == 'odysseia-bot'


def test_build_lora_install_payload_supports_explicit_filename_and_save_path():
    payload = ComfyUIService._build_lora_install_payload(
        url='https://example.com/models/abc.bin',
        filename='custom_name.safetensors',
        save_path='models/loras',
    )

    assert payload['filename'] == 'custom_name.safetensors'
    assert payload['save_path'] == 'models/loras'
    assert payload['base'] == 'lora'


def test_build_lora_install_payload_normalizes_legacy_type_and_base_values():
    payload = ComfyUIService._build_lora_install_payload(
        url='https://example.com/models/abc.safetensors',
        filename='abc.safetensors',
        model_type='loras',
        base='none',
    )

    assert payload['type'] == 'lora'
    assert payload['base'] == 'lora'


def test_normalize_download_url_for_match_ignores_case_and_query():
    normalized = ComfyUIService._normalize_download_url_for_match(
        'HTTPS://Example.com/models/a.safetensors?token=abc#frag'
    )
    assert normalized == 'https://example.com/models/a.safetensors'


def test_extract_filename_from_download_url_prefers_content_disposition():
    url = (
        'https://example.com/model/123/random_name.bin?'
        'response-content-disposition=attachment%3B%20filename%3D"final_name.safetensors"'
    )
    filename = ComfyUIService._extract_filename_from_download_url(url)
    assert filename == 'final_name.safetensors'


def test_build_runtime_params_supports_default_vae_and_clip(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps({'1': {'inputs': {'text': '{{positive_prompt}}'}}}, ensure_ascii=False), encoding='utf-8')

    original_default_vae_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_VAE_NAME')
    original_default_clip_name = chat_config.COMFYUI_CONFIG.get('DEFAULT_CLIP_NAME')

    try:
        chat_config.COMFYUI_CONFIG['DEFAULT_VAE_NAME'] = 'ae_default.safetensors'
        chat_config.COMFYUI_CONFIG['DEFAULT_CLIP_NAME'] = 'clip_default.safetensors'

        service = ComfyUIService(
            server_address='127.0.0.1:8188',
            workflow_path=str(workflow_path),
        )

        params = service._build_runtime_params(prompt='city skyline')

        assert params['vae_name'] == 'ae_default.safetensors'
        assert params['clip_name'] == 'clip_default.safetensors'
    finally:
        chat_config.COMFYUI_CONFIG['DEFAULT_VAE_NAME'] = original_default_vae_name
        chat_config.COMFYUI_CONFIG['DEFAULT_CLIP_NAME'] = original_default_clip_name


def test_sanitize_lora_filename_forces_safetensors_suffix():
    assert ComfyUIService._sanitize_lora_filename('abc.bin') == 'abc.safetensors'
    assert ComfyUIService._sanitize_lora_filename('x y z') == 'x_y_z.safetensors'


def test_resolve_lora_download_limit_bytes_reads_config():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')
    original_limit = chat_config.COMFYUI_CONFIG.get('LORA_DOWNLOAD_MAX_MB')

    try:
        chat_config.COMFYUI_CONFIG['LORA_DOWNLOAD_MAX_MB'] = 12
        assert service._resolve_lora_download_limit_bytes() == 12 * 1024 * 1024
    finally:
        chat_config.COMFYUI_CONFIG['LORA_DOWNLOAD_MAX_MB'] = original_limit


def test_normalize_safetensors_names_only_keeps_safetensors_and_dedupes():
    names = ComfyUIService._normalize_safetensors_names(
        [
            'A.safetensors',
            'b.ckpt',
            'a.safetensors',
            'nested/model_x.safetensors',
            '',
            None,
            'C.SAFETENSORS',
        ]
    )

    assert names == [
        'A.safetensors',
        'C.SAFETENSORS',
        'nested/model_x.safetensors',
    ]


def test_pick_best_name_candidate_supports_preferred_and_avoid_keywords():
    candidates = [
        'Wan2\\wan_2.1_vae.safetensors',
        'qwen\\qwen_image_vae.safetensors',
        'sdxlVAE_sdxlVAE.safetensors',
    ]

    selected = ComfyUIService._pick_best_name_candidate(
        candidates,
        hints=['zimage_v1.safetensors'],
        preferred_keywords=['qwen'],
        avoid_keywords=['wan'],
    )

    assert selected == 'qwen\\qwen_image_vae.safetensors'


def test_build_runtime_params_converts_negative_seed_to_random_value(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps({'1': {'inputs': {'seed': '%seed%'}}}, ensure_ascii=False), encoding='utf-8')

    service = ComfyUIService(
        server_address='127.0.0.1:8188',
        workflow_path=str(workflow_path),
    )

    params = service._build_runtime_params(prompt='test prompt', seed=-1)

    assert isinstance(params['seed'], int)
    assert 0 <= params['seed'] <= 4294967295


def test_fill_missing_runtime_names_fallbacks_when_model_name_not_available(tmp_path):
    workflow_template = {
        '1': {'inputs': {'unet_name': '%MODEL_NAME%'}},
    }
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps(workflow_template, ensure_ascii=False), encoding='utf-8')

    service = ComfyUIService(
        server_address='127.0.0.1:8188',
        workflow_path=str(workflow_path),
    )

    async def _fake_models() -> list[str]:
        return ['Rebalance_v1.safetensors', 'z_image_turbo_bf16.safetensors']

    service.get_available_model_names = _fake_models  # type: ignore[method-assign]

    params = {
        'model_name': 'oneObsession_v19.safetensors',
        'clip_name': '',
        'vae_name': '',
    }

    updated = asyncio.run(service._fill_missing_runtime_names(params, workflow_template=workflow_template))

    assert updated['model_name'] in {'Rebalance_v1.safetensors', 'z_image_turbo_bf16.safetensors'}


def test_build_runtime_params_normalizes_sampler_alias_and_trims_spaces(tmp_path):
    workflow_path = tmp_path / 'workflow_template.json'
    workflow_path.write_text(json.dumps({'1': {'inputs': {'sampler_name': '%sampler_name%'}}}, ensure_ascii=False), encoding='utf-8')

    service = ComfyUIService(
        server_address='127.0.0.1:8188',
        workflow_path=str(workflow_path),
    )

    params = service._build_runtime_params(prompt='test prompt', sampler=' euler_a ')

    assert params['sampler'] == 'euler_ancestral'


def test_replace_placeholders_in_string_trims_token_whitespace():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')

    result = service._replace_placeholders_in_string(
        ' %sampler_name% ',
        {'%sampler_name%': 'euler_ancestral'},
        {},
    )

    assert result == 'euler_ancestral'


def test_extract_media_meta_from_output_node_supports_videos():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')
    output_node = {
        'videos': [
            {
                'filename': 'demo.mp4',
                'subfolder': '',
                'type': 'output',
                'format': 'video/h264-mp4',
            }
        ]
    }

    media_meta = service._extract_media_meta_from_output_node(output_node)

    assert media_meta is not None
    assert media_meta['filename'] == 'demo.mp4'
    assert media_meta['media_kind'] == 'video'
    assert media_meta['mime_type'] == 'video/mp4'


def test_extract_media_meta_from_output_node_supports_images():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')
    output_node = {
        'images': [
            {
                'filename': 'demo.png',
                'subfolder': 'ComfyUI',
                'type': 'output',
            }
        ]
    }

    media_meta = service._extract_media_meta_from_output_node(output_node)

    assert media_meta is not None
    assert media_meta['filename'] == 'demo.png'
    assert media_meta['subfolder'] == 'ComfyUI'
    assert media_meta['media_kind'] == 'image'
    assert media_meta['mime_type'] == 'image/png'


def test_generate_image_ignores_non_image_media():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')

    async def _fake_generate_media(*args, **kwargs):
        return {
            'bytes': b'video-data',
            'filename': 'demo.mp4',
            'mime_type': 'video/mp4',
            'media_kind': 'video',
        }

    service.generate_media = _fake_generate_media  # type: ignore[method-assign]

    result = asyncio.run(service.generate_image(prompt='test'))

    assert result is None


def test_build_placeholder_token_values_supports_reference_image_aliases():
    service = ComfyUIService(server_address='127.0.0.1:8188', workflow_path='')
    token_values = service._build_placeholder_token_values({'input_image': 'input/ref_1.png'})

    assert token_values['%input_image%'] == 'input/ref_1.png'
    assert token_values['%reference_image%'] == 'input/ref_1.png'
    assert token_values['%init_image%'] == 'input/ref_1.png'

