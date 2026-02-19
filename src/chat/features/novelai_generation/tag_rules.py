# -*- coding: utf-8 -*-

"""
NovelAI Tag 生成规则库
基于「文生图+去八股（NAI完整版）」预设提取的完整规则和标签库。
供 AI 在生成 Tag 时参考使用。
"""

# ============================================================
# 完整的 Tag 生成规则 (用于对话工具 docstring 和 AI 重写)
# ============================================================

NOVELAI_TAG_RULES = (
    "## NovelAI Tag 生成核心规则\n\n"
    "### 基本要求\n"
    "- 使用英文 Danbooru 格式 Tag，逗号分隔\n"
    "- 单图 Tag 数量 >= 70 个\n"
    "- 禁止使用中文或自然语言句子\n"
    "- 定格画面：单图为同一时刻的静态瞬间，禁止连续动作过程\n\n"
    "### Tag 构成顺序 (按优先级)\n\n"
    "#### 1. Scene Composition 场景构成 (5~10%)\n"
    "- 场景类型: nsfw, sfw\n"
    "- 角色数量和性别: 1girl, 2boys, no humans\n"
    "- 角色关系: solo, hetero, harem\n\n"
    "#### 2. 背景 (10~20%)\n"
    "- 环境和背景: bedroom, park, alley, indoor, outdoor, coffee shop\n"
    "- 时间和天气: night, sunset, day, rain, golden hour\n"
    "- 氛围: mystical atmosphere, tense atmosphere\n"
    "- 光源和光影方向:\n"
    "  - 逆光: moonlight, backlighting, rim lighting\n"
    "  - 侧光: street light, sidelighting, dramatic shadows\n"
    "  - 顶/顺光: ceiling light, toplighting, cast shadows\n\n"
    "#### 3. 构图 (10~20%)\n"
    "- 区域: full body, upper body, lower body, cowboy shot\n"
    "- 远近: close-up, mid shot, wide shot, panorama\n"
    "- 透视: wide-angle, foreshortening, fisheye\n"
    "- 视角: front view, from behind, from above, from below, pov, male pov\n"
    "- 焦点: face focus, ass focus, breast focus, between legs\n"
    "- 角度: cinematic angle, dynamic angle, dutch angle\n"
    "- 其他: depth of field, bokeh, motion blur\n\n"
    "#### 4. Character Prompt 角色描述 (50~70%)\n\n"
    "**角色 DNA（身份）**:\n"
    "- 性别: girl, boy, other\n"
    "- 姓名: 同人角色用英文全名(来源)，原创角色中译英(original)\n"
    "- 身份: bishoujo, maid, loli, milf\n\n"
    "**角色 DNA（外貌）**:\n"
    "- 核心特征: 发长, 发色, 瞳色, 罩杯\n"
    "- 罩杯参考: flat chest(A), small breasts(B), medium breasts(C), large breasts(D), huge breasts(E), gigantic breasts(F+)\n"
    "- 修饰特征: white skin, makeup, scar, tan lines, bangs, petite\n\n"
    "**角色 DNA（服饰状态）**:\n"
    "- 核心服饰: 上装/下装/内衣/袜子/鞋类/配饰, 含风格/品类/颜色\n"
    "- 服饰材质: plaid, latex, satin, velvet, sheer fabric\n"
    "- 服饰状态: wet clothes, torn clothes, see-through\n"
    "- 穿着状态: nude, open shirt, strap slip, no panties\n"
    "- 裸露部位: pussy, ass, nipples\n\n"
    "**当前动作** (必须拆解动作要素):\n"
    "- 基础姿势: sitting, standing, lying, kneeling, all fours\n"
    "- 肢体动作: heart hands, head down, leg lift, v\n"
    "- 核心交互: walking, masturbation, hug, kiss\n"
    "- 物理反馈: bouncing breasts, ass ripple, skin indentation\n"
    "- 交互接触点: 明确动作主体+做什么+放在哪\n"
    "  - 自身: hands, grabbing ass, hands on own ass\n"
    "  - 别人: right hand, grabbing chest, hand on others' chest\n"
    "  - 物品: left hand, holding cup, reading\n\n"
    "**当前表情**:\n"
    "- 视线: looking at viewer, looking down, looking back\n"
    "- 眼: tears, wide-eyed, dilated pupils\n"
    "- 嘴: smile, open mouth, tongue out, clenched teeth\n"
    "- 感官: blush, ahegao, excited\n\n"
    "#### 5. Character UC 角色级负面 Tag\n"
    "- 路人屏蔽: background characters\n"
    "- 多角色屏蔽: fused bodies\n"
    "- 动态排除: 排除不需要的元素\n\n"
    "### 权重调整规则\n"
    "- 增强格式: 1.2::Tag:: (1.2倍增强)\n"
    "- 减弱格式: 0.8::Tag:: (0.8倍减弱)\n"
    "- 增强次数: 3~8 次\n"
    "- 减弱次数: 2~4 次\n"
    "- 增强优先级: 同人角色姓名 > 核心动作 > 服饰 > 特效 > 表情\n\n"
    "### 碎片化转译规则 (具象化，拒绝模糊)\n"
    "将复合概念拆解为多个具体 Tag:\n"
    "- 月下 -> moonlit, night, starry sky\n"
    "- 战斗 -> battle, standing, holding sword\n"
    "- 害羞 -> shy, blushing, looking down, fidgeting\n"
    "- '好热' -> sweating, fanning self, loosening clothes\n\n"
    "### 画面范围 (只保留视觉可见 Tag)\n"
    "- 构图导致不可见: 下身特写->排除上身元素\n"
    "- 衣物覆盖导致不可见: 穿戴整齐->排除内衣Tag\n"
    "- 遮盖导致不可见: 抱胸->hands, breast hold, covered nipples\n\n"
    "### POV 建议（1女+1男且男=user时）\n"
    "- 无互动: 1girl, solo\n"
    "- 对视/对话: 1girl, solo, looking at viewer\n"
    "- 物理接触: 1girl, 1boy, male pov, pov hands\n"
    "- 性行为: 1girl, male pov, pov hands, penis\n\n"
    "### 表现力/微细节 (必须额外添加)\n"
    "- 环境氛围: falling leaves, fireworks, steam, floating sakura\n"
    "- 生理反应: full-face blush, dilated pupils, drooling, heavy breathing, sweat\n"
    "- 动态互动: speed lines, motion lines, bouncing breasts, splashing fluids\n"
    "- 特效粒子: magical, ripple, glowing, light particles\n"
    "- 拟声词: sound effects\n"
)

# ============================================================
# 简化版标签库 (用于 prompt 模板中，减少 token 消耗)
# ============================================================

TAG_LIBRARY_COMPACT = (
    "Expressions: grin,smile,smug,seductive smile,glaring,pout,crying,sobbing,tears,"
    "surprised,flustered,blush,embarrassed,yawning,exhausted,parted lips,open mouth,"
    "tongue out,ahegao,heart eyes,fucked silly,rolling eyes\n"
    "Expression combos: ahegao+drooling+tears+rolling eyes(orgasm), "
    "open mouth+heavy breathing+blush(penetration), smile+blush+looking at viewer(gentle)\n"
    "Composition: whole body,upper body,close up,mid shot,wide shot,front view,from behind,"
    "from above,from below,pov,male pov,face focus,breast focus,ass focus,cinematic angle,"
    "dynamic angle,dutch angle,depth of field,bokeh\n"
    "Poses: standing,sitting,lying,kneeling,all fours,squatting,bent over,crawling,walking,"
    "running,jumping,contrapposto\n"
    "Arms: arm support,arms behind head,arms behind back,arms up,crossed arms,victory pose,"
    "outstretched arm\n"
    "Legs: crossed legs,spread legs,leg up,legs up,tiptoes,m legs,knees apart\n"
    "Hands: thumbs up,peace hand,heart hands,finger gun,fist,pillow hug\n"
    "Gaze: looking at viewer,looking down,looking up,looking back,looking away,sideways glance,"
    "eye contact\n"
    "Outfit states: nude,topless,bottomless,open shirt,no bra,no panties,strap slip,"
    "see-through,wet clothes,torn clothes,partially clothed\n"
    "Outfit openings: off-shoulder,bare shoulders,cleavage cutout,underboob cutout,"
    "center opening,navel cutout,back cutout,heart cutout\n"
    "Hair styles: long hair,short hair,ponytail,twintails,braid,bob cut,ahoge,bangs,"
    "sidelocks,wavy hair,straight hair,curly hair,drill hair,half updo\n"
    "Hair colors: black hair,blonde hair,brown hair,silver hair,white hair,red hair,"
    "blue hair,pink hair,purple hair,green hair,streaked hair\n"
    "Eye colors: blue eyes,red eyes,green eyes,brown eyes,purple eyes,yellow eyes,"
    "heterochromia,heart-shaped pupils\n"
    "Lighting: backlighting,rim lighting,sidelighting,dramatic shadows,moonlight,sunlight,"
    "neon light,golden hour\n"
    "Atmosphere: falling leaves,fireworks,steam,floating sakura,light particles,glowing,"
    "starry sky,rain\n"
    "Physiology: sweat,heavy breathing,steaming body,trembling,drooling,tears,blush,"
    "dilated pupils,nipple erection\n"
    "Dynamics: speed lines,motion lines,bouncing breasts,ass ripple,splashing fluids,"
    "sound effects\n"
    "Sex positions: doggystyle,missionary,cowgirl,reverse cowgirl,mating press,69,"
    "girl on top,sex from behind,piledriver,spitroast,suspended congress\n"
    "Sex acts: sex,vaginal,anal,oral,fellatio,handjob,footjob,paizuri,fingering,"
    "masturbation,deep penetration,cunnilingus\n"
    "Cum: cum,excessive cum,bukkake,facial,ejaculation,cumdrip,cum on body,"
    "internal cumshot,cum in mouth\n"
    "Breast sizes: flat chest(A),small breasts(B),medium breasts(C),large breasts(D),"
    "huge breasts(E),gigantic breasts(F+)\n"
    "Ages: loli,teenage,bishoujo,milf,mature female\n"
    "Locations indoor: bedroom,bathroom,kitchen,classroom,library,office,hotel room,"
    "locker room,living room\n"
    "Locations outdoor: beach,forest,park,mountain,city,alley,rooftop,garden,"
    "pool,shrine,playground\n"
    "Modifiers: makeup,eyeshadow,lipstick,freckles,scar,mole,tattoo,tan lines\n"
    "Two-person: holding hands,eye contact,cuddling,princess carry,spooning,headpat,"
    "sitting on lap,neck biting,face to face\n"
    "Weight syntax: 1.2::important_tag:: 1.3::very_important:: 0.8::subtle:: 0.7::bg_detail::"
)

# ============================================================
# AI 描述转 Tag 的提示词 (用于 /draw 面板的 AI 描述模式)
# ============================================================

AI_TAG_GENERATION_PROMPT_V2 = (
    "You are an expert NovelAI image generation tag creator. "
    "Convert the user's description into high-quality Danbooru-format English tags "
    "for NovelAI Diffusion.\n\n"
    "## STRICT RULES:\n"
    "1. Output ONLY comma-separated English tags, NO explanation, NO numbering\n"
    "2. Tag count MUST be >= 70\n"
    "3. Use weight syntax for emphasis: 1.2::Tag:: or 1.3::Tag:: (3~8 times), "
    "0.8::Tag:: for de-emphasis (2~4 times)\n"
    "4. Priority for weight: character name > core action > outfit > effects > expression\n"
    "5. Start with quality tags: masterpiece, best quality, amazing quality, very aesthetic, absurdres\n"
    "6. Decompose complex concepts into multiple specific tags\n"
    "7. Only include VISUALLY VISIBLE elements in the frame\n"
    "8. Add expressiveness details: atmosphere, physiological reactions, dynamic effects\n\n"
    "## TAG ORDER:\n"
    "1. Quality tags\n"
    "2. Scene type (nsfw/sfw) + character count (1girl, solo)\n"
    "3. Background: environment, time, weather, atmosphere, lighting\n"
    "4. Composition: framing, distance, perspective, angle, focus, depth of field\n"
    "5. Character DNA - Identity: gender, name, race, age\n"
    "6. Character DNA - Appearance: hair style/color, eye color, breast size, skin\n"
    "7. Character DNA - Outfit: clothing items, material, state, exposed parts\n"
    "8. Current Action: base pose, limb action, core interaction, contact points\n"
    "9. Current Expression: gaze, eyes, mouth, emotion\n"
    "10. Expressiveness: atmosphere effects, physiological reactions, dynamic effects, particles\n\n"
    "## REFERENCE TAG LIBRARY:\n"
    "{tag_library}\n\n"
    "## User description:\n"
    "{description}\n\n"
    "Tags:"
)

# ============================================================
# AI 重写 prompt 的提示词 (用于重写按钮)
# ============================================================

AI_REWRITE_PROMPT_V2 = (
    "You are an expert NovelAI image generation tag creator. "
    "Improve/rewrite the following prompt while keeping the same theme and subject.\n\n"
    "## STRICT RULES:\n"
    "1. Keep the same subject, characters, and general theme\n"
    "2. Improve quality, add more details, better composition\n"
    "3. Use Danbooru-style tags, comma-separated\n"
    "4. Start with quality tags: masterpiece, best quality, amazing quality, very aesthetic, absurdres\n"
    "5. Use weight syntax: 1.2::Tag:: for emphasis (3~8 times), 0.8::Tag:: for de-emphasis (2~4 times)\n"
    "6. Weight priority: character name > core action > outfit > effects > expression\n"
    "7. Output ONLY the improved comma-separated tags, no explanation\n"
    "8. Tag count MUST be >= 70\n"
    "9. Decompose complex concepts into specific tags\n"
    "10. Only include visually visible elements\n"
    "11. Add expressiveness: atmosphere, physiological reactions, dynamic effects, particles\n\n"
    "## TAG STRUCTURE ORDER:\n"
    "Quality -> Scene type -> Background -> Composition -> "
    "Character DNA (identity, appearance, outfit) -> Action -> Expression -> Expressiveness\n\n"
    "## REFERENCE TAG LIBRARY:\n"
    "{tag_library}\n\n"
    "Original prompt:\n"
    "{prompt}\n\n"
    "User's description of desired changes:\n"
    "{description}\n\n"
    "Improved tags:"
)


def get_tag_generation_prompt(description: str) -> str:
    """获取 AI 描述转 Tag 的完整提示词"""
    return AI_TAG_GENERATION_PROMPT_V2.format(
        tag_library=TAG_LIBRARY_COMPACT,
        description=description
    )


def get_rewrite_prompt(prompt: str, description: str) -> str:
    """获取 AI 重写 prompt 的完整提示词"""
    return AI_REWRITE_PROMPT_V2.format(
        tag_library=TAG_LIBRARY_COMPACT,
        prompt=prompt,
        description=description
    )
