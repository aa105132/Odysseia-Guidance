import json
import sys
import types


fake_image_module = types.ModuleType("PIL.Image")
fake_image_module.Image = object
fake_image_module.Resampling = types.SimpleNamespace(LANCZOS=0)

fake_image_draw_module = types.ModuleType("PIL.ImageDraw")
fake_image_draw_module.Draw = lambda *args, **kwargs: types.SimpleNamespace(
    text=lambda *a, **k: None
)

fake_pil_package = types.ModuleType("PIL")
fake_pil_package.Image = fake_image_module
fake_pil_package.ImageDraw = fake_image_draw_module

sys.modules.setdefault("PIL", fake_pil_package)
sys.modules.setdefault("PIL.Image", fake_image_module)
sys.modules.setdefault("PIL.ImageDraw", fake_image_draw_module)

fake_image_utils = types.ModuleType("src.chat.utils.image_utils")
fake_image_utils.extract_image_frames_for_ai = lambda *args, **kwargs: []
sys.modules.setdefault("src.chat.utils.image_utils", fake_image_utils)

fake_context_service_module = types.ModuleType("src.chat.services.context_service")
fake_context_service_module.context_service = types.SimpleNamespace(
    clean_message_content=lambda content, guild=None: content
)
sys.modules.setdefault("src.chat.services.context_service", fake_context_service_module)

from src.chat.services.prompt_service import PromptService


def test_merge_user_profile_source_data_supports_flat_source_metadata():
    service = PromptService()

    user_profile_data = {
        "discord_id": "123",
        "title": "小明",
        "personal_summary": None,
        "source_metadata": {
            "name": "小明",
            "discord_id": "123",
            "personality": "温柔",
            "background": "来自月海",
            "preferences": "喜欢甜食",
        },
    }

    merged = service._merge_user_profile_source_data(user_profile_data)

    assert merged["title"] == "小明"
    assert merged["name"] == "小明"
    assert merged["personality"] == "温柔"
    assert merged["background"] == "来自月海"
    assert merged["preferences"] == "喜欢甜食"


def test_merge_user_profile_source_data_keeps_nested_and_top_level_priority():
    service = PromptService()

    user_profile_data = {
        "title": "顶层名字",
        "preferences": "顶层偏好",
        "source_metadata": {
            "name": "扁平名字",
            "background": "扁平背景",
            "content_json": json.dumps(
                {
                    "name": "旧名字",
                    "personality": "content_json 性格",
                    "preferences": "content_json 偏好",
                },
                ensure_ascii=False,
            ),
        },
    }

    merged = service._merge_user_profile_source_data(user_profile_data)

    assert merged["title"] == "顶层名字"
    assert merged["name"] == "扁平名字"
    assert merged["background"] == "扁平背景"
    assert merged["personality"] == "content_json 性格"
    assert merged["preferences"] == "顶层偏好"


def test_build_chat_prompt_contains_master_alias_hints():
    service = PromptService()
    original_get_prompt = service.get_prompt

    def fake_get_prompt(prompt_name, *args, **kwargs):
        if prompt_name == "SYSTEM_PROMPT":
            return "基础系统提示，不含主人别名。"
        return original_get_prompt(prompt_name, *args, **kwargs)

    def fake_get_model_specific_prompt(model_name, prompt_name):
        if prompt_name in {"JAILBREAK_USER_PROMPT", "JAILBREAK_MODEL_RESPONSE"}:
            return None
        if prompt_name == "JAILBREAK_FINAL_INSTRUCTION":
            return (
                "<system_info>\n"
                "基础最终注入\n"
                "社区: {guild_name}\n"
                "频道: {location_name}\n"
                "当前用户: {username} ({user_id})\n"
                "主人ID: {master_id}\n"
                "</system_info>"
            )
        return None

    service.get_prompt = fake_get_prompt
    service._get_model_specific_prompt = fake_get_model_specific_prompt

    conversation = service.build_chat_prompt(
        user_name="测试用户",
        message="你好",
        replied_message=None,
        images=[],
        channel_context=[],
        world_book_entries=[],
        affection_status=None,
        guild_name="测试社区",
        location_name="测试频道",
        user_id=123456789,
    )

    combined_text_parts = []
    for turn in conversation:
        for part in turn.get("parts", []):
            if isinstance(part, str):
                combined_text_parts.append(part)

    combined_text = "\n".join(combined_text_parts)

    assert "guayue20" in combined_text
    assert "瓜瓜喵" in combined_text
    assert "瓜月" in combined_text
