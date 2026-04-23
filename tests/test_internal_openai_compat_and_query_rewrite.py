import asyncio
import importlib.util
import os
import sys
import types
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock


ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def patched_modules(modules: dict[str, types.ModuleType], environ: dict[str, str] | None = None):
    original_modules: dict[str, types.ModuleType | None] = {}
    original_env: dict[str, str | None] = {}
    try:
        for name, module in modules.items():
            original_modules[name] = sys.modules.get(name)
            sys.modules[name] = module

        for key, value in (environ or {}).items():
            original_env[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

        yield
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

        for key, original in original_env.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original


@contextmanager
def load_module_with_stubs(
    relative_path: str,
    stub_modules: dict[str, types.ModuleType],
    environ: dict[str, str] | None = None,
):
    module_name = f"_test_{Path(relative_path).stem}_{uuid.uuid4().hex}"
    module_path = ROOT / relative_path

    with patched_modules(stub_modules, environ=environ):
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
            yield module
        finally:
            sys.modules.pop(module_name, None)


def build_gemini_service_stubs() -> tuple[dict[str, types.ModuleType], types.ModuleType]:
    google_module = types.ModuleType("google")
    google_genai_module = types.ModuleType("google.genai")
    google_genai_types_module = types.ModuleType("google.genai.types")
    google_genai_errors_module = types.ModuleType("google.genai.errors")

    class DummyClient:
        def __init__(self, *args, **kwargs):
            self.models = SimpleNamespace()

    class DummySafetySetting:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class DummyHttpOptions:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class DummyImage:
        pass

    class DummyPart:
        pass

    class DummyGenerateContentResponse:
        parts = []
        prompt_feedback = None

    class DummyClientError(Exception):
        pass

    class DummyServerError(Exception):
        pass

    google_genai_types_module.SafetySetting = DummySafetySetting
    google_genai_types_module.HttpOptions = DummyHttpOptions
    google_genai_types_module.Part = DummyPart
    google_genai_types_module.GenerateContentResponse = DummyGenerateContentResponse
    google_genai_types_module.Content = type("Content", (), {})
    google_genai_types_module.Blob = type("Blob", (), {})
    google_genai_types_module.Tool = type("Tool", (), {})
    google_genai_types_module.GoogleSearch = type("GoogleSearch", (), {})
    google_genai_types_module.UrlContext = type("UrlContext", (), {})
    google_genai_types_module.ThinkingConfig = type("ThinkingConfig", (), {})
    google_genai_types_module.GenerateContentConfig = type("GenerateContentConfig", (), {})
    google_genai_types_module.AutomaticFunctionCallingConfig = type(
        "AutomaticFunctionCallingConfig", (), {}
    )
    google_genai_types_module.EmbedContentConfig = type("EmbedContentConfig", (), {})
    google_genai_types_module.HarmCategory = SimpleNamespace(
        HARM_CATEGORY_HARASSMENT="harassment",
        HARM_CATEGORY_HATE_SPEECH="hate_speech",
        HARM_CATEGORY_SEXUALLY_EXPLICIT="sexually_explicit",
        HARM_CATEGORY_DANGEROUS_CONTENT="dangerous_content",
    )
    google_genai_types_module.HarmBlockThreshold = SimpleNamespace(BLOCK_NONE="BLOCK_NONE")

    google_genai_errors_module.ClientError = DummyClientError
    google_genai_errors_module.ServerError = DummyServerError

    google_genai_module.Client = DummyClient
    google_genai_module.types = google_genai_types_module
    google_genai_module.errors = google_genai_errors_module
    google_module.genai = google_genai_module

    pil_module = types.ModuleType("PIL")
    pil_image_module = types.ModuleType("PIL.Image")
    pil_image_module.Image = DummyImage
    pil_module.Image = pil_image_module

    regex_service_module = types.ModuleType("src.chat.services.regex_service")
    regex_service_module.regex_service = SimpleNamespace(
        clean_user_input=lambda text: str(text).strip()
    )

    chat_utils_database_module = types.ModuleType("src.chat.utils.database")
    chat_utils_database_module.chat_db_manager = SimpleNamespace()

    chat_config_module = types.ModuleType("src.chat.config.chat_config")
    chat_config_module.DEBUG_CONFIG = {}
    chat_config_module.MAX_CONCURRENT_REQUESTS = 1
    chat_config_module.GEMINI_MODEL = "gemini-2.5-flash"
    chat_config_module.API_RETRY_CONFIG = {"MAX_ATTEMPTS_PER_KEY": 1}
    chat_config_module.GEMINI_TEXT_GEN_CONFIG = {
        "temperature": 0.0,
        "max_output_tokens": 256,
    }
    chat_config_module.CUSTOM_GEMINI_ENDPOINTS = {}
    chat_config_module.MODEL_GENERATION_CONFIG = {
        "default": {"temperature": 1.0, "max_output_tokens": 1024}
    }
    chat_config_module._db_api_format = None
    chat_config_module._db_api_url = ""
    chat_config_module._db_api_key = ""

    chat_config_package = types.ModuleType("src.chat.config")
    chat_config_package.chat_config = chat_config_module

    prompt_utils_module = types.ModuleType("src.chat.utils.prompt_utils")
    prompt_utils_module.replace_emojis = lambda text: text

    response_dedup_module = types.ModuleType("src.chat.utils.response_dedup_utils")
    response_dedup_module.collapse_consecutive_duplicate_sentences = lambda text: text

    prompt_service_module = types.ModuleType("src.chat.services.prompt_service")
    prompt_service_module.prompt_service = SimpleNamespace(
        build_rag_summary_prompt=lambda latest_query, user_name, conversation_history: (
            f"{user_name}:{latest_query}"
        )
    )

    key_rotation_service_module = types.ModuleType("src.chat.services.key_rotation_service")

    class DummyKeyRotationService:
        def __init__(self, keys):
            self.keys = keys

        async def acquire_key(self):
            return SimpleNamespace(key=self.keys[0])

        async def release_key(self, key, success=True):
            return None

    class DummyNoAvailableKeyError(Exception):
        pass

    key_rotation_service_module.KeyRotationService = DummyKeyRotationService
    key_rotation_service_module.NoAvailableKeyError = DummyNoAvailableKeyError

    tool_service_module = types.ModuleType("src.chat.features.tools.services.tool_service")

    class DummyToolService:
        def __init__(self, *args, **kwargs):
            self.bot = kwargs.get("bot")

        def get_visible_tool_declarations(self):
            return []

    tool_service_module.ToolService = DummyToolService

    tool_loader_module = types.ModuleType("src.chat.features.tools.tool_loader")
    tool_loader_module.load_tools_from_directory = lambda path: ([], {})

    chat_settings_service_module = types.ModuleType(
        "src.chat.features.chat_settings.services.chat_settings_service"
    )
    chat_settings_service_module.chat_settings_service = SimpleNamespace()

    discord_image_utils_module = types.ModuleType(
        "src.chat.features.tools.utils.discord_image_utils"
    )

    async def fake_fetch_avatar_image(*args, **kwargs):
        return None

    discord_image_utils_module.fetch_avatar_image = fake_fetch_avatar_image

    image_utils_module = types.ModuleType("src.chat.utils.image_utils")
    image_utils_module.sanitize_image = lambda *args, **kwargs: None
    image_utils_module.extract_image_frames_for_ai = (
        lambda *args, **kwargs: ([], {"is_animated": False, "sampled_frames": 0, "total_frames": 0})
    )

    token_usage_service_module = types.ModuleType("src.database.services.token_usage_service")
    token_usage_service_module.token_usage_service = SimpleNamespace()

    database_module = types.ModuleType("src.database.database")
    database_module.AsyncSessionLocal = None

    return (
        {
            "google": google_module,
            "google.genai": google_genai_module,
            "google.genai.types": google_genai_types_module,
            "google.genai.errors": google_genai_errors_module,
            "PIL": pil_module,
            "PIL.Image": pil_image_module,
            "src.chat.services.regex_service": regex_service_module,
            "src.chat.utils.database": chat_utils_database_module,
            "src.chat.config": chat_config_package,
            "src.chat.config.chat_config": chat_config_module,
            "src.chat.utils.prompt_utils": prompt_utils_module,
            "src.chat.utils.response_dedup_utils": response_dedup_module,
            "src.chat.services.prompt_service": prompt_service_module,
            "src.chat.services.key_rotation_service": key_rotation_service_module,
            "src.chat.features.tools.services.tool_service": tool_service_module,
            "src.chat.features.tools.tool_loader": tool_loader_module,
            "src.chat.features.chat_settings.services.chat_settings_service": chat_settings_service_module,
            "src.chat.features.tools.utils.discord_image_utils": discord_image_utils_module,
            "src.chat.utils.image_utils": image_utils_module,
            "src.database.services.token_usage_service": token_usage_service_module,
            "src.database.database": database_module,
        },
        chat_config_module,
    )


def build_world_book_service_stubs(
    *,
    summarized_query: str | None,
    search_results: list[dict],
) -> tuple[dict[str, types.ModuleType], SimpleNamespace, SimpleNamespace]:
    gemini_service_module = types.ModuleType("src.chat.services.gemini_service")
    fake_gemini_service = SimpleNamespace(
        is_available=lambda: True,
        summarize_for_rag=AsyncMock(return_value=summarized_query),
    )
    gemini_service_module.GeminiService = object
    gemini_service_module.gemini_service = fake_gemini_service

    chat_config_module = types.ModuleType("src.chat.config.chat_config")
    chat_config_module.RAG_N_RESULTS_DEFAULT = 5

    chat_config_package = types.ModuleType("src.chat.config")
    chat_config_package.chat_config = chat_config_module

    incremental_rag_module = types.ModuleType(
        "src.chat.features.world_book.services.incremental_rag_service"
    )
    incremental_rag_module.incremental_rag_service = SimpleNamespace(
        _get_parade_connection=lambda: None
    )

    regex_service_module = types.ModuleType("src.chat.services.regex_service")
    regex_service_module.regex_service = SimpleNamespace(
        clean_user_input=lambda text: str(text).strip()
    )

    knowledge_search_service_module = types.ModuleType(
        "src.chat.features.world_book.services.knowledge_search_service"
    )
    fake_knowledge_search_service = SimpleNamespace(
        search=AsyncMock(return_value=search_results)
    )
    knowledge_search_service_module.knowledge_search_service = fake_knowledge_search_service

    return (
        {
            "src.chat.services.gemini_service": gemini_service_module,
            "src.chat.config": chat_config_package,
            "src.chat.config.chat_config": chat_config_module,
            "src.chat.features.world_book.services.incremental_rag_service": incremental_rag_module,
            "src.chat.features.world_book.services.knowledge_search_service": knowledge_search_service_module,
            "src.chat.services.regex_service": regex_service_module,
        },
        fake_gemini_service,
        fake_knowledge_search_service,
    )


def test_generate_simple_response_accepts_openai_compatible_alias():
    stub_modules, app_config = build_gemini_service_stubs()

    with load_module_with_stubs(
        "src/chat/services/gemini_service.py",
        stub_modules,
        environ={"GOOGLE_API_KEYS_LIST": "test-key"},
    ) as module:
        service = module.GeminiService()
        app_config._db_api_format = "openai_compatible"
        app_config._db_api_url = "https://bufan.live/v1"
        app_config._db_api_key = "test-key"
        app_config.CUSTOM_GEMINI_ENDPOINTS = {}

        service._generate_simple_with_openai_compatible = AsyncMock(return_value="openai-ok")
        service._generate_simple_with_gemini_custom = AsyncMock(return_value="gemini-custom")
        service._generate_simple_with_gemini_key_rotation = AsyncMock(return_value="gemini-rotation")

        result = asyncio.run(
            service.generate_simple_response(
                prompt="hello",
                generation_config={},
                model_name="deepseek-expert-chat",
                return_error_text=False,
            )
        )

        assert result == "openai-ok"
        service._generate_simple_with_openai_compatible.assert_awaited_once()
        service._generate_simple_with_gemini_custom.assert_not_called()
        service._generate_simple_with_gemini_key_rotation.assert_not_called()


def test_generate_simple_response_respects_explicit_openai_format_for_v1beta_proxy():
    stub_modules, _ = build_gemini_service_stubs()

    with load_module_with_stubs(
        "src/chat/services/gemini_service.py",
        stub_modules,
        environ={"GOOGLE_API_KEYS_LIST": "test-key"},
    ) as module:
        service = module.GeminiService()
        service._generate_simple_with_openai_compatible = AsyncMock(return_value="openai-ok")
        service._generate_simple_with_gemini_custom = AsyncMock(return_value="gemini-custom")
        service._generate_simple_with_gemini_key_rotation = AsyncMock(return_value="gemini-rotation")

        result = asyncio.run(
            service.generate_simple_response(
                prompt="hello",
                generation_config={},
                model_name="deepseek-expert-chat",
                api_format="openai",
                api_url="https://proxy.example.com/v1beta/openai",
                api_key="test-key",
                return_error_text=False,
            )
        )

        assert result == "openai-ok"
        service._generate_simple_with_openai_compatible.assert_awaited_once()
        service._generate_simple_with_gemini_custom.assert_not_called()
        service._generate_simple_with_gemini_key_rotation.assert_not_called()


def test_world_book_find_entries_uses_query_rewrite_before_search():
    stub_modules, fake_gemini_service, fake_knowledge_search_service = (
        build_world_book_service_stubs(
            summarized_query="Alice 银发 红瞳 外貌",
            search_results=[{"id": "alice-profile", "distance": 0.1}],
        )
    )

    with load_module_with_stubs(
        "src/chat/features/world_book/services/world_book_service.py",
        stub_modules,
    ) as module:
        service = module.WorldBookService(fake_gemini_service)
        history = [
            {"role": "user", "parts": ["上一轮对话"]},
            {"role": "model", "parts": ["我会按好感度和上下文综合回复"]},
        ]

        result = asyncio.run(
            service.find_entries(
                latest_query="  <@123456> 她长什么样  ",
                user_id=1,
                guild_id=2,
                user_name="月月",
                conversation_history=history,
            )
        )

        fake_gemini_service.summarize_for_rag.assert_awaited_once()
        summarize_call = fake_gemini_service.summarize_for_rag.await_args
        assert summarize_call.args[0] == "她长什么样"
        assert summarize_call.args[1] == "月月"
        assert summarize_call.args[2] == [{"role": "user", "parts": ["上一轮对话"]}]

        fake_knowledge_search_service.search.assert_awaited_once_with("Alice 银发 红瞳 外貌")
        assert result == [{"id": "alice-profile", "distance": 0.1}]


def test_world_book_find_entries_falls_back_to_clean_query_when_rewrite_is_empty():
    stub_modules, fake_gemini_service, fake_knowledge_search_service = (
        build_world_book_service_stubs(
            summarized_query="   ",
            search_results=[{"id": "fallback", "distance": 0.2}],
        )
    )

    with load_module_with_stubs(
        "src/chat/features/world_book/services/world_book_service.py",
        stub_modules,
    ) as module:
        service = module.WorldBookService(fake_gemini_service)

        result = asyncio.run(
            service.find_entries(
                latest_query=" <@42> 她长什么样 ",
                user_id=1,
                guild_id=2,
                user_name="月月",
            )
        )

        fake_gemini_service.summarize_for_rag.assert_awaited_once()
        fake_knowledge_search_service.search.assert_awaited_once_with("她长什么样")
        assert result == [{"id": "fallback", "distance": 0.2}]
