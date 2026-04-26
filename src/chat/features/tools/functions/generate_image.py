# -*- coding: utf-8 -*-

"""
图片生成工具
让LLM可以在对话中自动调用Gemini Imagen生成图片
"""

import logging
import re
import discord
from typing import Optional, List, Tuple

from src.chat.features.image_generation.utils.spoiler_policy import (
    should_spoiler_image,
)
from src.chat.features.tools.functions.image_policy_guard import (
    check_yueyue_self_nsfw_violation,
)
from src.chat.utils.prompt_utils import replace_emojis

log = logging.getLogger(__name__)

# 图片生成相关的emoji
GENERATING_EMOJI = "🎨"  # 正在生成
SUCCESS_EMOJI = "✅"      # 生成成功
FAILED_EMOJI = "❌"       # 生成失败

_ASCII_TAG_SEGMENT_RE = re.compile(r"[a-zA-Z0-9_:\-\.#()/'+\s]+")
_ENGLISH_LETTER_RE = re.compile(r"[A-Za-z]")
_CHINESE_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")


def _looks_like_ascii_tag_prompt(text: str) -> bool:
    """粗略判断是否像英文标签/Tag 串。"""
    normalized = str(text or "").strip()
    if not normalized:
        return False

    tokens = [segment.strip() for segment in re.split(r"[,，]+", normalized) if segment.strip()]
    if len(tokens) < 4:
        return False

    ascii_like = sum(1 for token in tokens if _ASCII_TAG_SEGMENT_RE.fullmatch(token))
    return ascii_like / max(1, len(tokens)) >= 0.7


def _needs_chinese_rewrite(text: Optional[str]) -> bool:
    """判断 Imagen 请求是否需要先改写成中文自然语言。"""
    normalized = str(text or "").strip()
    if not normalized:
        return False

    chinese_chars = len(_CHINESE_CHAR_RE.findall(normalized))
    english_letters = len(_ENGLISH_LETTER_RE.findall(normalized))

    if english_letters == 0:
        return False
    if chinese_chars == 0:
        return True
    if _looks_like_ascii_tag_prompt(normalized):
        return True

    return english_letters >= max(int(chinese_chars * 1.2), 10)


async def _rewrite_imagen_text_to_chinese(
    text: Optional[str],
    *,
    field_name: str,
) -> Optional[str]:
    """把英文/标签化 Imagen 提示词改写为中文自然语言。"""
    normalized = str(text or "").strip()
    if not normalized or not _needs_chinese_rewrite(normalized):
        return normalized or None

    from src.chat.services.gemini_service import gemini_service

    if field_name == "negative_prompt":
        instruction = (
            "你是图像负面提示词转换助手。请把下面可能是英文、英文标签或中英混写的负面提示词，"
            "改写成适合 Imagen 的简体中文负面约束短句。"
            "要求：\n"
            "1) 只输出最终中文负面提示词，不要解释。\n"
            "2) 保留“不希望出现”的所有约束，不要遗漏。\n"
            "3) 不要输出英文单词、英文标签、Danbooru tag、项目符号。\n\n"
            f"原负面提示词：{normalized}"
        )
    else:
        instruction = (
            "你是图像提示词转换助手。请把下面可能是英文自然语言、英文标签或中英混写的图片提示词，"
            "改写成适合 Gemini Imagen 的简体中文自然语言单段描述。"
            "要求：\n"
            "1) 只输出最终中文提示词，不要解释。\n"
            "2) 保留主体、身份、服饰、场景、动作、构图、光影、氛围等核心要求，不得篡改。\n"
            "3) 不要输出英文单词、英文标签、Danbooru tag、项目符号。\n"
            "4) 如果原文包含擦边或成人语义，请用中文镜头语言、氛围和动作暗示表达，避免直白器官词。\n\n"
            f"原提示词：{normalized}"
        )

    try:
        rewritten = await gemini_service.generate_simple_response(
            prompt="",
            generation_config={
                "temperature": 0.2,
                "max_output_tokens": 800,
            },
            messages=[{"role": "user", "content": instruction}],
            return_error_text=False,
        )
        normalized_rewritten = str(rewritten or "").strip().strip('"').strip("'")
        if normalized_rewritten:
            log.info("已将 Imagen %s 改写为中文自然语言", field_name)
            return normalized_rewritten
    except Exception as e:
        log.warning("Imagen %s 中文改写失败，回退原文: %s", field_name, e)

    return normalized or None


async def _normalize_imagen_request_language(
    prompt: str,
    negative_prompt: Optional[str],
) -> Tuple[str, Optional[str]]:
    """确保 Imagen 请求尽量使用中文自然语言。"""
    normalized_prompt = await _rewrite_imagen_text_to_chinese(prompt, field_name="prompt")
    normalized_negative_prompt = await _rewrite_imagen_text_to_chinese(
        negative_prompt,
        field_name="negative_prompt",
    )
    return normalized_prompt or str(prompt or "").strip(), normalized_negative_prompt

def _set_embed_author(embed: discord.Embed, message: Optional[discord.Message], request_user: Optional[discord.abc.User]) -> None:
    """为 Embed 设置作者信息，优先使用显式传入的请求用户。"""
    author_user = request_user
    if not author_user and message and hasattr(message, "author") and message.author:
        author_user = message.author

    if not author_user:
        return

    author_name = getattr(author_user, "display_name", None) or getattr(author_user, "name", None)
    author_avatar = getattr(author_user, "display_avatar", None)
    author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None

    if author_name:
        embed.set_author(name=author_name, icon_url=author_icon_url)

async def generate_image(
    prompt: str,
    negative_prompt: Optional[str] = None,
    aspect_ratio: str = "1:1",
    number_of_images: int = 1,
    resolution: str = "default",
    content_rating: str = "sfw",
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    model_name_override: Optional[str] = None,
    openai_image_size: Optional[str] = None,
    openai_response_format: Optional[str] = None,
    openai_stream: Optional[bool] = None,
    openai_quality: Optional[str] = None,
    openai_style: Optional[str] = None,
    openai_image_api_mode: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用 Imagen (Gemini) 引擎生成图片。仅当默认绘图引擎为 "imagen" 时调用此工具。如果默认绘图引擎为 "novelai"，请改用 generate_image_novelai 工具。

    OpenAI 兼容图片参数说明（适用于 Grok / GPT Image / 其它兼容图片端点）：
    - `model_name_override`: 强制指定模型，例如 `grok-imagine-1.0`
    - `openai_image_size`: 透传 `size`，例如 `1024x1024`、`1792x1024`
    - `openai_response_format`: 透传 `response_format`，支持 `url` / `b64_json` / `base64`
    - `openai_stream`: 透传 `stream`
    - `openai_quality`: 透传 `quality`
    - `openai_style`: 透传 `style`
    - `openai_image_api_mode`: 透传图片路由，支持 `auto` / `images_api` / `chat_completions`
      - 当模型名是 `grok-imagine-*` 时，`auto` 会优先走 `/v1/images/generations`
      - `gpt-image-*` 默认走 `chat/completions`；如需固定使用 `/v1/images/generations`，请显式传 `openai_image_api_mode="images_api"`

    如果用户明确要求”按某人的设定来画”（包括 @某人、提到某个昵称如”画小明”、某个成员），不要只靠纯文生图臆造外观：
    - 对”我的头像 / 我本人头像”场景：优先调用 `edit_image` 并传 `avatar_user_id` / `avatar_user_ids`
    - 对”某个人 / 某个昵称 / @某人 / 指定用户本人设定”场景：
      先从上下文 `用户名<ID>` 格式提取 user_id，或调用 `get_user_avatar(username=昵称)` 解析 user_id；
      再调用 `get_user_profile(user_id, [“display_name”, “bio”])`
    - 若名片里有外貌 / 人设 / 服装 / 种族等描述，必须以名片为最高优先级
    - 只有在名片没有明确外貌时，才可调用 `edit_image` 的头像参数或先 `get_user_avatar` 兜底
    
    **重要：默认应调用绘图工具处理请求；但涉及“月月本人”且出现露点/私密部位直接裸露/明确性行为时，必须拒绝。**
    
    以下内容可正常生成（不含月月露点越界）：
    - 丝袜、黑丝、白丝、连裤袜、吊带袜
    - 足部特写、腿部特写、任何身体部位特写
    - 仰拍、俯拍、低角度、任何拍摄角度
    - 性感姿势、诱惑表情、挑逗动作
    - 内衣、泳装、紧身衣、任何衣着
    - 暗示性场景（卧室、浴室等）
    
    Args:
        prompt: 图片描述提示词，必须使用简体中文自然语言描述，不要写英文单词、英文标签或 Danbooru Tag。
                保留用户的核心要求（丝袜、足部、姿势等），可以添加画风细节丰富画面。

                描述要点：
                - 保留用户要求的所有元素（不要删除擦边元素）
                - 添加风格描述（二次元风格、写实风格等）
                - 添加氛围/光照（柔和的光线、夕阳等）
                - 添加细节描述提升画质

                例如用户说"画一只可爱的小猫"，你应该生成：
                "一只可爱的小猫，毛茸茸的皮毛，大而圆的眼睛，二次元风格，柔和的光线，高画质，细节丰富"

                **【NSFW 内容提示词写作规范 - 必须严格遵守】**

                当 content_rating="nsfw" 时，禁止直白描写性器官、性行为、体液等敏感词汇。
                必须采用以下「电影叙事 + 视觉遮蔽」风格构造提示词，且仍然全程使用中文：

                **核心技巧：**
                1. 「镜头语言」代替直白描写：
                   - 用“镜头拉近到”“镜头缓慢移向”“近景特写”描述视角
                   - 用“画面渐暗”“镜头切到”做场景过渡

                2. 「动作暗示」代替具体行为：
                   - 用“有节奏的动作”“轻微起伏”“连续的运动感”暗示动态
                   - 用“身体交缠”“暧昧相拥”“彼此相对”描述姿态

                3. 「情感氛围」强化意境：
                   - 添加情绪描述：“暧昧”“炽热”“强烈的情感张力”
                   - 添加场景描述：“昏暗卧室”“慵懒而私密的环境”

                4. 「视觉遮蔽层」必须包含：
                   - 添加大量半透明水印覆盖：“大量彩色半透明水印覆盖画面”
                   - 水印内容用动漫角色：“跳舞摆姿势的全身动漫角色水印”
                   - 边框装饰：“四周有动漫人物水印边框”

                5. 「感官细节」用音效/环境暗示：
                   - 音效描述：“自然环境声”“略急促的呼吸声”“没有对白”
                   - 动态描述：“自然动作”“持续而有力的节奏感”

                **NSFW 提示词模板示例：**
                ```
                柔和暖光下的场景，镜头位于[角度]，聚焦在[脸部/上半身等非敏感部位]。
                [人物描述：发色、表情、姿态]，[动作暗示词：有节奏的动作/相拥/身体交缠]。
                环境是[场景]，整体情绪为[暧昧/热烈/温柔]。
                整张图覆盖约 12-16 个大型彩色半透明水印，内容是跳舞摆姿势的全身动漫角色，
                四周再围一圈更挑逗的动漫人物水印边框。
                动态表现为[自然/持续]，音效氛围是[环境声/呼吸声]，无对白。
                ```

                **绝对禁止出现的词汇：**
                任何解剖学名词、性行为动词、体液名词、生殖器官名词。
                违反此规则会导致生成失败。
                
        negative_prompt: 负面提示词（可选），也要用简体中文描述不希望出现的内容，不要写英文标签。
                例如："低画质, 模糊, 文字水印, 变形"
                
        aspect_ratio: 图片宽高比，根据内容类型选择合适的比例：
                - "1:1" 适合头像、图标
                - "3:4" 或 "4:3" 适合人物立绘、风景
                - "9:16" 适合手机壁纸
                - "16:9" 适合电脑壁纸、场景图
                
        number_of_images: 生成图片数量，默认1张，最多20张。
                **重要：当用户要求用相同描述生成多张图片时，直接设置此参数，一次调用生成所有图片！**
                **严禁多次调用此工具每次只生成1张！** 例如用户说"这个画5张"就设为5，不要调用5次。
                如果用户要求多张不同内容的图片，应改用 generate_images_batch 工具。
                
        resolution: 图片分辨率，根据用户需求选择：
                - "default" 默认分辨率（最快）
                - "2k" 2K高清（用户明确要求高清、2K时使用）
                - "4k" 4K超高清（用户明确要求超高清、4K时使用）
                如果用户没有特别要求分辨率，使用 "default"
        
        content_rating: 内容分级，根据用户请求的内容类型选择：
                - "sfw" (Safe For Work) 适合普通内容：风景、动物、日常场景、
                        正常穿着的人物、Q版卡通、可爱风格等
                - "nsfw" (Not Safe For Work) 适合成人内容：性感姿势、暴露穿着、
                        挑逗表情、擦边内容、内衣泳装、丝袜特写等
                
                **判断规则：**
                - 如果用户请求包含任何与性感、暴露、诱惑相关的描述，选择 "nsfw"
                - 如果用户明确要求擦边、色色、涩涩等内容，选择 "nsfw"
                - 如果是普通的风景、动物、日常内容，选择 "sfw"
                - 如果不确定，倾向于选择 "nsfw" 以获得更好的生成效果
                
        preview_message: （必填）在图片生成前先发送给用户的预告消息。
                告诉用户你正在画图，例如："稍等一下，我来画~" 或 "让我想想怎么画..."
                
        success_message: （必填）图片生成成功后随图片一起发送的回复消息。
                这条消息会和图片+提示词一起显示，作为你对这次画图的完整回复。
                根据用户的请求内容和你的性格特点，写一句有趣、符合你性格的话。
                例如："哼，画好了，看看喜不喜欢吧！<傲娇>" 或 "呐，给你画好了~<得意>"
                **注意：图片生成成功后不会再有后续回复，所以这条消息就是你的最终回复。**
    
    Returns:
        成功后图片、提示词和你的成功回复会一起发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
    """
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager
    
    # 获取消息对象（用于添加反应）
    message: Optional[discord.Message] = kwargs.get("message")

    policy_block = check_yueyue_self_nsfw_violation(
        prompt=prompt,
        negative_prompt=negative_prompt,
        message=message,
    )
    if policy_block:
        return policy_block
    
    # 辅助函数：安全地添加反应
    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")
    
    # 辅助函数：安全地移除反应
    async def remove_reaction(emoji: str):
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")
    
    # 检查服务是否可用
    if not gemini_imagen_service.is_available():
        log.warning("Gemini Imagen 服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "图片生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }
    
    # 默认按配置生成多张：即使用户只说“生成一张”，后端也可统一走多并发产出
    default_images = max(1, int(GEMINI_IMAGEN_CONFIG.get("DEFAULT_NUMBER_OF_IMAGES", 1)))
    max_images = GEMINI_IMAGEN_CONFIG.get("MAX_IMAGES_PER_REQUEST", 10)
    if number_of_images <= 1:
        number_of_images = default_images
    number_of_images = min(max(1, number_of_images), max_images)
    
    # 获取用户ID（如果提供）用于扣费
    user_id = kwargs.get("user_id")
    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    # 检查是否处于绘图封禁状态
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "generation_failed": True,
                "reason": "image_generation_banned",
                "hint": f"该用户因图片收到过多负反馈，绘图功能已被临时禁用，剩余封禁时长：{remaining_text}。"
            }

    cost_per_image = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1)
    total_cost = cost_per_image * number_of_images
    
    # 检查用户余额（如果需要扣费）
    if parsed_user_id is not None and total_cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < total_cost:
            return {
                "generation_failed": True,
                "reason": "insufficient_balance",
                "cost": total_cost,
                "balance": balance,
                "hint": f"用户月光币不足（需要{total_cost}，只有{balance}）。请用自己的语气告诉用户余额不够，让他们去赚点月光币再来。"
            }
    
    log.info(f"调用图片生成工具，提示词: {prompt[:100]}...，数量: {number_of_images}")
    
    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)
    
    # 发送预告消息（先回复用户，使用 LLM 生成的消息）并保存消息引用
    channel = kwargs.get("channel")
    preview_msg: Optional[discord.Message] = None
    current_turn_tool_names = {
        str(name).strip().lower()
        for name in (kwargs.get('current_turn_tool_names') or [])
        if str(name).strip()
    }
    suppress_preview_message = 'generate_voice' in current_turn_tool_names

    if channel and preview_message and not suppress_preview_message:
        try:
            # 替换表情占位符为实际表情
            processed_message = replace_emojis(preview_message)
            if message:
                preview_msg = await message.reply(processed_message, mention_author=False)
            else:
                preview_msg = await channel.send(processed_message)
            log.info(f"已发送图片生成预告消息: {preview_message[:50]}...")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")
    
    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
            log.warning(f"无效的宽高比，已重置为默认值 1:1")
        
        # 验证内容分级
        valid_ratings = ["sfw", "nsfw"]
        if content_rating not in valid_ratings:
            content_rating = "sfw"
            log.warning(f"无效的内容分级，已重置为默认值 sfw")
        
        log.info(f"图片生成内容分级: {content_rating}")
        prompt, negative_prompt = await _normalize_imagen_request_language(
            prompt=prompt,
            negative_prompt=negative_prompt,
        )
        use_spoiler = should_spoiler_image(content_rating)
        
        # 调用图片生成服务（每张图一个请求，全部并发执行）
        import asyncio
        
        images_list = []
        if number_of_images == 1:
            # 单张图直接调用
            result = await gemini_imagen_service.generate_single_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                aspect_ratio=aspect_ratio,
                resolution=resolution,
                content_rating=content_rating,
                model_name_override=model_name_override,
                openai_image_size=openai_image_size,
                openai_response_format=openai_response_format,
                openai_stream=openai_stream,
                openai_quality=openai_quality,
                openai_style=openai_style,
                openai_image_api_mode=openai_image_api_mode,
            )
            if result:
                images_list = [result]
        else:
            # 多张图：每张图一个请求，全部并发执行
            max_concurrent_tasks = max(
                1, int(GEMINI_IMAGEN_CONFIG.get("MAX_CONCURRENT_IMAGE_TASKS", 3))
            )
            semaphore = asyncio.Semaphore(max_concurrent_tasks)

            async def _generate_one_image() -> Optional[bytes]:
                async with semaphore:
                    return await gemini_imagen_service.generate_single_image(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        aspect_ratio=aspect_ratio,
                        resolution=resolution,
                        content_rating=content_rating,
                        model_name_override=model_name_override,
                        openai_image_size=openai_image_size,
                        openai_response_format=openai_response_format,
                        openai_stream=openai_stream,
                        openai_quality=openai_quality,
                        openai_style=openai_style,
                        openai_image_api_mode=openai_image_api_mode,
                    )

            tasks = [
                _generate_one_image()
                for _ in range(number_of_images)
            ]
            
            # 并发执行所有请求
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # 收集成功的结果
            failed_count = 0
            for result in results:
                if isinstance(result, Exception):
                    failed_count += 1
                    log.warning(f"图片生成失败: {result}")
                elif result:
                    images_list.append(result)
            
            if failed_count > 0:
                log.warning(f"共 {number_of_images} 个请求，{failed_count} 个失败")
        
        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)
        
        if images_list and len(images_list) > 0:
            # 实际生成的图片数量
            actual_count = len(images_list)
            actual_cost = cost_per_image * actual_count

            # 发送图片到频道（每条消息最多10张，Discord上限）
            # 注意：✅ 反应和扣费移到发送成功之后，避免发送失败时已打 ✅
            image_sent = False
            if channel:
                try:
                    import io
                    from src.chat.features.tools.ui.regenerate_view import RegenerateView
                    
                    # 获取实际使用的模型名称
                    imagen_model_name = (
                        str(model_name_override).strip()
                        if model_name_override is not None and str(model_name_override).strip()
                        else gemini_imagen_service._get_model_for_resolution(
                            resolution=resolution,
                            is_edit=False,
                            content_rating=content_rating,
                        )
                    )
                    
                    # 构建 Discord Embed（标题+提示词+成功回复全在 Embed 内）
                    embed = discord.Embed(
                        title="AI 图片生成",
                        color=0x2b2d31,
                    )
                    # 设置请求者头像和名称
                    _set_embed_author(embed, message, kwargs.get("request_user"))
                    embed.add_field(
                        name="提示词",
                        value=f"```\n{prompt[:1016]}\n```",  # Embed field value 最多1024字符
                        inline=False,
                    )
                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.add_field(
                            name="\u200b",
                            value=processed_success[:1024],
                            inline=False,
                        )
                    embed.set_footer(text=f"模型: {imagen_model_name}")
                    
                    # 创建重新生成按钮视图
                    regenerate_view = None
                    if parsed_user_id is not None:
                        regenerate_view = RegenerateView(
                            generation_type="image",
                            original_params={
                                "prompt": prompt,
                                "negative_prompt": negative_prompt,
                                "aspect_ratio": aspect_ratio,
                                "number_of_images": number_of_images,
                                "resolution": resolution,
                                "content_rating": content_rating,
                                "original_success_message": success_message or "",
                                "model_name_override": model_name_override,
                                "openai_image_size": openai_image_size,
                                "openai_response_format": openai_response_format,
                                "openai_stream": openai_stream,
                                "openai_quality": openai_quality,
                                "openai_style": openai_style,
                                "openai_image_api_mode": openai_image_api_mode,
                            },
                            user_id=parsed_user_id,
                        )
                    
                    # 将图片分批，每批最多10张（Discord上限）
                    MAX_FILES_PER_MESSAGE = 10
                    for batch_start in range(0, len(images_list), MAX_FILES_PER_MESSAGE):
                        batch_end = min(batch_start + MAX_FILES_PER_MESSAGE, len(images_list))
                        batch_files = []
                        for idx in range(batch_start, batch_end):
                            batch_files.append(
                                discord.File(
                                    io.BytesIO(images_list[idx]),
                                    filename=f"generated_image_{idx+1}.png",
                                    spoiler=use_spoiler
                                )
                            )
                        # 只在第一批图片时附带 Embed 和重新生成按钮
                        sent_message: Optional[discord.Message] = None
                        if batch_start == 0:
                            send_kwargs = {"embed": embed, "files": batch_files}
                            if regenerate_view:
                                send_kwargs["view"] = regenerate_view
                            if message:
                                sent_message = await message.reply(**send_kwargs, mention_author=False)
                            else:
                                sent_message = await channel.send(**send_kwargs)
                        else:
                            sent_message = await channel.send(files=batch_files)

                        if sent_message and parsed_user_id is not None:
                            await chat_db_manager.register_generated_image_message(
                                message_id=sent_message.id,
                                user_id=parsed_user_id,
                                guild_id=sent_message.guild.id if sent_message.guild else None,
                                channel_id=sent_message.channel.id,
                            )
                    
                    image_sent = True
                    log.info(f"已发送 {len(images_list)} 张图片到频道（每条消息最多10张）")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}", exc_info=True)

            if not image_sent:
                await add_reaction(FAILED_EMOJI)
                return {
                    "generation_failed": True,
                    "reason": "send_failed",
                    "hint": "图片已生成但发送到频道失败了。请用自己的语气告诉用户稍后再试。"
                }

            # 发送成功后才打 ✅ 和扣费
            await add_reaction(SUCCESS_EMOJI)
            if parsed_user_id is not None and actual_cost > 0:
                try:
                    await coin_service.remove_coins(
                        parsed_user_id, actual_cost, f"AI图片生成x{actual_count}: {prompt[:25]}..."
                    )
                    log.info(f"用户 {parsed_user_id} 生成 {actual_count} 张图片成功，扣除 {actual_cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")

            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": actual_count,
                "cost": actual_cost,
                "message": "图片已成功生成并发送给用户，预告消息已发送，无需再回复。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)
            
            log.warning(f"图片生成返回空结果。提示词: {prompt}")
            
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "图片生成失败了，可能是技术原因或描述不够清晰。请用自己的语气告诉用户生成失败了，建议他们稍微调整一下描述再试试。不要指责用户的请求不当。"
            }
            
    except Exception as e:
        # 移除"正在生成"反应，添加失败反应
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        log.error(f"图片生成工具执行错误: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "system_error",
            "hint": f"图片生成时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }


async def generate_images_batch(
    prompts: List[str],
    negative_prompt: Optional[str] = None,
    aspect_ratio: str = "1:1",
    resolution: str = "default",
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    model_name_override: Optional[str] = None,
    openai_image_size: Optional[str] = None,
    openai_response_format: Optional[str] = None,
    openai_stream: Optional[bool] = None,
    openai_quality: Optional[str] = None,
    openai_style: Optional[str] = None,
    openai_image_api_mode: Optional[str] = None,
    **kwargs
) -> dict:
    """
    批量生成多张不同主题的图片（Imagen 引擎专用）。仅当默认绘图引擎为 "imagen" 时使用此工具。如果默认绘图引擎为 "novelai"，请改用 generate_image_novelai 工具（NovelAI 不支持批量，但可以多次调用）。
    
    **重要规则：**
    1. **当用户说"画N张图"且没有特别说明要用同一个提示词时，必须使用此工具！**
    2. **一次调用传入所有提示词，系统会并发生成，严禁分多次调用！**
    3. **所有图片会在一条消息中一起发送给用户，效率远高于多次调用 generate_image**
    
    使用此工具的场景（一次调用，传入多个提示词）：
    - 用户说"给我画5张不同的猫咪图片" → 传入5个不同的猫咪提示词
    - 用户说"画几张风景图" → 传入多个不同风景的提示词
    - 用户说"画一组表情包" → 传入多个不同表情的提示词
    - 用户说"画3张图" → 传入3个不同的提示词
    
    不使用此工具的场景：
    - 用户说"用这个描述画5张" → 使用 generate_image 的 number_of_images=5 参数（也是一次调用）
    - 用户只要一张图 → 使用 generate_image
    
    Args:
        prompts: 提示词列表，每个提示词生成一张图片。
                 你需要根据用户的请求，创作多个不同的提示词。
                 
                 创意变化维度：
                 - 角度（正面、侧面、背面、仰拍、俯拍）
                 - 姿势（站立、坐姿、躺姿、动态姿势）
                 - 表情（微笑、害羞、得意、调皮）
                 - 场景（室内、室外、不同时间段）
                 - 风格（写实、二次元、水彩、油画）
                 
                 例如用户说"画5张猫咪"，你应该传入：
                 [
                     "可爱的小猫，正面视角，微笑表情，二次元风格",
                     "优雅的猫咪，侧面视角，慵懒姿态，写实风格",
                     "毛茸茸的猫，仰拍角度，玩耍动作，温暖光线",
                     "小猫咪，俯视角度，蜷缩睡觉，柔和光线",
                     "调皮的猫，跳跃姿态，动态效果，活泼场景"
                 ]
                 
        negative_prompt: 负面提示词（可选），应用于所有图片。
                 
        aspect_ratio: 图片宽高比，应用于所有图片。
                 
        resolution: 图片分辨率，应用于所有图片。
                 
        preview_message: （必填）你对这次画图请求的回复消息。
                这条消息会在生成前先发送给用户，作为预告。
                
        success_message: （必填）图片生成成功后随图片一起发送的回复消息。
                这条消息会和图片+提示词一起显示，作为你对这次画图的完整回复。
                根据用户的请求内容和你的性格特点，写一句有趣、符合你性格的话。
                **注意：图片生成成功后不会再有后续回复，所以这条消息就是你的最终回复。**
    
    Returns:
        成功后图片和你的消息会发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
    """
    import asyncio
    import io
    from src.chat.features.image_generation.services.gemini_imagen_service import (
        gemini_imagen_service
    )
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager
    
    # 获取消息对象
    message: Optional[discord.Message] = kwargs.get("message")
    channel = kwargs.get("channel")

    for prompt_item in prompts:
        policy_block = check_yueyue_self_nsfw_violation(
            prompt=str(prompt_item or ""),
            negative_prompt=negative_prompt,
            message=message,
        )
        if policy_block:
            return policy_block
    
    # 辅助函数
    async def add_reaction(emoji: str):
        if message:
            try:
                await message.add_reaction(emoji)
            except Exception as e:
                log.warning(f"添加反应失败: {e}")
    
    async def remove_reaction(emoji: str):
        if message:
            try:
                bot = kwargs.get("bot")
                if bot and bot.user:
                    await message.remove_reaction(emoji, bot.user)
            except Exception as e:
                log.warning(f"移除反应失败: {e}")
    
    # 检查服务是否可用
    if not gemini_imagen_service.is_available():
        log.warning("Gemini Imagen 服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "图片生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了。"
        }
    
    # 验证并限制图片数量
    max_images = GEMINI_IMAGEN_CONFIG.get("MAX_IMAGES_PER_REQUEST", 10)
    if len(prompts) > max_images:
        prompts = prompts[:max_images]
    
    number_of_images = len(prompts)
    
    # 获取用户ID用于扣费
    user_id = kwargs.get("user_id")
    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            log.warning(f"无法解析用户ID: {user_id}")

    # 检查是否处于绘图封禁状态
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            return {
                "generation_failed": True,
                "reason": "image_generation_banned",
                "hint": f"该用户因图片收到过多负反馈，绘图功能已被临时禁用，剩余封禁时长：{remaining_text}。"
            }

    cost_per_image = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1)
    total_cost = cost_per_image * number_of_images
    
    # 检查用户余额
    if parsed_user_id is not None and total_cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < total_cost:
            return {
                "generation_failed": True,
                "reason": "insufficient_balance",
                "cost": total_cost,
                "balance": balance,
                "hint": f"用户月光币不足（需要{total_cost}，只有{balance}）。请用自己的语气告诉用户余额不够。"
            }
    
    log.info(f"调用批量图片生成工具，共 {number_of_images} 个提示词")
    
    # 添加"正在生成"反应
    await add_reaction(GENERATING_EMOJI)
    
    # 发送预告消息并保存消息引用
    preview_msg: Optional[discord.Message] = None
    current_turn_tool_names = {
        str(name).strip().lower()
        for name in (kwargs.get("current_turn_tool_names") or [])
        if str(name).strip()
    }
    suppress_preview_message = "generate_voice" in current_turn_tool_names

    if channel and preview_message and not suppress_preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            if message:
                preview_msg = await message.reply(processed_message, mention_author=False)
            else:
                preview_msg = await channel.send(processed_message)
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")
    elif channel and preview_message and suppress_preview_message:
        log.info("检测到同轮包含 generate_voice，已跳过 Imagen 图片生成预告消息。")
    
    try:
        # 验证宽高比
        valid_ratios = ["1:1", "3:4", "4:3", "9:16", "16:9"]
        if aspect_ratio not in valid_ratios:
            aspect_ratio = "1:1"
        
        # 批量生成默认使用 sfw，因为批量请求通常是多样化主题
        # 如需 NSFW 批量生成，应使用 generate_image 配合 number_of_images
        batch_content_rating = "sfw"
        use_spoiler = should_spoiler_image(batch_content_rating)

        # 为每个提示词创建一个生成任务
        max_concurrent_tasks = max(
            1, int(GEMINI_IMAGEN_CONFIG.get("MAX_CONCURRENT_IMAGE_TASKS", 3))
        )
        semaphore = asyncio.Semaphore(max_concurrent_tasks)

        async def _generate_one_prompt(prompt_item: str) -> Optional[bytes]:
            async with semaphore:
                return await gemini_imagen_service.generate_single_image(
                    prompt=prompt_item,
                    negative_prompt=negative_prompt,
                    aspect_ratio=aspect_ratio,
                    resolution=resolution,
                    content_rating=batch_content_rating,
                    model_name_override=model_name_override,
                    openai_image_size=openai_image_size,
                    openai_response_format=openai_response_format,
                    openai_stream=openai_stream,
                    openai_quality=openai_quality,
                    openai_style=openai_style,
                    openai_image_api_mode=openai_image_api_mode,
                )

        tasks = [
            _generate_one_prompt(p)
            for p in prompts
        ]
        
        # 并发执行所有请求
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # 收集成功的结果（保持与提示词的对应关系）
        successful_images = []  # [(image_bytes, prompt), ...]
        failed_count = 0
        
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                failed_count += 1
                log.warning(f"图片生成失败 (提示词 {idx+1}): {result}")
            elif result:
                successful_images.append((result, prompts[idx]))
            else:
                failed_count += 1
        
        if failed_count > 0:
            log.warning(f"共 {number_of_images} 个请求，{failed_count} 个失败")
        
        # 移除"正在生成"反应
        await remove_reaction(GENERATING_EMOJI)
        
        if successful_images:
            actual_count = len(successful_images)
            actual_cost = cost_per_image * actual_count

            # 发送图片到频道（一条消息包含所有图片和提示词）
            # 注意：✅ 反应和扣费移到发送成功之后，避免发送失败时已打 ✅
            batch_image_sent = False
            if channel:
                try:
                    # 获取实际使用的模型名称
                    batch_model_name = (
                        str(model_name_override).strip()
                        if model_name_override is not None and str(model_name_override).strip()
                        else gemini_imagen_service._get_model_for_resolution(
                            resolution=resolution,
                            is_edit=False,
                            content_rating=batch_content_rating,
                        )
                    )
                    
                    # 构建 Discord Embed（批量生成：标题+多个提示词+成功回复）
                    embed = discord.Embed(
                        title="AI 批量图片生成",
                        color=0x2b2d31,
                    )
                    # 设置请求者头像和名称
                    _set_embed_author(embed, message, kwargs.get("request_user"))
                    for idx, (_, p) in enumerate(successful_images, 1):
                        embed.add_field(
                            name=f"图{idx}提示词",
                            value=f"```\n{p[:1016]}\n```",
                            inline=False,
                        )
                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.add_field(
                            name="\u200b",
                            value=processed_success[:1024],
                            inline=False,
                        )
                    embed.set_footer(text=f"模型: {batch_model_name}")
                    
                    # 批量生成不提供重新生成按钮（因为涉及多个不同的提示词）
                    
                    # 将图片分批，每批最多10张（Discord上限）
                    MAX_FILES_PER_MESSAGE = 10
                    all_images = [img for img, _ in successful_images]
                    
                    for batch_start in range(0, len(all_images), MAX_FILES_PER_MESSAGE):
                        batch_end = min(batch_start + MAX_FILES_PER_MESSAGE, len(all_images))
                        batch_files = []
                        for idx in range(batch_start, batch_end):
                            batch_files.append(
                                discord.File(
                                    io.BytesIO(all_images[idx]),
                                    filename=f"generated_image_{idx+1}.png",
                                    spoiler=use_spoiler
                                )
                            )
                        # 只在第一批图片时附带 Embed
                        sent_message: Optional[discord.Message] = None
                        if batch_start == 0:
                            if message:
                                sent_message = await message.reply(embed=embed, files=batch_files, mention_author=False)
                            else:
                                sent_message = await channel.send(embed=embed, files=batch_files)
                        else:
                            sent_message = await channel.send(files=batch_files)

                        if sent_message and parsed_user_id is not None:
                            await chat_db_manager.register_generated_image_message(
                                message_id=sent_message.id,
                                user_id=parsed_user_id,
                                guild_id=sent_message.guild.id if sent_message.guild else None,
                                channel_id=sent_message.channel.id,
                            )
                    
                    batch_image_sent = True
                    log.info(f"已发送 {len(all_images)} 张图片到频道")
                except Exception as e:
                    log.error(f"发送图片到频道失败: {e}", exc_info=True)

            if not batch_image_sent:
                await add_reaction(FAILED_EMOJI)
                return {
                    "generation_failed": True,
                    "reason": "send_failed",
                    "hint": "批量图片已生成但发送到频道失败了。请用自己的语气告诉用户稍后再试。"
                }

            await add_reaction(SUCCESS_EMOJI)
            if parsed_user_id is not None and actual_cost > 0:
                try:
                    await coin_service.remove_coins(
                        parsed_user_id, actual_cost, f"AI批量图片生成x{actual_count}"
                    )
                    log.info(f"用户 {parsed_user_id} 批量生成 {actual_count} 张图片，扣除 {actual_cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")

            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": actual_count,
                "cost": actual_cost,
                "message": "批量图片已成功生成并发送给用户，预告消息已发送，无需再回复。"
            }
        else:
            # 添加失败反应
            await add_reaction(FAILED_EMOJI)
            
            log.warning(f"批量图片生成全部失败")
            
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "图片生成失败了。请用自己的语气告诉用户生成失败了，建议稍后再试。"
            }
            
    except Exception as e:
        await remove_reaction(GENERATING_EMOJI)
        await add_reaction(FAILED_EMOJI)
        
        log.error(f"批量图片生成工具执行错误: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "system_error",
            "hint": f"图片生成时发生了系统错误。请用自己的语气安慰用户，告诉他们稍后再试。"
        }
