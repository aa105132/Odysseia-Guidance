# -*- coding: utf-8 -*-

"""
NovelAI 图片生成工具
让 LLM 可以在对话中自动调用 NovelAI 生成图片。
AI 需要生成符合 Danbooru 格式的英文 Tag 来作为 prompt。

遵循 NAI 预设规则:
- Tag 必须是 Danbooru 格式的英文单词/词语，逗号分隔
- 单图 Tag 数量 ≥ 90 个
- 使用权重语法: n::Tag:: (n>1 增强, n<1 减弱)
- 支持角色 DNA 系统确保角色一致性
- 支持 Character Prompt + Character UC 分离
- 支持场景模板库关键词匹配
"""

import logging
import io
import random
import discord
from typing import Optional, List

from src.chat.utils.prompt_utils import replace_emojis
from src.chat.features.novelai_generation.tag_rules import (
    NOVELAI_TAG_RULES,
    TAG_LIBRARY_COMPACT,
    get_rewrite_prompt,
)

log = logging.getLogger(__name__)


def _prompt_already_contains_artist(prompt: str, artist_string: str) -> bool:
    """
    检查 prompt 中是否已经包含了画师串的内容，防止重复拼接。

    策略：提取画师串中的 artist:xxx tag，检查 prompt 是否已包含其中的大部分。
    如果画师串中超过一半的 artist tag 已存在于 prompt 中，认为已包含。
    """
    import re
    # 提取画师串中的 artist: tag（忽略权重语法）
    artist_tags = re.findall(r'artist[:\s]+[\w\-_/()]+', artist_string.lower())
    if not artist_tags:
        # 画师串中没有明确的 artist: tag，用前几个 tag 做简单比对
        first_tags = [t.strip().lower() for t in artist_string.split(",")[:3] if t.strip()]
        if not first_tags:
            return False
        prompt_lower = prompt.lower()
        match_count = sum(1 for t in first_tags if t in prompt_lower)
        return match_count >= max(1, len(first_tags) // 2)

    prompt_lower = prompt.lower()
    match_count = sum(1 for tag in artist_tags if tag in prompt_lower)
    return match_count > len(artist_tags) // 2


async def generate_image_novelai(
    prompt: str,
    negative_prompt: Optional[str] = None,
    width: int = 832,
    height: int = 1216,
    steps: int = 28,
    scale: float = 5.0,
    sampler: str = "k_euler_ancestral",
    seed: Optional[int] = None,
    preset_name: Optional[str] = None,
    skip_artist_prefix: bool = False,
    preview_message: Optional[str] = None,
    success_message: Optional[str] = None,
    **kwargs
) -> dict:
    """
    使用 NovelAI 引擎生成图片。当默认绘图引擎为 "novelai" 时，所有画图请求都必须使用此工具，不要使用 generate_image。

    **重要：你必须生成 Danbooru 格式的英文 Tag 作为 prompt！不要使用自然语言描述！**

    ## 画师串自动拼接说明（重要）
    系统会自动在你的 prompt 前面拼接用户保存的「画师串预设」（artist string）。
    - **你生成的 prompt 中不要包含画师串/artist tag**，系统会自动处理。
    - 你只需要专注于生成场景、角色、动作、表情等内容 Tag。
    - 如果用户明确说"不要画师串"、"不用预设"、"裸 prompt"等，请传 `skip_artist_prefix=True`。
    - 默认情况下不需要传这个参数，系统会自动拼接。

    ## Tag 生成核心规则（必须严格遵守）：

    ### 1. 基本要求
    - 使用英文 Danbooru 格式 Tag，逗号分隔
    - 单图 Tag 数量 ≥ 90 个
    - 禁止使用中文或自然语言句子
    - 定格画面：单图为同一时刻的静态瞬间，禁止连续动作过程
    - 单图最多 4 个角色，最多 2 个女性角色
    - **不要在 prompt 中写画师串（artist:xxx），系统会自动拼接**

    ### 2. Tag 构成顺序（按优先级）
    按以下顺序构建 Tag：
    1. **质量 Tag**: masterpiece, best quality, amazing quality, very aesthetic, absurdres
    2. **场景构成 (5~10%)**: nsfw/sfw, 角色数量(1girl, solo), 角色关系(hetero, harem, yuri)
    3. **背景 (10~20%)**: 年代(modern, medieval, fantasy), 环境(bedroom, park, outdoor, onsen, train interior), 时间(night, sunset, golden hour), 氛围(mystical atmosphere), 光影(backlighting, rim lighting, sidelighting, dramatic shadows, moonlight, neon light, spotlight, tyndall effect)
    4. **构图 (10~20%)**: 区域(full body, upper body, cowboy shot), 远近(close-up, mid shot, wide shot), 透视(wide-angle, foreshortening, fisheye), 视角(front view, from behind, from above, from below, from side, pov, male pov), 焦点(face focus, ass focus, breast focus, foot focus, crotch focus), 角度(cinematic angle, dynamic angle, dutch angle), 效果(depth of field, bokeh, motion blur)
    5. **角色 DNA - 身份**: 性别(girl, boy), 姓名(同人角色用英文全名(来源), 原创用original), 身份(bishoujo, maid, loli, milf, office lady)
    6. **角色 DNA - 外貌**: 发长/发型/发色/瞳色/罩杯(flat chest=A, small breasts=B, medium breasts=C, large breasts=D, huge breasts=E, gigantic breasts=F+)/肤色/修饰(makeup, scar, tan lines, bangs, petite, curvy, narrow waist, wide hips)
    7. **角色 DNA - 服饰**: 核心服饰(上装/下装/内衣/袜子/鞋类/配饰, 含风格/品类/颜色), 材质(plaid, latex, satin, velvet, sheer fabric, lace), 状态(wet clothes, torn clothes, see-through), 穿着状态(nude, open shirt, strap slip, no panties, clothes lift, skirt lift), 裸露部位(pussy, ass, nipples, navel)
    8. **当前动作**: 基础姿势(sitting, standing, lying, kneeling, all fours, squatting), 肢体动作(heart hands, head down, leg lift, v, arms up, peace sign), 核心交互(walking, masturbation, hug, kiss, sex, fellatio), 物理反馈(bouncing breasts, ass ripple, skin indentation, motion lines), 交互接触点(明确动作主体+做什么+放在哪, 如: grabbing own breasts, grabbing another's ass, holding phone)
    9. **当前表情**: 视线(looking at viewer, looking down, looking back, looking away), 眼(tears, wide-eyed, dilated pupils, half-closed eyes, empty eyes), 嘴(smile, open mouth, tongue out, clenched teeth, :3, pout), 感官(blush, ahegao, excited, embarrassed, flustered)
    10. **表现力/微细节**: 环境氛围(falling leaves, fireworks, steam, floating sakura, light particles), 生理反应(full-face blush, dilated pupils, drooling, heavy breathing, sweat, steaming body, shiny skin, wet body), 动态互动(speed lines, motion lines, bouncing breasts, splashing fluids, motion blur), 特效粒子(magical, ripple, glowing, light particles, sound effects)

    ### 3. 碎片化转译（具象化，拒绝模糊）
    将复合概念拆解为多个具体 Tag：
    - 月下 -> moonlit, night, starry sky
    - 战斗 -> battle, standing, holding sword
    - 害羞 -> shy, blushing, looking down, fidgeting
    - '好热' -> sweating, fanning self, loosening clothes
    - 色情浴室 -> bathroom, steam, wet body, shiny skin, nude
    - 上课无聊 -> classroom, sitting, chin rest, looking away, yawning

    ### 4. 权重调整（重要）
    - 增强核心元素: `1.2::Tag::` 或 `1.3::Tag::`
    - 减弱次要元素: `0.8::Tag::` 或 `0.7::Tag::`
    - 增强 3~8 次，减弱 2~4 次
    - 增强优先级: 同人角色姓名 > 核心动作 > 服饰 > 特效 > 表情

    ### 5. 画面范围（只保留视觉可见 Tag）
    - 构图导致不可见: 下身特写->排除上身元素
    - 衣物覆盖导致不可见: 穿戴整齐->排除内衣Tag
    - 遮盖导致不可见: 抱胸->hands, breast hold, covered nipples

    ### 6. POV 建议（1女+1男且男=user时）
    - 无互动: 1girl, solo
    - 对视/对话: 1girl, solo, looking at viewer
    - 物理接触: 1girl, 1boy, male pov, pov hands
    - 性行为: 1girl, male pov, pov hands, penis

    ### 7. 多角色互动前缀
    - source#: 动作发起方 (如 source#grabbing another's breasts)
    - target#: 动作接收方 (如 target#being grabbed)
    - mutual#: 双方互动 (如 mutual#kissing)

    ### 8. 男性配角默认 Tag
    - 大几把路人男: 1boy, faceless male, muscular male, large penis, hetero
    - 黑人男: dark-skinned male, dark skin, muscular male
    - 正太: shota, age difference, size difference
    - 胖男: fat man, faceless male, ugly man

    ### 9. 画月月（自己）时的 Tag
    如果用户要求画"你"、"月月"、"自己"：
    - 1girl, solo, silver hair, high ponytail, crescent hair ornament, blue grey eyes
    - fox ears, white fox ears, pink inner ear, fox tail, silver white tail, fluffy tail
    - small triangular watermelon earrings（小巧的三角形西瓜耳坠）
    - white off-shoulder top, fur trim, detached sleeves, white high waist skirt, pink bow belt, silver necklace, jewelry

    ### 10. 参考标签库
    === 表情 ===
    grin, smile, smug, seductive smile, naughty face, glaring, pout, crying, sobbing, tears, surprised, flustered, blush, embarrassed, parted lips, open mouth, tongue out, ahegao, heart eyes, heart-shaped pupils, fucked silly, rolling eyes, empty eyes, half-closed eyes, wavy mouth, clenched teeth, :3, expressionless, evil smile, shaded face
    表情组合: ahegao+drooling+tears+rolling eyes(高潮), open mouth+heavy breathing+blush(插入), smile+blush+looking at viewer(温柔), clenched teeth+tears+blush(忍耐), tongue out+saliva+half-closed eyes(口交), empty eyes+expressionless(精神崩坏)

    === 姿势 ===
    standing, sitting, lying, kneeling, all fours, squatting, bent over, crawling, walking, running, jumping, contrapposto, seiza, wariza, leaning forward, leaning back, on stomach, on back, on side, top-down bottom-up, dogeza

    === 肢体 ===
    手臂: arm support, arms behind head, arms behind back, arms up, crossed arms, victory pose, outstretched arm, waving, beckoning
    腿部: crossed legs, spread legs, leg up, legs up, tiptoes, m legs, knees apart, legs together, standing split, leg lock
    手部: thumbs up, peace sign, double peace sign, heart hands, finger gun, v, double v, finger to mouth, shushing
    视线: looking at viewer, looking down, looking up, looking back, looking away, sideways glance, eye contact, upturned eyes

    === 衣物 ===
    状态: nude, completely nude, topless, bottomless, open shirt, no bra, no panties, strap slip, see-through, wet clothes, torn clothes, clothed female nude male, naked shirt, naked apron, clothes lift, skirt lift, shirt lift, panty pull
    开口: off-shoulder, bare shoulders, cleavage cutout, underboob cutout, center opening, navel cutout, back cutout, sideless dress, crotchless, nipple cutout
    类型: school uniform, sailor uniform, maid outfit, bikini, swimsuit, wedding dress, kimono, yukata, hanfu, gothic lolita, bunny girl, santa costume, leotard, bodysuit, latex, lingerie, babydoll, gym uniform, pajamas, naked apron, cheerleader
    配饰: choker, collar, leash, thigh strap, garter straps, thighhighs, pantyhose, high heels, boots, glasses, earrings, necklace, blindfold, ball gag

    === 发型/发色/瞳色 ===
    发型: long hair, short hair, ponytail, high ponytail, twintails, braid, bob cut, ahoge, bangs, sidelocks, wavy hair, curly hair, drill hair, messy hair, wet hair
    发色: black hair, blonde hair, brown hair, silver hair, white hair, red hair, blue hair, pink hair, purple hair, green hair, gradient hair
    瞳色: blue eyes, red eyes, green eyes, brown eyes, purple eyes, yellow eyes, heterochromia, heart-shaped pupils, slit pupils

    === 光影/氛围 ===
    光影: backlighting, rim lighting, sidelighting, dramatic shadows, moonlight, sunlight, neon light, golden hour, spotlight, tyndall effect, volumetric light, dimly lit, dark theme
    氛围: falling leaves, fireworks, steam, floating sakura, light particles, glowing, starry sky, rain, snow, petals, lens flare
    生理: sweat, heavy breathing, steaming body, trembling, drooling, tears, blush, shiny skin, oiled skin, wet body, twitching, flying sweatdrops
    动态: speed lines, motion lines, motion blur, bouncing breasts, ass ripple, sound effects

    === 性爱 ===
    体位: doggystyle, missionary, cowgirl position, reverse cowgirl, mating press, 69, girl on top, sex from behind, piledriver, spitroast, suspended congress, standing sex, prone bone, leg lock
    行为: sex, vaginal, anal, oral, fellatio, deepthroat, handjob, footjob, paizuri, thigh sex, buttjob, fingering, masturbation, deep penetration, cunnilingus, double penetration
    射精: cum, excessive cum, bukkake, facial, ejaculation, cumdrip, cum on body, internal cumshot, cum in mouth, cum in pussy, cum on breasts, cum string, cum overflow, after ejaculation, used condom
    BDSM: bondage, rope bondage, shibari, handcuffs, chains, blindfold, ball gag, collar, leash, pet play, spanking, slap mark, breast bondage, suspension
    双人: holding hands, eye contact, cuddling, princess carry, hug, hug from behind, breast press, grabbing another's breasts, lifting person, headpat, sitting on lap, face to face
    玩具: vibrator, dildo, egg vibrator, remote control vibrator, vibrator under panties, anal beads, butt plug, dildo riding, object insertion
    特殊: x-ray, cross section, stomach bulge, livestream, fake screenshot, exhibitionism, public indecency

    === 场景 ===
    室内: bedroom, bathroom, kitchen, classroom, library, office, hotel room, love hotel, bar, elevator, train interior, car interior, dungeon, church
    室外: beach, forest, park, alley, rooftop, garden, pool, shrine, street, ruins

    === 场景模板参考 ===
    骑乘: cowgirl position, girl on top, straddling, spread legs, sex, penis, motion lines
    后入: doggystyle, sex from behind, all fours, ass, from behind, grabbing hips
    传教士: missionary, on back, lying, spread legs, on bed, from above
    口交: fellatio, oral, kneeling, penis, pov, looking up, tongue out, saliva
    洗澡: bathroom, steam, completely nude, wet, wet hair, shiny skin, standing
    温泉: onsen, partially submerged, steam, wet body, nude, outdoors, rocks
    做饭: kitchen, cooking, apron, holding spatula, stove, steam
    自慰: female masturbation, fingering, spread legs, blush, heavy breathing, solo
    绳缚: bondage, rope bondage, shibari, bound wrists, nude, breast bondage, tears
    触手: tentacles, tentacle sex, restrained, spread legs, suspended, nude
    露出: exhibitionism, public indecency, outdoors, crowd, embarrassed, blush
    钢管舞: pole dancing, stripper pole, holding pole, armpits, spotlight, sweat
    酒吧: bar (place), cocktail, dim lighting, neon light, looking at viewer
    直播: livestream, chat log, fake screenshot, sitting, gaming chair, webcam

    ### 11. 示例
    用户说"画一个银发少女在月光下"，你应该生成：
    ```
    masterpiece, best quality, amazing quality, very aesthetic, absurdres, sfw, 1girl, solo, outdoors, night, 1.2::moonlight::, starry sky, rim lighting, backlighting, full body, front view, cinematic angle, depth of field, girl, bishoujo, 1.3::silver hair::, long hair, flowing hair, blue eyes, medium breasts, white skin, dress, white dress, long dress, elegant, standing, wind, hair flowing, looking at viewer, gentle smile, serene, falling leaves, light particles
    ```

    用户说"画月月在温泉里"，你应该生成：
    ```
    masterpiece, best quality, amazing quality, very aesthetic, absurdres, nsfw, 1girl, solo, outdoors, night, starry sky, 1.2::moonlight::, rim lighting, onsen, steam, rocks, hot spring, cowboy shot, from above, depth of field, girl, bishoujo, 1.3::silver hair::, high ponytail, crescent hair ornament, blue grey eyes, fox ears, white fox ears, fox tail, silver white tail, fluffy tail, medium breasts, white skin, nude, completely nude, partially submerged, wet body, wet hair, 1.2::shiny skin::, small triangular watermelon earrings, bathing, relaxing, arms on edge, looking at viewer, gentle smile, blush, nose blush, steam, water droplets, light particles, 0.8::falling leaves::
    ```

    Args:
        prompt: Danbooru 格式的英文 Tag 提示词（逗号分隔）。
                **必须是英文 Tag，不能是中文或自然语言！**
                Tag 数量必须 ≥ 70 个。
                按照 "质量Tag, 场景, 背景, 构图, 角色DNA, 动作, 表情" 顺序排列。
                使用权重语法调整核心元素: 1.2::Tag:: 增强, 0.8::Tag:: 减弱。

        negative_prompt: 负面提示词（可选），使用英文 Tag。
                留空则使用系统默认负面提示词。
                可用于排除不需要的元素，如: "background characters, fused bodies, bad anatomy"

        width: 图片宽度，默认 832。常用尺寸:
                - 竖图(人物): 832x1216
                - 横图(风景): 1216x832
                - 正方形: 1024x1024

        height: 图片高度，默认 1216。

        steps: 采样步数(1-28)，默认 28。步数越高细节越好但速度越慢。

        scale: 引导强度 CFG Scale(1.0-10.0)，默认 5.0。
                越高越贴近提示词但可能过饱和。

        sampler: 采样器，默认 "k_euler_ancestral"。可选:
                - "k_euler_ancestral" (推荐，效果最好)
                - "k_euler"
                - "k_dpmpp_2s_ancestral"
                - "k_dpmpp_2m"
                - "k_dpmpp_sde"
                - "ddim"

        seed: 随机种子（可选），留空则随机。指定种子可复现相同图片。

        preset_name: 画师串预设名称（可选）。
                如果用户提到使用某个预设，填入预设名称。
                若未填写，系统会自动尝试使用该用户最近保存的预设作为画师串前缀；
                若用户没有任何预设，则回退到全局默认画师串。

        skip_artist_prefix: 是否跳过画师串拼接，默认 False。
                设为 True 时不会在 prompt 前拼接任何画师串预设或全局默认画师串。
                仅当用户明确要求不使用画师串时才设为 True。

        preview_message: （必填）在图片生成前先发送给用户的预告消息。
                告诉用户你正在画图，例如："稍等一下，我来画~" 或 "让我想想怎么画..."

        success_message: （必填）图片生成成功后随图片一起发送的回复消息。
                这条消息会和图片+提示词一起显示，作为你对这次画图的完整回复。

    Returns:
        成功后图片、提示词和你的成功回复会一起发送给用户，不需要再额外回复。
        失败时你需要根据返回的提示信息告诉用户。
    """
    from src.chat.features.novelai_generation.services.novelai_service import novelai_service
    from src.chat.config.chat_config import NOVELAI_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    # 获取消息对象
    message: Optional[discord.Message] = kwargs.get("message")

    # 辅助函数：安全地添加/移除反应
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
    if not novelai_service.is_available():
        log.warning("NovelAI 服务不可用")
        return {
            "generation_failed": True,
            "reason": "service_unavailable",
            "hint": "NovelAI 图片生成服务当前不可用。请用自己的语气告诉用户这个功能暂时用不了，可以让管理员在 Dashboard 中配置。"
        }

    # 获取用户ID
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

    cost = NOVELAI_CONFIG.get("IMAGE_GENERATION_COST", 5)

    # 检查用户余额
    if parsed_user_id is not None and cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            return {
                "generation_failed": True,
                "reason": "insufficient_balance",
                "cost": cost,
                "balance": balance,
                "hint": f"用户月光币不足（需要{cost}，只有{balance}）。请用自己的语气告诉用户余额不够。"
            }

    # 画师串应用策略：
    # skip_artist_prefix=True 时跳过所有画师串拼接
    # 1) 指定 preset_name 时，优先按名称匹配该用户预设（支持大小写不敏感）
    # 2) 未指定 preset_name 时，自动使用该用户最近保存的预设（如存在）
    # 3) 若用户没有预设，回退到全局默认画师串
    final_prompt = prompt
    applied_artist = False
    effective_preset_name = preset_name

    if skip_artist_prefix:
        log.info("skip_artist_prefix=True, 跳过画师串拼接")
    else:
        user_presets = []
        if parsed_user_id is not None:
            try:
                user_presets = await chat_db_manager.get_novelai_presets(parsed_user_id)
            except Exception as e:
                log.warning(f"读取用户画师串预设失败: {e}")

        if user_presets:
            selected_preset = None

            # 优先使用用户显式指定的预设名
            if preset_name:
                requested_name = preset_name.strip()
                selected_preset = next(
                    (p for p in user_presets if p.get("name") == requested_name),
                    None,
                )
                if not selected_preset:
                    requested_name_lower = requested_name.lower()
                    selected_preset = next(
                        (
                            p
                            for p in user_presets
                            if str(p.get("name", "")).lower() == requested_name_lower
                        ),
                        None,
                    )
                    if selected_preset:
                        effective_preset_name = selected_preset.get("name")
                        log.info(
                            f"预设名大小写不一致，已匹配预设: {requested_name} -> {effective_preset_name}"
                        )

                if not selected_preset:
                    log.warning(
                        f"未找到用户预设 '{preset_name}'，将继续使用全局默认画师串（或无前缀）"
                    )

            # 未显式指定预设时，自动使用最近保存的预设
            if not selected_preset and not preset_name:
                selected_preset = user_presets[0]  # get_novelai_presets 已按 created_at DESC 排序
                effective_preset_name = selected_preset.get("name")
                if effective_preset_name:
                    log.info(f"未指定预设，自动应用用户最近画师串预设: {effective_preset_name}")

            # 应用选中的预设
            if selected_preset and selected_preset.get("artist_string"):
                artist_str = selected_preset["artist_string"]
                # 防重复：检查 prompt 中是否已包含画师串（AI 可能自己写了一遍）
                if not _prompt_already_contains_artist(prompt, artist_str):
                    final_prompt = f"{artist_str}, {prompt}"
                    applied_artist = True
                    log.info(f"应用画师串预设: {selected_preset.get('name', 'unknown')}")
                else:
                    applied_artist = True  # 标记为已应用，不再回退到全局默认
                    log.info(f"prompt 已包含画师串内容，跳过重复拼接")
                effective_preset_name = selected_preset.get("name") or effective_preset_name
                # 如果预设有负面提示词且用户未指定
                if not negative_prompt and selected_preset.get("negative_prompt"):
                    negative_prompt = selected_preset["negative_prompt"]

        # 如果没有通过用户预设应用画师串，则使用全局默认画师串
        if not applied_artist:
            default_artist = NOVELAI_CONFIG.get("DEFAULT_ARTIST_STRING", "")
            if default_artist:
                # 防重复：检查 prompt 中是否已包含全局画师串
                if not _prompt_already_contains_artist(prompt, default_artist):
                    final_prompt = f"{default_artist}, {prompt}"
                    log.info(f"应用全局默认画师串: {default_artist[:60]}...")
                else:
                    log.info(f"prompt 已包含全局画师串内容，跳过重复拼接")

    log.info(f"调用 NovelAI 图片生成工具，Tag: {final_prompt[:100]}..., 尺寸: {width}x{height}")

    # 添加"正在生成"反应
    await add_reaction("🎨")

    # 发送预告消息
    channel = kwargs.get("channel")
    if channel and preview_message:
        try:
            processed_message = replace_emojis(preview_message)
            await channel.send(processed_message)
            log.info(f"已发送 NovelAI 图片生成预告消息")
        except Exception as e:
            log.warning(f"发送预告消息失败: {e}")

    try:
        # 验证尺寸参数
        width = max(512, min(2048, width))
        height = max(512, min(2048, height))
        steps = max(1, min(50, steps))
        scale = max(1.0, min(10.0, scale))

        # 验证采样器
        valid_samplers = ["k_euler", "k_euler_ancestral", "k_dpmpp_2s_ancestral",
                          "k_dpmpp_2m", "k_dpmpp_sde", "ddim"]
        if sampler not in valid_samplers:
            sampler = "k_euler_ancestral"

        # 调用 NovelAI 服务
        result = await novelai_service.generate_image(
            prompt=final_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            steps=steps,
            scale=scale,
            sampler=sampler,
            seed=seed,
        )

        # 移除"正在生成"反应
        await remove_reaction("🎨")

        if result is not None:
            # 添加成功反应
            await add_reaction("✅")

            # 扣除月光币
            if parsed_user_id is not None and cost > 0:
                try:
                    await coin_service.remove_coins(
                        parsed_user_id, cost, f"NovelAI生图: {final_prompt[:25]}..."
                    )
                    log.info(f"用户 {parsed_user_id} NovelAI 生图成功，扣除 {cost} 月光币")
                except Exception as e:
                    log.error(f"扣除月光币失败: {e}")

            # 发送图片到频道
            if channel:
                try:
                    # 构建 Embed
                    embed = discord.Embed(
                        title="NovelAI 图片生成",
                        color=0x9B59B6,
                    )
                    # 设置请求者信息
                    request_user = kwargs.get("request_user")
                    author_user = request_user
                    if not author_user and message and hasattr(message, "author"):
                        author_user = message.author
                    if author_user:
                        author_name = getattr(author_user, "display_name", None) or getattr(author_user, "name", None)
                        author_avatar = getattr(author_user, "display_avatar", None)
                        author_icon_url = getattr(author_avatar, "url", None) if author_avatar else None
                        if author_name:
                            embed.set_author(name=author_name, icon_url=author_icon_url)

                    if success_message:
                        processed_success = replace_emojis(success_message)
                        embed.description = processed_success[:2048]

                    # 生成信息（紧凑排列）
                    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
                    if effective_preset_name:
                        embed.add_field(name="预设", value=effective_preset_name, inline=True)
                    embed.add_field(name="种子", value=str(result.seed), inline=True)
                    embed.add_field(
                        name="参数",
                        value=f"{result.width}x{result.height} | {steps}步 | CFG {scale}",
                        inline=True,
                    )
                    embed.set_footer(
                        text=f"消耗 {cost} 月光币 | {sampler} | {model_name}"
                    )

                    image_file = discord.File(
                        io.BytesIO(result.image_data),
                        filename="novelai_generated.png",
                        spoiler=True,
                    )
                    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

                    # 创建交互按钮 View
                    interaction_view = NovelAIResultView(
                        prompt=final_prompt,
                        negative_prompt=negative_prompt,
                        width=width,
                        height=height,
                        steps=steps,
                        scale=scale,
                        sampler=sampler,
                        preset_name=effective_preset_name,
                        user_id=user_id,
                        cost=cost,
                    )

                    sent_message = await channel.send(
                        embed=embed, file=image_file, view=interaction_view
                    )
                    if parsed_user_id is not None:
                        await chat_db_manager.register_generated_image_message(
                            message_id=sent_message.id,
                            user_id=parsed_user_id,
                            guild_id=sent_message.guild.id if sent_message.guild else None,
                            channel_id=sent_message.channel.id,
                        )
                    log.info("已发送 NovelAI 生成图片到频道（含交互按钮）")

                except Exception as e:
                    log.error(f"发送 NovelAI 图片到频道失败: {e}")

            return {
                "success": True,
                "skip_ai_response": True,
                "images_generated": 1,
                "cost": cost,
                "message": "NovelAI 图片已成功生成并发送给用户，无需再回复。"
            }
        else:
            # 生成失败
            await add_reaction("❌")
            log.warning(f"NovelAI 图片生成返回空结果。Tag: {final_prompt[:100]}")
            return {
                "generation_failed": True,
                "reason": "generation_failed",
                "hint": "NovelAI 图片生成失败了。请用自己的语气告诉用户稍后再试或换个描述试试。可能是 API Token 无效、Anlas 不足、或请求冲突。"
            }

    except Exception as e:
        await remove_reaction("🎨")
        await add_reaction("❌")
        log.error(f"NovelAI 图片生成异常: {e}", exc_info=True)
        return {
            "generation_failed": True,
            "reason": "exception",
            "hint": f"NovelAI 图片生成时出现错误: {str(e)[:100]}。请用自己的语气告诉用户出了问题。"
        }


# ==================== 交互按钮组件 ====================


class EditPromptModal(discord.ui.Modal, title="修改提示词"):
    """弹出 Modal 让用户编辑提示词后重新生成"""

    prompt_input = discord.ui.TextInput(
        label="正面提示词 (Danbooru Tag)",
        style=discord.TextStyle.paragraph,
        placeholder="masterpiece, best quality, 1girl, ...",
        required=True,
        max_length=4000,
    )
    negative_input = discord.ui.TextInput(
        label="负面提示词（可选）",
        style=discord.TextStyle.paragraph,
        placeholder="lowres, bad anatomy, ...",
        required=False,
        max_length=2000,
    )

    def __init__(
        self,
        current_prompt: str,
        current_negative: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
    ):
        super().__init__()
        self.prompt_input.default = current_prompt[:4000]
        if current_negative:
            self.negative_input.default = current_negative[:2000]
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            new_prompt = self.prompt_input.value.strip()
            new_negative = self.negative_input.value.strip() if self.negative_input.value else None

            if not new_prompt:
                await interaction.followup.send("提示词不能为空！", ephemeral=True)
                return

            await _regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=new_negative,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
            )
        except Exception as e:
            log.error(f"修改提示词重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class ToolAIRewriteModal(discord.ui.Modal, title="AI 重写提示词"):
    """对话工具生成结果 - AI 重写提示词弹窗"""

    description_input = discord.ui.TextInput(
        label="描述你想要的变化",
        style=discord.TextStyle.paragraph,
        placeholder="例如：换成夜晚的场景，加上星空和月亮\n或：改为更动感的姿势，添加战斗元素\n留空则自动优化当前提示词",
        required=False,
        max_length=1000,
    )

    def __init__(
        self,
        current_prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
    ):
        super().__init__()
        self._current_prompt = current_prompt
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True)
        try:
            description = self.description_input.value.strip() if self.description_input.value else "自动优化和增强提示词，使画面更精美细腻"

            # 调用 AI 重写 prompt
            from src.chat.services.gemini_service import gemini_service

            rewrite_prompt = get_rewrite_prompt(
                prompt=self._current_prompt,
                description=description,
            )
            new_tags = await gemini_service.generate_simple_response(
                prompt=rewrite_prompt,
                generation_config={
                    "temperature": 0.8,
                    "max_output_tokens": 2000,
                },
            )

            if not new_tags or not new_tags.strip():
                await interaction.followup.send("AI 重写失败，请稍后重试。", ephemeral=True)
                return

            new_prompt = new_tags.strip().strip('"').strip("'")
            log.info(f"对话工具 AI 重写 prompt 成功: {new_prompt[:100]}...")

            await _regenerate_novelai(
                interaction=interaction,
                prompt=new_prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
                title_suffix="（AI 重写）",
            )
        except Exception as e:
            log.error(f"对话工具 AI 重写 prompt 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"AI 重写失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass


class NovelAIResultView(discord.ui.View):
    """NovelAI 生成结果的交互按钮"""

    def __init__(
        self,
        prompt: str,
        negative_prompt: Optional[str],
        width: int,
        height: int,
        steps: int,
        scale: float,
        sampler: str,
        preset_name: Optional[str],
        user_id: Optional[str],
        cost: int,
    ):
        super().__init__(timeout=600)  # 10 分钟超时
        self._prompt = prompt
        self._negative_prompt = negative_prompt
        self._width = width
        self._height = height
        self._steps = steps
        self._scale = scale
        self._sampler = sampler
        self._preset_name = preset_name
        self._user_id = user_id
        self._cost = cost

    @discord.ui.button(label="重新生成", style=discord.ButtonStyle.primary, row=0)
    async def regenerate_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """用相同 prompt 但新种子重新生成"""
        # 权限检查：只有原始请求者或管理员可以操作
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            await _regenerate_novelai(
                interaction=interaction,
                prompt=self._prompt,
                negative_prompt=self._negative_prompt,
                width=self._width,
                height=self._height,
                steps=self._steps,
                scale=self._scale,
                sampler=self._sampler,
                preset_name=self._preset_name,
                user_id=self._user_id,
                cost=self._cost,
            )
        except Exception as e:
            log.error(f"重新生成失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"重新生成失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="修改提示词", style=discord.ButtonStyle.secondary, row=0)
    async def edit_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """弹出 Modal 让用户编辑提示词"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = EditPromptModal(
            current_prompt=self._prompt,
            current_negative=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="切换到 Imagen", style=discord.ButtonStyle.success, row=0)
    async def switch_to_imagen_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """使用 Gemini Imagen 重新生成"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        await interaction.response.defer(thinking=True)
        try:
            await _regenerate_with_imagen(
                interaction=interaction,
                prompt=self._prompt,
                user_id=self._user_id,
            )
        except Exception as e:
            log.error(f"切换到 Imagen 失败: {e}", exc_info=True)
            try:
                await interaction.followup.send(f"切换失败: {str(e)[:200]}", ephemeral=True)
            except Exception:
                pass

    @discord.ui.button(label="查看提示词", style=discord.ButtonStyle.secondary, emoji="📋", row=1)
    async def view_prompt_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """以 ephemeral 消息展示完整提示词"""
        prompt_text = self._prompt or "（无）"
        negative_text = self._negative_prompt or "（使用默认）"

        # Discord ephemeral 消息最多 2000 字符，做截断
        content_parts = [f"**正面提示词：**\n```\n{prompt_text[:900]}\n```"]
        if len(prompt_text) > 900:
            content_parts.append(f"```\n{prompt_text[900:1800]}\n```")
        content_parts.append(f"**负面提示词：**\n```\n{negative_text[:500]}\n```")

        content = "\n".join(content_parts)
        await interaction.response.send_message(content[:2000], ephemeral=True)

    @discord.ui.button(label="AI 重写", style=discord.ButtonStyle.secondary, row=1)
    async def ai_rewrite_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """让 AI 根据用户描述重新生成 prompt"""
        if self._user_id and str(interaction.user.id) != str(self._user_id):
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message("只有原始请求者才能操作哦~", ephemeral=True)
                return

        modal = ToolAIRewriteModal(
            current_prompt=self._prompt,
            negative_prompt=self._negative_prompt,
            width=self._width,
            height=self._height,
            steps=self._steps,
            scale=self._scale,
            sampler=self._sampler,
            preset_name=self._preset_name,
            user_id=self._user_id,
            cost=self._cost,
        )
        await interaction.response.send_modal(modal)

    async def on_timeout(self):
        """超时后禁用所有按钮"""
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        # 尝试编辑原消息来禁用按钮
        try:
            if self.message:
                await self.message.edit(view=self)
        except Exception:
            pass


async def _regenerate_novelai(
    interaction: discord.Interaction,
    prompt: str,
    negative_prompt: Optional[str],
    width: int,
    height: int,
    steps: int,
    scale: float,
    sampler: str,
    preset_name: Optional[str],
    user_id: Optional[str],
    cost: int,
    title_suffix: str = "（重新生成）",
):
    """内部函数：使用 NovelAI 重新生成图片"""
    from src.chat.features.novelai_generation.services.novelai_service import novelai_service
    from src.chat.config.chat_config import NOVELAI_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            parsed_user_id = None

    # 检查封禁与余额
    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            await interaction.followup.send(
                f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
                ephemeral=True,
            )
            return

    if parsed_user_id is not None and cost > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost:
            await interaction.followup.send(
                f"月光币不足（需要 {cost}，当前 {balance}）",
                ephemeral=True,
            )
            return

    # 生成图片（新种子）
    result = await novelai_service.generate_image(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        seed=None,  # 新种子
    )

    if result is None:
        await interaction.followup.send("NovelAI 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if parsed_user_id is not None and cost > 0:
        try:
            await coin_service.remove_coins(
                parsed_user_id, cost, f"NovelAI重新生图: {prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    # 构建 Embed
    embed = discord.Embed(
        title=f"NovelAI 图片生成{title_suffix}",
        color=0x9B59B6,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    # 生成信息（紧凑排列，提示词通过按钮查看）
    model_name = result.model or NOVELAI_CONFIG.get("MODEL", "unknown")
    if preset_name:
        embed.add_field(name="预设", value=preset_name, inline=True)
    embed.add_field(name="种子", value=str(result.seed), inline=True)
    embed.add_field(
        name="参数",
        value=f"{result.width}x{result.height} | {steps}步 | CFG {scale}",
        inline=True,
    )
    embed.set_footer(
        text=f"消耗 {cost} 月光币 | {sampler} | {model_name}"
    )

    image_file = discord.File(
        io.BytesIO(result.image_data),
        filename="novelai_generated.png",
        spoiler=True,
    )
    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

    # 创建新的交互按钮
    new_view = NovelAIResultView(
        prompt=prompt,
        negative_prompt=negative_prompt,
        width=width,
        height=height,
        steps=steps,
        scale=scale,
        sampler=sampler,
        preset_name=preset_name,
        user_id=user_id,
        cost=cost,
    )

    sent_message = await interaction.followup.send(
        embed=embed, file=image_file, view=new_view, wait=True
    )
    if parsed_user_id is not None and sent_message:
        await chat_db_manager.register_generated_image_message(
            message_id=sent_message.id,
            user_id=parsed_user_id,
            guild_id=sent_message.guild.id if sent_message.guild else None,
            channel_id=sent_message.channel.id,
        )
    log.info(f"NovelAI 重新生成成功, 种子: {result.seed}")


async def _regenerate_with_imagen(
    interaction: discord.Interaction,
    prompt: str,
    user_id: Optional[str],
):
    """内部函数：使用 Gemini Imagen 重新生成图片"""
    from src.chat.features.image_generation.services.gemini_imagen_service import gemini_imagen_service
    from src.chat.config.chat_config import GEMINI_IMAGEN_CONFIG
    from src.chat.features.odysseia_coin.service.coin_service import coin_service
    from src.chat.utils.database import chat_db_manager

    cost_per_image = GEMINI_IMAGEN_CONFIG.get("IMAGE_GENERATION_COST", 1)

    if not gemini_imagen_service.is_available():
        await interaction.followup.send("Gemini Imagen 服务当前不可用。", ephemeral=True)
        return

    parsed_user_id: Optional[int] = None
    if user_id:
        try:
            parsed_user_id = int(user_id)
        except (ValueError, TypeError):
            parsed_user_id = None

    if parsed_user_id is not None:
        ban_status = await chat_db_manager.get_image_generation_ban_status(parsed_user_id)
        if ban_status.get("is_banned"):
            remaining_text = ban_status.get("remaining_text", "未知时长")
            await interaction.followup.send(
                f"你的绘图功能当前已被临时禁用，剩余封禁时长：{remaining_text}",
                ephemeral=True,
            )
            return

    # 检查余额
    if parsed_user_id is not None and cost_per_image > 0:
        balance = await coin_service.get_balance(parsed_user_id)
        if balance < cost_per_image:
            await interaction.followup.send(
                f"月光币不足（需要 {cost_per_image}，当前 {balance}）",
                ephemeral=True,
            )
            return

    # NovelAI 的 Danbooru Tag prompt 不太适合 Imagen 的自然语言风格
    # 但我们仍然尝试用同样的 prompt 生成
    result = await gemini_imagen_service.generate_single_image(
        prompt=prompt,
        negative_prompt=None,
        aspect_ratio="3:4",  # 类似 832x1216 的竖图
        resolution="default",
        content_rating="nsfw",
    )

    if not result:
        await interaction.followup.send("Gemini Imagen 图片生成失败，请稍后重试。", ephemeral=True)
        return

    # 扣费
    if parsed_user_id is not None and cost_per_image > 0:
        try:
            await coin_service.remove_coins(
                parsed_user_id, cost_per_image, f"Imagen生图(切换): {prompt[:25]}..."
            )
        except Exception as e:
            log.error(f"扣除月光币失败: {e}")

    # 构建 Embed
    embed = discord.Embed(
        title="Gemini Imagen 图片生成（从 NovelAI 切换）",
        color=0x4285F4,
    )
    embed.set_author(
        name=interaction.user.display_name,
        icon_url=interaction.user.display_avatar.url if interaction.user.display_avatar else None,
    )
    embed.set_footer(text=f"消耗 {cost_per_image} 月光币 | 引擎: Gemini Imagen")

    # result 是图片 bytes
    image_file = discord.File(
        io.BytesIO(result),
        filename="imagen_generated.png",
        spoiler=True,
    )
    # 不使用 embed.set_image()，让 spoiler 遮罩正常生效

    sent_message = await interaction.followup.send(embed=embed, file=image_file, wait=True)
    if parsed_user_id is not None and sent_message:
        await chat_db_manager.register_generated_image_message(
            message_id=sent_message.id,
            user_id=parsed_user_id,
            guild_id=sent_message.guild.id if sent_message.guild else None,
            channel_id=sent_message.channel.id,
        )
    log.info("已切换到 Imagen 生成图片")
