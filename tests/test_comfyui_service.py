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
        )

        prepared_workflow = service._prepare_workflow(params)

        assert prepared_workflow['1']['inputs']['text'] == '银狐少女'
        assert prepared_workflow['2']['inputs']['text'] == 'lowres'
        assert prepared_workflow['3']['inputs']['steps'] == 36
        assert prepared_workflow['4']['inputs']['cfg'] == 6.5
        assert prepared_workflow['5']['inputs']['width'] == 896
    finally:
        chat_config.COMFYUI_CONFIG['PLACEHOLDER_MAPPING'] = original_placeholder_mapping
        chat_config.COMFYUI_CONFIG['NODE_MAPPING'] = original_node_mapping
