from google.genai import types
import discord
import inspect
from typing import Optional, Dict, Callable, Any, List

import logging

from src.chat.config.chat_config import HIDDEN_TOOLS
from src.chat.features.tools.tool_availability import filter_tool_declarations
from src.chat.features.tools.services.user_tool_settings_service import (
    user_tool_settings_service,
)

log = logging.getLogger(__name__)


class ToolService:
    """
    一个负责管理和执行 Gemini 模型工具的服务。

    它包含两个核心功能:
    1. 动态地为每个聊天上下文提供正确的工具列表。
    2. 执行模型请求的工具函数调用。
    """

    def __init__(
        self,
        bot: Optional[discord.Client],
        tool_map: Dict[str, Callable],
        tool_declarations: List[Callable],
    ):
        """
        初始化 ToolService。

        Args:
            bot: Discord 客户端实例，将注入到需要它的工具中。
            tool_map: 一个字典，将工具名称映射到其对应的异步函数实现。
            tool_declarations: 从工具加载器获得的原始工具函数声明列表。
        """
        self.bot = bot
        self.tool_map = tool_map
        self.tool_declarations = tool_declarations
        self.cached_search_reference_images: List[Dict[str, Any]] = []
        log.info(
            f"ToolService 已使用 {len(tool_map)} 个工具进行初始化: {list(tool_map.keys())}"
        )

    def get_visible_tool_declarations(self) -> List[Callable]:
        """返回当前应暴露给模型的工具声明列表。"""
        visible_tools = filter_tool_declarations(self.tool_declarations)
        if len(visible_tools) != len(self.tool_declarations):
            log.info(
                "根据活动状态过滤工具后，剩余 %s 个工具。",
                len(visible_tools),
            )
        return visible_tools

    async def get_dynamic_tools_for_context(
        self, user_id_for_settings: Optional[str] = None
    ) -> List[Callable]:
        """
        根据提供的用户ID动态获取可用的工具列表。

        - 无论用户是否禁用工具，都返回所有工具声明。
        - 工具执行时会检查是否被用户禁用，如果被禁用则返回错误提示。

        Args:
            user_id_for_settings: 用于查询工具设置的用户的ID。如果为 None，则返回默认工具。

        Returns:
            所有工具函数列表（包括被用户禁用的工具）。
        """
        visible_tools = self.get_visible_tool_declarations()

        if not user_id_for_settings:
            log.info("未提供 user_id_for_settings，使用默认工具集。")
            return visible_tools

        log.info(
            f"为用户 {user_id_for_settings} 返回可见工具声明（共 {len(visible_tools)} 个）"
        )
        return visible_tools

    async def execute_tool_call(
        self,
        tool_call: types.FunctionCall,
        channel: Optional[discord.TextChannel] = None,
        user_id: Optional[int] = None,
        log_detailed: bool = False,
        message: Optional[discord.Message] = None,
        user_id_for_settings: Optional[str] = None,
        current_turn_tool_names: Optional[List[str]] = None,
        user_name: Optional[str] = None,
        fallback_query: Optional[str] = None,
        channel_context: Optional[List[Dict[str, Any]]] = None,
    ) -> types.Part:
        """
        执行单个工具调用，并以可发送回 Gemini 模型的格式返回结果。
        这个版本通过依赖注入来提供上下文（如 bot 实例、channel），并处理备用参数（如 user_id）。

        Args:
            tool_call: 来自 Gemini API 响应的函数调用对象。
            channel: 可选的当前消息所在的 Discord 频道对象。
            user_id: 可选的当前消息作者的 Discord ID，用作某些参数的备用值。
            log_detailed: 是否记录详细日志。
            user_id_for_settings: 用于检查工具设置的用户ID（通常是帖子所有者的ID）。
            current_turn_tool_names: 当前这轮模型计划执行的工具名列表。
            user_name: 当前消息作者的显示名，供上下文工具使用。
            fallback_query: 当前消息合并回复内容后的备用检索词。
            channel_context: 已格式化的频道历史，供知识库查询重写使用。

        Returns:
            一个格式化为 FunctionResponse 的 Part 对象，其中包含工具的输出。
        """
        tool_name = tool_call.name
        if log_detailed:
            log.info(f"--- [工具执行流程]: 准备执行 '{tool_name}' ---")

        if not tool_name:
            log.error("接收到没有名称的工具调用。")
            return types.Part.from_function_response(
                name="unknown_tool",
                response={"error": "Tool call with no name received."},
            )

        tool_function = self.tool_map.get(tool_name)

        if not tool_function:
            log.error(f"找不到工具 '{tool_name}' 的实现。")
            return types.Part.from_function_response(
                name=tool_name, response={"error": f"Tool '{tool_name}' not found."}
            )

        # --- 检查工具是否被禁用 ---
        if user_id_for_settings:
            try:
                # HIDDEN_TOOLS 中的工具是系统必须保留的，不应该让用户控制
                if tool_name not in HIDDEN_TOOLS:
                    user_settings = (
                        await user_tool_settings_service.get_user_tool_settings(
                            user_id_for_settings
                        )
                    )
                    if user_settings and isinstance(user_settings, dict):
                        enabled_tools = user_settings.get("enabled_tools", [])
                        # 如果 enabled_tools 不为空且当前工具不在列表中，则禁用
                        if enabled_tools and tool_name not in enabled_tools:
                            log.info(
                                f"工具 '{tool_name}' 被 {user_id_for_settings} 禁用，拒绝执行。"
                            )
                            # 返回错误信息，让AI解释给用户
                            return types.Part.from_function_response(
                                name=tool_name,
                                response={
                                    "error": f"工具 '{tool_name}' 被帖子所有者禁用了。ta不让我干这个活啦!"
                                },
                            )
            except Exception as e:
                log.error(f"检查工具设置时出错: {e}", exc_info=True)
        # --- 结束检查 ---

        try:
            # 步骤 1: 从模型响应中提取参数
            tool_args: Dict[str, Any] = (
                {key: value for key, value in tool_call.args.items()}
                if tool_call.args
                else {}
            )
            if log_detailed:
                log.info(f"模型提供的参数: {tool_args}")

            # 步骤 2 & 3: 智能注入依赖和上下文
            # 我们不再检查函数签名，而是将所有可用的上下文信息直接注入
            # 到 tool_args 中。工具函数可以通过 **kwargs 来按需取用。
            sig = inspect.signature(tool_function)
            # 无条件注入 bot 实例，让工具函数可以通过 **kwargs 按需获取
            tool_args["bot"] = self.bot
            if log_detailed:
                log.info("已注入 'bot' 实例。")

            if user_id is not None:
                # 优先注入通用的 user_id
                # 统一将 user_id 转为字符串类型再注入，以适配工具函数的类型期望
                user_id_str = str(user_id)
                # 核心修复：只有当模型没有提供 user_id 时，才注入当前用户的 id 作为默认值。
                if "user_id" not in tool_args:
                    tool_args["user_id"] = user_id_str
                    if log_detailed:
                        log.info(
                            f"模型未提供 'user_id'，已注入当前用户 ID: {user_id_str}"
                        )

                # 为需要 author_id 的旧工具提供兼容性
                if "author_id" in sig.parameters and "author_id" not in tool_args:
                    tool_args["author_id"] = user_id_str
                    if log_detailed:
                        log.info(
                            f"为兼容性，已填充 'author_id': {tool_args['author_id']}"
                        )

            if channel:
                tool_args["channel"] = channel
                if log_detailed:
                    log.info(f"已注入 'channel' (ID: {channel.id}) 到 **kwargs。")
                if channel.guild:
                    # 同时注入 guild 对象本身和 guild_id，以提供最大的灵活性
                    tool_args["guild"] = channel.guild
                    tool_args["guild_id"] = str(channel.guild.id)
                    if log_detailed:
                        log.info(f"已注入 'guild' (ID: {channel.guild.id}) 实例。")
                if isinstance(channel, discord.Thread):
                    tool_args["thread_id"] = channel.id
                    if log_detailed:
                        log.info(f"检测到帖子上下文，已注入 'thread_id': {channel.id}")

            if user_name is not None:
                tool_args["user_name"] = user_name
                if log_detailed:
                    log.info(f"已注入 'user_name': {user_name}")

            if fallback_query is not None:
                tool_args["fallback_query"] = fallback_query
                if log_detailed:
                    log.info("已注入 'fallback_query'。")

            if channel_context is not None:
                tool_args["channel_context"] = channel_context
                if log_detailed:
                    log.info(
                        f"已注入 'channel_context'，长度: {len(channel_context)}"
                    )

            # 注入 message 对象（用于添加反应等）
            if message:
                tool_args["message"] = message
                if log_detailed:
                    log.info(f"已注入 'message' (ID: {message.id}) 到 **kwargs。")

            supports_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in sig.parameters.values()
            )
            if current_turn_tool_names is not None and (
                "current_turn_tool_names" in sig.parameters or supports_kwargs
            ):
                tool_args["current_turn_tool_names"] = [
                    str(name).strip()
                    for name in current_turn_tool_names
                    if str(name).strip()
                ]
                if log_detailed:
                    log.info(
                        "已注入 'current_turn_tool_names': "
                        f"{tool_args['current_turn_tool_names']}"
                    )

            # 步骤 4: 智能地传递 log_detailed 参数
            if "log_detailed" in sig.parameters:
                tool_args["log_detailed"] = log_detailed

            # --- 安全加固：确保 'get_yearly_summary' 只能对当前用户执行 ---
            if tool_name == "get_yearly_summary" and user_id is not None:
                user_id_str = str(user_id)
                if tool_args.get("user_id") != user_id_str:
                    log.warning(
                        f"检测到模型为 get_yearly_summary 提供了不同的 user_id ({tool_args.get('user_id')})。"
                        f"已强制覆盖为当前用户 ID ({user_id_str})。"
                    )
                tool_args["user_id"] = user_id_str

            # --- 安全加固：确保 'issue_user_warning' 只能对当前用户执行 ---
            if tool_name == "issue_user_warning" and user_id is not None:
                user_id_str = str(user_id)
                if tool_args.get("user_id") != user_id_str:
                    log.warning(
                        f"检测到模型尝试为其他用户 ({tool_args.get('user_id')}) 调用警告工具。"
                        f"已强制重定向到当前用户 ({user_id_str})。"
                    )
                tool_args["user_id"] = user_id_str

            # --- 安全加固：图片生成工具始终绑定当前用户（用于读取用户持久化参数）---
            image_tools_bind_user = {'generate_image_novelai', 'generate_image', 'generate_image_comfyui', 'edit_image'}
            if tool_name in image_tools_bind_user and user_id is not None:
                user_id_str = str(user_id)
                if tool_args.get("user_id") != user_id_str:
                    log.info(
                        f"图片工具 {tool_name} 强制使用当前用户 user_id={user_id_str} "
                        f"(原始值: {tool_args.get('user_id')})"
                    )
                tool_args["user_id"] = user_id_str

            # 搜索图只在模型显式选择编号时才作为图生图/图生视频参考图；
            # 不能由代码层自动硬塞搜索结果。
            if (
                tool_name in {"edit_image", "generate_video"}
                and self.cached_search_reference_images
                and not tool_args.get("_prepared_reference_images")
                and not tool_args.get("_prepared_reference_image")
            ):
                raw_indexes = tool_args.get("image_search_reference_indexes")
                if raw_indexes in (None, ""):
                    raw_indexes = tool_args.get("image_search_reference_index")

                requested_indexes: List[int] = []
                raw_values = raw_indexes if isinstance(raw_indexes, list) else [raw_indexes]
                for raw_value in raw_values:
                    if raw_value in (None, ""):
                        continue
                    for part in str(raw_value).replace("，", ",").split(","):
                        try:
                            index = int(part.strip())
                        except (TypeError, ValueError):
                            continue
                        if index > 0 and index not in requested_indexes:
                            requested_indexes.append(index)

                if requested_indexes:
                    selected_refs = []
                    for index in requested_indexes:
                        if 1 <= index <= len(self.cached_search_reference_images):
                            selected_refs.append(self.cached_search_reference_images[index - 1])
                    if selected_refs:
                        tool_args["_prepared_reference_images"] = selected_refs[:8]
                        if tool_name == "generate_video":
                            tool_args["use_reference_image"] = True
                        prompt_key = "edit_prompt" if tool_name == "edit_image" else "prompt"
                        prompt_text = str(tool_args.get(prompt_key) or "").strip()
                        watermark_constraint = (
                            "参考图仅用于理解角色/主体外观、服装、发型、配色、构图与画风；"
                            "不要复制参考图里的水印、署名、平台文字、截图 UI、边框或无关文字，输出中不要出现任何水印。"
                        )
                        if watermark_constraint not in prompt_text:
                            tool_args[prompt_key] = f"{prompt_text}\n{watermark_constraint}" if prompt_text else watermark_constraint

            # 步骤 5: 执行工具函数
            result = await tool_function(**tool_args)
            if log_detailed:
                log.info(f"工具 '{tool_name}' 执行完毕。")

            # 步骤 5: 根据工具返回的结果，构造相应的 Part
            image_infos: List[Dict[str, Any]] = []
            if isinstance(result, dict):
                raw_image_list = result.get("image_data_list")
                if isinstance(raw_image_list, list):
                    image_infos.extend(
                        item for item in raw_image_list
                        if isinstance(item, dict) and item.get("data")
                    )
                elif isinstance(result.get("image_data"), dict):
                    image_infos.append(result["image_data"])

            if image_infos:
                # 多模态结果：返回多张图片 Part + 文本 function_response Part。
                image_parts = [
                    types.Part(
                        inline_data=types.Blob(
                            mime_type=image_info.get("mime_type", "image/png"),
                            data=image_info.get("data", b""),
                        )
                    )
                    for image_info in image_infos
                ]

                def _strip_binary(value: Any) -> Any:
                    if isinstance(value, (bytes, bytearray, memoryview)):
                        return "<binary-image-data>"
                    if isinstance(value, dict):
                        return {k: _strip_binary(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [_strip_binary(item) for item in value]
                    return value

                text_metadata = {
                    k: _strip_binary(v) for k, v in result.items()
                    if k not in {"image_data", "image_data_list"}
                    and not isinstance(v, (bytes, bytearray, memoryview))
                }
                if text_metadata:
                    text_part = types.Part.from_function_response(
                        name=tool_name,
                        response={"result": text_metadata},
                    )
                    if log_detailed:
                        log.info(f"已为 '{tool_name}' 构造 {len(image_parts)} 张图片+元数据 Part。")
                    return [*image_parts, text_part]
                if log_detailed:
                    log.info(f"已为 '{tool_name}' 构造 {len(image_parts)} 张图片 Part。")
                return image_parts
            else:
                # 这是一个标准的文本/JSON结果（包括错误信息）
                part = types.Part.from_function_response(
                    name=tool_name,
                    response={"result": result or "操作成功完成，但没有返回文本内容。"},
                )
                if log_detailed:
                    log.info(f"已为 '{tool_name}' 构造标准的 FunctionResponse Part。")
                return part

        except Exception as e:
            log.error(f"执行工具 '{tool_name}' 时发生意外错误。", exc_info=True)
            return types.Part.from_function_response(
                name=tool_name,
                response={
                    "error": f"An unexpected error occurred during execution: {str(e)}"
                },
            )
