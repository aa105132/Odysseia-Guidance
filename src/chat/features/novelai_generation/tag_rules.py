# -*- coding: utf-8 -*-

"""
NovelAI Tag 生成规则库
基于「文生图+去八股（NAI完整版、无声效）-26.01.11版本」预设提取的完整规则和标签库。
供 AI 在对话生图和 /draw 面板中参考使用。
"""

# ============================================================
# 完整的 Tag 生成核心规则 (用于对话工具 docstring 和 AI 重写)
# ============================================================

NOVELAI_TAG_RULES = (
    "## NovelAI Tag 生成核心规则\n\n"
    "### 基本要求\n"
    "- 使用英文 Danbooru 格式 Tag，逗号分隔\n"
    "- 单图 Tag 数量 >= 90 个\n"
    "- 禁止使用中文或自然语言句子\n"
    "- 定格画面：单图为同一时刻的静态瞬间，禁止连续动作过程\n"
    "- 单图最多 4 个角色，最多 2 个女性角色\n\n"

    "### Tag 构成顺序 (按优先级)\n\n"

    "#### 1. Scene Composition 场景构成 (5~10%)\n"
    "- 场景类型: nsfw, sfw\n"
    "- 角色数量和性别: 1girl, 2boys, no humans\n"
    "- 角色关系: solo, hetero, harem, yuri, yaoi\n\n"

    "#### 2. Background 背景 (10~20%)\n"
    "- 年代/世界观: modern, medieval, sci-fi, fantasy\n"
    "- 环境: bedroom, park, alley, indoor, outdoor, coffee shop, onsen, train interior\n"
    "- 时间/天气: night, sunset, day, rain, golden hour, overcast\n"
    "- 氛围: mystical atmosphere, tense atmosphere, romantic mood\n"
    "- 光源和光影:\n"
    "  - 逆光: moonlight, backlighting, rim lighting\n"
    "  - 侧光: street light, sidelighting, dramatic shadows\n"
    "  - 顶/顺光: ceiling light, toplighting, cast shadows\n"
    "  - 特殊: neon light, spotlight, candlelight, tyndall effect\n\n"

    "#### 3. Composition 构图 (10~20%)\n"
    "- 区域: full body, upper body, lower body, cowboy shot\n"
    "- 远近: close-up, mid shot, wide shot, panorama\n"
    "- 透视: wide-angle, foreshortening, fisheye\n"
    "- 视角: front view, from behind, from above, from below, pov, male pov, from side\n"
    "- 焦点: face focus, ass focus, breast focus, between legs, foot focus\n"
    "- 角度: cinematic angle, dynamic angle, dutch angle\n"
    "- 其他: depth of field, bokeh, motion blur\n\n"

    "#### 4. Character Prompt 角色描述 (50~70%)\n\n"
    "**角色 DNA（身份）**:\n"
    "- 性别: girl, boy, other\n"
    "- 姓名: 同人角色用英文全名(来源)，原创角色用 original\n"
    "- 身份: bishoujo, maid, loli, milf, office lady, student\n\n"
    "**角色 DNA（外貌）**:\n"
    "- 核心特征: 发长/发型, 发色, 瞳色, 罩杯, 肤色\n"
    "- 罩杯参考: flat chest(A), small breasts(B), medium breasts(C), large breasts(D), huge breasts(E), gigantic breasts(F+)\n"
    "- 修饰特征: white skin, makeup, scar, tan lines, bangs, petite, curvy, narrow waist, wide hips\n\n"
    "**角色 DNA（服饰状态）**:\n"
    "- 核心服饰: 上装/下装/内衣/袜子/鞋类/配饰, 含风格/品类/颜色\n"
    "- 服饰材质: plaid, latex, satin, velvet, sheer fabric, lace\n"
    "- 服饰状态: wet clothes, torn clothes, see-through\n"
    "- 穿着状态: nude, open shirt, strap slip, no panties, clothes lift, skirt lift\n"
    "- 裸露部位: pussy, ass, nipples, navel\n\n"
    "**当前动作** (必须拆解动作要素):\n"
    "- 基础姿势: sitting, standing, lying, kneeling, all fours, squatting\n"
    "- 肢体动作: heart hands, head down, leg lift, v, arms up, peace sign\n"
    "- 核心交互: walking, masturbation, hug, kiss, sex, fellatio\n"
    "- 物理反馈: bouncing breasts, ass ripple, skin indentation\n"
    "- 交互接触点: 明确动作主体+做什么+放在哪\n"
    "  - 自身: hands on own ass, grabbing own breasts\n"
    "  - 别人: grabbing another's breasts, hand on another's head\n"
    "  - 物品: holding cup, holding phone, holding sword\n\n"
    "**当前表情**:\n"
    "- 视线: looking at viewer, looking down, looking back, looking away\n"
    "- 眼: tears, wide-eyed, dilated pupils, half-closed eyes, empty eyes\n"
    "- 嘴: smile, open mouth, tongue out, clenched teeth, :3, pout\n"
    "- 感官: blush, ahegao, excited, embarrassed, flustered\n\n"

    "#### 5. Character UC 角色级负面 Tag\n"
    "- 路人屏蔽: background characters\n"
    "- 多角色屏蔽: fused bodies\n"
    "- 动态排除: 排除不需要的元素\n\n"

    "### 多角色场景前缀\n"
    "- 当有多角色互动时，使用 Character N Prompt 格式分别描述\n"
    "- 交互 Tag 前缀:\n"
    "  - source#: 动作发起方 (如 source#grabbing another's breasts)\n"
    "  - target#: 动作接收方 (如 target#being grabbed)\n"
    "  - mutual#: 双方互动 (如 mutual#kissing)\n\n"

    "### 权重调整规则\n"
    "- 增强格式: 1.2::Tag:: (1.2倍增强) 或 1.3::Tag:: (1.3倍)\n"
    "- 减弱格式: 0.8::Tag:: (0.8倍减弱) 或 0.7::Tag::\n"
    "- 增强次数: 3~8 次\n"
    "- 减弱次数: 2~4 次\n"
    "- 增强优先级: 同人角色姓名 > 核心动作 > 服饰 > 特效 > 表情\n\n"

    "### 碎片化转译规则 (具象化，拒绝模糊)\n"
    "将复合概念拆解为多个具体 Tag:\n"
    "- 月下 -> moonlit, night, starry sky\n"
    "- 战斗 -> battle, standing, holding sword\n"
    "- 害羞 -> shy, blushing, looking down, fidgeting\n"
    "- '好热' -> sweating, fanning self, loosening clothes\n"
    "- 色情浴室 -> bathroom, steam, wet body, shiny skin, nude\n"
    "- 上课无聊 -> classroom, sitting, chin rest, looking away, yawning\n\n"

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
    "- 环境氛围: falling leaves, fireworks, steam, floating sakura, light particles\n"
    "- 生理反应: full-face blush, dilated pupils, drooling, heavy breathing, sweat, steaming body\n"
    "- 动态互动: speed lines, motion lines, bouncing breasts, splashing fluids, motion blur\n"
    "- 特效粒子: magical, ripple, glowing, light particles\n"
    "- 拟声词: sound effects\n\n"

    "### 男性配角默认 Tag (当需要男性角色但不是重点时)\n"
    "- 大几把路人男: 1boy, faceless male, muscular male, large penis, hetero\n"
    "- 黑人男: dark-skinned male, dark skin, muscular male\n"
    "- 正太: shota, age difference, size difference\n"
    "- 胖男: fat man, faceless male, ugly man\n"
)

# ============================================================
# 扩展版标签库 (用于 prompt 模板中)
# ============================================================

TAG_LIBRARY_COMPACT = (
    "=== Expressions 表情 ===\n"
    "grin,smile,smug,seductive smile,naughty face,glaring,pout,crying,sobbing,tears,"
    "surprised,flustered,blush,embarrassed,yawning,exhausted,parted lips,open mouth,"
    "tongue out,ahegao,heart eyes,heart-shaped pupils,fucked silly,rolling eyes,empty eyes,"
    "half-closed eyes,closed eyes,wavy mouth,clenched teeth,:3,:d,:o,:q,expressionless,"
    "disgust,shaded face,evil smile,light smile\n"

    "=== Expression Combos 表情组合 ===\n"
    "ahegao+drooling+tears+rolling eyes(高潮), "
    "open mouth+heavy breathing+blush(penetration), "
    "smile+blush+looking at viewer(gentle/温柔), "
    "clenched teeth+tears+blush(endurance/忍耐), "
    "tongue out+saliva+half-closed eyes(oral/口交), "
    "empty eyes+expressionless(mind break/精神崩坏)\n"

    "=== Composition 构图 ===\n"
    "full body,upper body,lower body,cowboy shot,close-up,mid shot,wide shot,"
    "front view,from behind,from above,from below,from side,pov,male pov,female pov,"
    "face focus,breast focus,ass focus,foot focus,crotch focus,"
    "cinematic angle,dynamic angle,dutch angle,depth of field,bokeh,"
    "foreshortening,fisheye,wide-angle,straight-on\n"

    "=== Poses 姿势 ===\n"
    "standing,sitting,lying,kneeling,all fours,squatting,bent over,crawling,"
    "walking,running,jumping,contrapposto,seiza,wariza,leaning forward,leaning back,"
    "on stomach,on back,on side,top-down bottom-up,dogeza,pigeon-toed\n"

    "=== Arms 手臂 ===\n"
    "arm support,arms behind head,arms behind back,arms up,crossed arms,victory pose,"
    "outstretched arm,arms at sides,hand on own chest,hand on own hip,"
    "hands on own head,waving,beckoning\n"

    "=== Legs 腿部 ===\n"
    "crossed legs,spread legs,leg up,legs up,tiptoes,m legs,knees apart,"
    "legs together,knees together feet apart,standing split,leg lock\n"

    "=== Hands 手部 ===\n"
    "thumbs up,peace sign,double peace sign,heart hands,finger gun,fist,"
    "v,double v,finger to mouth,shushing,holding phone,holding cup\n"

    "=== Gaze 视线 ===\n"
    "looking at viewer,looking down,looking up,looking back,looking away,"
    "sideways glance,eye contact,looking at another,looking at phone,"
    "upturned eyes,looking to the side\n"

    "=== Outfit States 衣物状态 ===\n"
    "nude,completely nude,topless,bottomless,open shirt,no bra,no panties,"
    "strap slip,see-through,wet clothes,torn clothes,partially clothed,"
    "clothed female nude male,naked shirt,naked apron,clothes lift,skirt lift,"
    "shirt lift,dress lift,panty pull,pantyhose pull,unzipped,unbuttoned\n"

    "=== Outfit Openings 衣物开口 ===\n"
    "off-shoulder,bare shoulders,cleavage cutout,underboob cutout,"
    "center opening,navel cutout,back cutout,heart cutout,sideless dress,"
    "crotchless,crotchless panties,nipple cutout\n"

    "=== Clothing Types 服饰类型 ===\n"
    "school uniform,sailor uniform,blazer,maid outfit,nurse,bikini,swimsuit,"
    "wedding dress,china dress,kimono,yukata,hanfu,qixiong ruqun,"
    "gothic lolita,cheerleader,bunny girl,santa costume,leotard,bodysuit,"
    "latex,lingerie,babydoll,garter belt,corset,apron,naked apron,"
    "bodystocking,gym uniform,track suit,pajamas,sundress,cocktail dress\n"

    "=== Accessories 配饰 ===\n"
    "choker,collar,animal collar,leash,thigh strap,garter straps,"
    "thighhighs,pantyhose,knee highs,ankle socks,high heels,boots,"
    "glasses,earrings,necklace,bracelet,hair ribbon,hair bow,"
    "hair ornament,headband,tiara,veil,mask,blindfold,ball gag\n"

    "=== Hair Styles 发型 ===\n"
    "long hair,very long hair,short hair,medium hair,ponytail,high ponytail,"
    "twintails,braid,twin braids,bob cut,ahoge,bangs,blunt bangs,"
    "sidelocks,wavy hair,straight hair,curly hair,drill hair,half updo,"
    "hair bun,messy hair,hair down,wet hair\n"

    "=== Hair Colors 发色 ===\n"
    "black hair,blonde hair,brown hair,silver hair,white hair,red hair,"
    "blue hair,pink hair,purple hair,green hair,streaked hair,gradient hair,"
    "multicolored hair\n"

    "=== Eye Colors 瞳色 ===\n"
    "blue eyes,red eyes,green eyes,brown eyes,purple eyes,yellow eyes,"
    "heterochromia,heart-shaped pupils,slit pupils,empty eyes,glowing eyes\n"

    "=== Lighting 光影 ===\n"
    "backlighting,rim lighting,sidelighting,dramatic shadows,moonlight,sunlight,"
    "neon light,golden hour,spotlight,candlelight,tyndall effect,volumetric light,"
    "light rays,ambient light,dimly lit,dark theme\n"

    "=== Atmosphere 氛围 ===\n"
    "falling leaves,fireworks,steam,floating sakura,light particles,glowing,"
    "starry sky,rain,snow,bubbles,petals,sparkle,lens flare,bokeh\n"

    "=== Physiology 生理反应 ===\n"
    "sweat,heavy breathing,steaming body,trembling,drooling,tears,blush,"
    "dilated pupils,nipple erection,goosebumps,full-face blush,nose blush,"
    "ear blush,body blush,flying sweatdrops,twitching,shiny skin,oiled skin,"
    "wet skin,wet body\n"

    "=== Dynamics 动态效果 ===\n"
    "speed lines,motion lines,motion blur,bouncing breasts,ass ripple,"
    "splashing fluids,sound effects,afterimage,impact,vibration\n"

    "=== Sex Positions 性爱体位 ===\n"
    "doggystyle,missionary,cowgirl position,reverse cowgirl,mating press,69,"
    "girl on top,sex from behind,piledriver,spitroast,suspended congress,"
    "standing sex,prone bone,face to face,sitting on lap,carrying,"
    "leg lock,upright straddle,reverse upright straddle\n"

    "=== Sex Acts 性行为 ===\n"
    "sex,vaginal,anal,oral,fellatio,deepthroat,irrumatio,"
    "handjob,footjob,paizuri,thigh sex,buttjob,fingering,"
    "masturbation,female masturbation,deep penetration,cunnilingus,"
    "double penetration,fisting,tailjob\n"

    "=== Cum 射精 ===\n"
    "cum,excessive cum,bukkake,facial,ejaculation,cumdrip,cum on body,"
    "internal cumshot,cum in mouth,cum in pussy,cum on breasts,cum on face,"
    "cum on hair,cum string,cum overflow,cum pool,cum on stomach,"
    "after ejaculation,used condom,cum on tongue\n"

    "=== BDSM/Bondage 束缚 ===\n"
    "bondage,rope bondage,shibari,handcuffs,shackles,chains,bound wrists,"
    "bound arms,bound legs,blindfold,ball gag,ring gag,collar,leash,"
    "pet play,slave,spanking,slap mark,whip,crotch rope,breast bondage,"
    "suspension,pillory\n"

    "=== Breast Sizes 罩杯 ===\n"
    "flat chest(A),small breasts(B),medium breasts(C),large breasts(D),"
    "huge breasts(E),gigantic breasts(F+),sagging breasts,hanging breasts,"
    "bouncing breasts,uneven breasts\n"

    "=== Ages/Types 年龄/类型 ===\n"
    "loli,teenage,bishoujo,milf,mature female,ojou-sama\n"

    "=== Locations Indoor 室内场景 ===\n"
    "bedroom,bathroom,kitchen,classroom,library,office,hotel room,"
    "locker room,living room,love hotel,bar,elevator,prison,church,"
    "train interior,car interior,dressing room,warehouse,dungeon\n"

    "=== Locations Outdoor 室外场景 ===\n"
    "beach,forest,park,mountain,city,alley,rooftop,garden,"
    "pool,shrine,playground,street,balcony,bridge,ruins\n"

    "=== Body Modifiers 身体修饰 ===\n"
    "makeup,eyeshadow,lipstick,freckles,scar,mole,tattoo,tan lines,"
    "body painting,pubic tattoo,barcode tattoo,nail polish,piercing,"
    "nipple piercing,navel piercing\n"

    "=== Two-person Interaction 双人互动 ===\n"
    "holding hands,eye contact,cuddling,princess carry,spooning,headpat,"
    "sitting on lap,neck biting,face to face,hug,hug from behind,"
    "breast press,grabbing another's breasts,grabbing another's ass,"
    "lifting person,carrying,piggyback,hand on another's head,"
    "stepping on,foot on face\n"

    "=== Exposure/Exhibitionism 露出 ===\n"
    "exhibitionism,public indecency,flashing,wardrobe malfunction,"
    "convenient censoring,nude towel,streaking\n"

    "=== Toys 玩具 ===\n"
    "vibrator,dildo,sex toy,egg vibrator,remote control vibrator,"
    "vibrator under clothes,vibrator under panties,anal beads,butt plug,"
    "dildo riding,object insertion,vaginal object insertion\n"

    "=== Special Effects 特殊效果 ===\n"
    "x-ray,cross section,stomach bulge,heart,spoken heart,speech bubble,"
    "thought bubble,??,!!,sound effects,2koma,fake screenshot,"
    "livestream,chat log\n"

    "=== Weight Syntax 权重语法 ===\n"
    "1.2::important_tag:: 1.3::very_important:: 0.8::subtle:: 0.7::bg_detail::\n"
)

# ============================================================
# 场景模板库 - 关键词触发的预制 Tag 模板
# 当用户描述匹配到特定场景时，AI 可以参考这些模板
# ============================================================

SCENARIO_TEMPLATES = {
    # === 性爱体位 ===
    "骑乘位/女上位": {
        "keywords": ["骑乘", "女上位", "cowgirl"],
        "templates": [
            "cowgirl position, girl on top, straddling, spread legs, sex, penis, hetero, sitting on person, motion lines",
            "reverse cowgirl, girl on top, from behind, ass, sex, looking back, hetero, sitting on person",
        ]
    },
    "后入/背后位": {
        "keywords": ["后入", "背后位", "doggy", "狗爬式"],
        "templates": [
            "doggystyle, sex from behind, all fours, ass, hetero, from behind, motion lines, grabbing hips",
            "prone bone, sex from behind, lying, on stomach, hetero, from above, deep penetration",
        ]
    },
    "正常位/传教士": {
        "keywords": ["正常位", "传教士", "missionary"],
        "templates": [
            "missionary, on back, lying, spread legs, hetero, sex, facing viewer, on bed",
            "mating press, legs up, folded, on back, deep penetration, hetero, from above",
        ]
    },
    "站立位": {
        "keywords": ["站立位", "standing sex"],
        "templates": [
            "standing sex, against wall, leg up, hetero, from side, standing, sex",
            "suspended congress, lifting person, face to face, carrying, standing, hetero, sex",
        ]
    },

    # === 口交相关 ===
    "口交/吮吸": {
        "keywords": ["口交", "舔", "吮吸", "fellatio", "blowjob"],
        "templates": [
            "fellatio, oral, kneeling, penis, hetero, pov, looking up, tongue out, saliva",
            "deepthroat, irrumatio, kneeling, head grab, tears, saliva trail, from side",
        ]
    },
    "舔舐": {
        "keywords": ["舔舐", "舔吊", "licking"],
        "templates": [
            "licking penis, penis on face, tongue out, penis grab, large penis, solo focus, pov",
            "testicle licking, testicles, penis, oral, from below, looking up",
        ]
    },

    # === 乳交/足交 ===
    "乳交": {
        "keywords": ["乳交", "paizuri", "夹胸"],
        "templates": [
            "paizuri, breasts, penis, between breasts, hetero, large breasts, pov, cum on breasts",
        ]
    },
    "足交": {
        "keywords": ["足交", "footjob", "脚"],
        "templates": [
            "footjob, feet, barefoot, penis, soles, toes, hetero, pov, cum on feet",
        ]
    },

    # === 日常场景 ===
    "洗澡/浴室": {
        "keywords": ["洗澡", "浴室", "沐浴", "泡澡"],
        "templates": [
            "bathroom, washing, steam, completely nude, wet, wet hair, shiny skin, standing, indoors",
            "bathtub, partially submerged, soap bubbles, relaxing, nude, steam, bathroom",
        ]
    },
    "温泉": {
        "keywords": ["温泉", "浴池", "hot spring"],
        "templates": [
            "onsen, partially submerged, steam, wet body, nude, outdoors, rocks, night, starry sky",
            "bath yukata, open kimono, bare shoulders, onsen, cleavage, standing, indoors",
        ]
    },
    "睡觉/起床": {
        "keywords": ["睡觉", "起床", "醒来", "sleeping"],
        "templates": [
            "lying, on bed, sleeping, peaceful, closed eyes, pillow, blanket, bedroom, night",
            "waking up, stretching, yawning, bed, morning, sunlight, messy hair, pajamas",
        ]
    },
    "做饭/厨房": {
        "keywords": ["做饭", "厨房", "cooking"],
        "templates": [
            "kitchen, cooking, apron, holding spatula, stove, indoors, steam, smile",
            "naked apron, cooking, kitchen, sideboob, from behind, looking back, indoors",
        ]
    },

    # === 服装主题 ===
    "女仆": {
        "keywords": ["女仆", "maid"],
        "templates": [
            "maid, maid outfit, maid headdress, apron, frills, white thighhighs, standing, indoors",
        ]
    },
    "JK/校服": {
        "keywords": ["JK", "校服", "学生", "school uniform"],
        "templates": [
            "school uniform, sailor uniform, pleated skirt, white shirt, bowtie, thighhighs, classroom",
            "school uniform, blazer, plaid skirt, collared shirt, necktie, loafers, outdoors",
        ]
    },
    "泳装/比基尼": {
        "keywords": ["泳装", "比基尼", "swimsuit", "bikini"],
        "templates": [
            "bikini, beach, ocean, sand, sunlight, standing, looking at viewer, wet, shiny skin",
            "school swimsuit, pool, wet, see-through, covered nipples, standing, indoors",
        ]
    },
    "和服/浴衣": {
        "keywords": ["和服", "浴衣", "kimono"],
        "templates": [
            "kimono, japanese clothes, floral print, obi, hair ornament, traditional, elegant",
            "yukata, summer, festival, fireworks, night, hair up, bare shoulders",
        ]
    },
    "汉服": {
        "keywords": ["汉服", "中国古装", "hanfu"],
        "templates": [
            "hanfu, chinese clothes, wide sleeves, hair stick, elegant, traditional, flowing fabric",
            "qixiong ruqun, see-through, ribbon, bare legs, chinese hairpin, indoor, traditional",
        ]
    },
    "婚纱": {
        "keywords": ["婚纱", "新娘", "wedding"],
        "templates": [
            "wedding dress, bridal veil, white dress, bouquet, elegant, church, smile, gloves",
        ]
    },
    "圣诞装": {
        "keywords": ["圣诞", "christmas", "santa"],
        "templates": [
            "santa costume, fur trim, red dress, christmas, santa hat, bell, red gloves, smile",
            "naked ribbon, red ribbon, santa hat, nude, christmas, skindentation",
        ]
    },
    "兔女郎": {
        "keywords": ["兔女郎", "bunny girl"],
        "templates": [
            "bunny girl, playboy bunny, bunny ears, leotard, pantyhose, rabbit tail, high heels, wrist cuffs",
        ]
    },

    # === 特殊场景 ===
    "绳缚/BDSM": {
        "keywords": ["绳缚", "捆绑", "BDSM", "束缚", "shibari"],
        "templates": [
            "bondage, rope bondage, shibari, bound wrists, bound arms, nude, breast bondage, tears, kneeling",
            "handcuffs, bound wrists, blindfold, ball gag, collar, leash, kneeling, submissive",
        ]
    },
    "触手": {
        "keywords": ["触手", "tentacle"],
        "templates": [
            "tentacles, tentacle sex, restrained, spread legs, suspended, nude, tears, ahegao",
            "tentacle clothes, living clothes, parasite, see-through, cowboy shot, blush",
        ]
    },
    "露出": {
        "keywords": ["露出", "暴露", "exhibitionism"],
        "templates": [
            "exhibitionism, public indecency, outdoors, crowd, nude, embarrassed, blush, tears",
            "flashing, skirt lift, no panties, public, outdoors, looking at viewer, blush",
        ]
    },

    # === 情感场景 ===
    "亲吻": {
        "keywords": ["亲吻", "接吻", "kiss"],
        "templates": [
            "kiss, french kiss, tongue, saliva trail, eyes closed, face to face, blush, intimate, hetero",
            "forehead kiss, gentle, closed eyes, hand on cheek, romantic, soft lighting",
        ]
    },
    "拥抱": {
        "keywords": ["拥抱", "抱", "hug"],
        "templates": [
            "hug, embrace, face to face, breast press, arms around another, romantic, blush, eyes closed",
            "hug from behind, arms around waist, chin on shoulder, gentle, indoors, couple",
        ]
    },

    # === 事后 ===
    "事后": {
        "keywords": ["事后", "after sex", "余韵"],
        "templates": [
            "after sex, lying, on bed, cum on body, exhausted, blush, messy hair, nude, stained sheets",
            "after sex, cum in pussy, cumdrip, on back, spread legs, heavy breathing, steaming body",
        ]
    },

    # === 自慰 ===
    "自慰": {
        "keywords": ["自慰", "masturbation"],
        "templates": [
            "female masturbation, fingering, spread legs, sitting, blush, heavy breathing, solo, nude",
            "dildo, object insertion, female masturbation, lying, on bed, solo, blush, half-closed eyes",
        ]
    },

    # === 环境场景 ===
    "酒吧": {
        "keywords": ["酒吧", "bar", "喝酒"],
        "templates": [
            "bar (place), sitting, cocktail, dim lighting, neon light, looking at viewer, elegant, indoors",
            "drunk, half-closed eyes, blush, bar, leaning on counter, revealing clothes, night",
        ]
    },
    "办公室": {
        "keywords": ["办公室", "office", "OL"],
        "templates": [
            "office, office lady, pencil skirt, white shirt, sitting, desk, computer, indoors",
            "office, desk, swivel chair, collared shirt, necktie, legs crossed, professional",
        ]
    },
    "教室": {
        "keywords": ["教室", "classroom"],
        "templates": [
            "classroom, desk, chalkboard, school uniform, sitting, indoors, daytime, window",
        ]
    },
    "电车/地铁": {
        "keywords": ["电车", "地铁", "train"],
        "templates": [
            "train interior, standing, hand grip, school uniform, crowded, window, solo focus",
        ]
    },
    "直播": {
        "keywords": ["直播", "vtuber", "livestream"],
        "templates": [
            "livestream, chat log, fake screenshot, sitting, facing camera, gaming chair, indoors, webcam",
        ]
    },

    # === 性玩具 ===
    "振动器/跳蛋": {
        "keywords": ["振动器", "跳蛋", "vibrator"],
        "templates": [
            "vibrator, vibrator under panties, trembling, blush, heavy breathing, standing, public",
            "remote control vibrator, vibrator cord, thigh strap, trembling, steaming body, public",
        ]
    },
    "假阳具": {
        "keywords": ["假阳具", "假鸡巴", "dildo"],
        "templates": [
            "dildo, object insertion, female masturbation, spread legs, sitting, nude, blush",
            "dildo riding, sex toy, huge dildo, squatting, spread legs, pussy juice, solo",
        ]
    },

    # === 特殊玩法 ===
    "催眠": {
        "keywords": ["催眠", "hypnosis", "精神控制"],
        "templates": [
            "hypnosis, mind control, empty eyes, @_@, spiral background, expressionless",
        ]
    },
    "宠物调教": {
        "keywords": ["宠物", "母狗", "pet play"],
        "templates": [
            "pet play, collar, leash, all fours, nude, fake animal ears, tail, viewer holding leash",
        ]
    },

    # === 杂志/封面 ===
    "杂志封面": {
        "keywords": ["杂志封面", "DVD封面", "cover"],
        "templates": [
            "magazine cover, fake cover, smile, looking at viewer, text, pose, professional lighting",
        ]
    },
    "照片": {
        "keywords": ["照片", "photo", "拍照"],
        "templates": [
            "photograph, polaroid, slightly yellowed paper, snapshot, casual, candid",
        ]
    },

    # === 人体彩绘 ===
    "人体彩绘": {
        "keywords": ["人体彩绘", "body painting"],
        "templates": [
            "bodypaint, nude, body painting, covered nipples, colorful, artistic, standing",
        ]
    },

    # === 钢管舞 ===
    "钢管舞": {
        "keywords": ["钢管舞", "pole dancing"],
        "templates": [
            "pole dancing, stripper pole, holding pole, armpits, navel, revealing clothes, spotlight, sweat",
        ]
    },
}


def get_scenario_hint(user_text: str) -> str:
    """
    根据用户描述文本匹配场景模板，返回可参考的 Tag 提示。
    如果匹配到多个场景，会合并返回。
    """
    matched = []
    user_lower = user_text.lower()

    for name, data in SCENARIO_TEMPLATES.items():
        for keyword in data["keywords"]:
            if keyword.lower() in user_lower:
                templates_str = "\n".join(f"  - {t}" for t in data["templates"])
                matched.append(f"【{name}】参考模板:\n{templates_str}")
                break

    if matched:
        return "\n\n".join(matched)
    return ""


# ============================================================
# AI 描述转 Tag 的提示词 (用于 /draw 面板的 AI 描述模式)
# ============================================================

AI_TAG_GENERATION_PROMPT_V2 = (
    "You are an expert NovelAI image generation tag creator. "
    "Convert the user's description into high-quality Danbooru-format English tags "
    "for NovelAI Diffusion.\n\n"
    "## STRICT RULES:\n"
    "1. Output ONLY comma-separated English tags, NO explanation, NO numbering\n"
    "2. Tag count MUST be >= 90\n"
    "3. Use weight syntax for emphasis: 1.2::Tag:: or 1.3::Tag:: (3~8 times), "
    "0.8::Tag:: for de-emphasis (2~4 times)\n"
    "4. Priority for weight: character name > core action > outfit > effects > expression\n"
    "5. Start with quality tags: masterpiece, best quality, amazing quality, very aesthetic, absurdres\n"
    "6. Decompose complex concepts into multiple specific tags\n"
    "7. Only include VISUALLY VISIBLE elements in the frame\n"
    "8. Add expressiveness details: atmosphere, physiological reactions, dynamic effects\n"
    "9. Every action must specify: WHO does WHAT to WHERE\n"
    "10. Single frame = one static moment, NO sequential actions\n\n"
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
    "{scenario_hint}"
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
    "8. Tag count MUST be >= 90\n"
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
    """获取 AI 描述转 Tag 的完整提示词（含场景模板匹配）"""
    scenario_hint = get_scenario_hint(description)
    scenario_section = ""
    if scenario_hint:
        scenario_section = f"## MATCHED SCENARIO TEMPLATES (use as reference):\n{scenario_hint}\n\n"

    return AI_TAG_GENERATION_PROMPT_V2.format(
        tag_library=TAG_LIBRARY_COMPACT,
        scenario_hint=scenario_section,
        description=description,
    )


def get_rewrite_prompt(prompt: str, description: str) -> str:
    """获取 AI 重写 prompt 的完整提示词"""
    return AI_REWRITE_PROMPT_V2.format(
        tag_library=TAG_LIBRARY_COMPACT,
        prompt=prompt,
        description=description,
    )
