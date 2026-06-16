import asyncio
import sys
import types

from src.chat.config import chat_config


async def _dummy_generate_image_novelai():
    return None


async def _dummy_edit_image():
    return None


async def _dummy_generate_voice():
    return None


def test_get_tool_usage_guide_returns_topic_specific_runtime_rules():
    captured = {}

    def _fake_get_dynamic_tools_for_context(user_id=None):
        captured["user_id"] = user_id
        return asyncio.sleep(
            0, result=[_dummy_generate_image_novelai, _dummy_edit_image, _dummy_generate_voice]
        )

    fake_gemini_service_module = types.ModuleType("src.chat.services.gemini_service")
    fake_gemini_service_module.gemini_service = types.SimpleNamespace(
        tool_service=types.SimpleNamespace(
            get_dynamic_tools_for_context=_fake_get_dynamic_tools_for_context
        ),
        _load_novelai_preset_context=lambda user_id: asyncio.sleep(
            0,
            result={
                "user_preset_names": ["用户预设A"],
                "admin_preset_names": ["管理员预设B"],
            },
        ),
        _load_comfyui_choice_context=lambda user_id: asyncio.sleep(
            0,
            result={
                "available_model_names": ["modelA.safetensors"],
                "available_lora_names": ["loraA.safetensors"],
            },
        ),
    )
    sys.modules["src.chat.services.gemini_service"] = fake_gemini_service_module

    original_engine = chat_config.DEFAULT_IMAGE_ENGINE
    chat_config.DEFAULT_IMAGE_ENGINE = "comfyui"

    try:
        from src.chat.features.tools.functions.get_tool_usage_guide import (
            get_tool_usage_guide,
        )

        result = asyncio.run(get_tool_usage_guide(topic="image", user_id="123"))
    finally:
        chat_config.DEFAULT_IMAGE_ENGINE = original_engine
        sys.modules.pop("src.chat.services.gemini_service", None)

    assert result["topic"] == "image"
    assert result["image"]["default_image_engine"] == "comfyui"
    assert result["image"]["default_new_image_tool"] == "generate_image_comfyui"
    assert result["image"]["novelai_presets"]["user_presets"] == ["用户预设A"]
    assert result["image"]["comfyui_choices"]["model_names"] == ["modelA.safetensors"]
    assert captured["user_id"] == "123"
    returned_names = [item["tool_name"] for item in result["tool_overview"]]
    assert "_dummy_generate_image_novelai" in returned_names
    assert "_dummy_edit_image" in returned_names
    assert "_dummy_generate_voice" not in returned_names
    routing_rules = result["image"]["routing_rules"]
    assert any("先调用 get_user_profile" in rule for rule in routing_rules)
    assert any("名片里的外貌" in rule for rule in routing_rules)
    assert any("头像兜底" in rule for rule in routing_rules)
    assert any("edit_images_batch" in rule for rule in routing_rules)
    assert any("多名字混搜" in rule for rule in routing_rules)
