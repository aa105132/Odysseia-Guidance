# -*- coding: utf-8 -*-

"""
NovelAI 图像生成服务
通过 NovelAI 官方 API 生成图片

API 端点: POST https://image.novelai.net/ai/generate-image
认证方式: Bearer Token (Persistent API Token)
响应格式: application/zip (包含 PNG 图片)
"""

import logging
import io
import zipfile
import random
import base64
import asyncio
import aiohttp
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

from src.chat.config import chat_config as app_config

log = logging.getLogger(__name__)

# NovelAI 官方 API 端点
NOVELAI_API_URL = "https://image.novelai.net/ai/generate-image"

# 支持的模型列表
AVAILABLE_MODELS = [
    "nai-diffusion-4-5-full",
    "nai-diffusion-4-5-curated",
    "nai-diffusion-4-curated-preview",
    "nai-diffusion-3",
]

# 支持的采样器列表
AVAILABLE_SAMPLERS = [
    "k_euler",
    "k_euler_ancestral",
    "k_dpmpp_2s_ancestral",
    "k_dpmpp_2m",
    "k_dpmpp_sde",
    "ddim",
]

# 常用尺寸预设 (宽x高)
SIZE_PRESETS = {
    "竖版人物 (832x1216)": (832, 1216),
    "横版风景 (1216x832)": (1216, 832),
    "正方形 (1024x1024)": (1024, 1024),
    "大竖版 (1024x1536)": (1024, 1536),
    "大横版 (1536x1024)": (1536, 1024),
    "手机壁纸 (768x1344)": (768, 1344),
    "宽屏壁纸 (1344x768)": (1344, 768),
}

# 噪声调度选项
NOISE_SCHEDULES = [
    "native",
    "karras",
    "exponential",
    "polyexponential",
]


@dataclass
class NovelAIResult:
    """NovelAI 图片生成结果"""
    image_data: bytes  # PNG 图片二进制数据
    seed: int = 0
    prompt: str = ""
    negative_prompt: str = ""
    width: int = 0
    height: int = 0
    model: str = ""


class NovelAIService:
    """
    NovelAI 图像生成服务类

    通过 NovelAI 官方 API 生成图片，支持文生图和氛围转移。
    """

    def __init__(self):
        self._api_token: Optional[str] = None
        # 请求队列: 同一时间只允许一个 NovelAI 请求，避免 429
        self._request_semaphore = asyncio.Semaphore(1)
        self._initialize()

    def _initialize(self):
        """初始化服务"""
        config = app_config.NOVELAI_CONFIG

        if not config.get("ENABLED"):
            log.info("NovelAI 图像生成服务未启用")
            return

        api_token = config.get("API_TOKEN", "")
        if not api_token:
            log.warning("NovelAI 服务缺少 API Token")
            return

        self._api_token = api_token
        log.info("NovelAI 图像生成服务已初始化")

    def is_available(self) -> bool:
        """检查服务是否可用"""
        return (
            self._api_token is not None
            and app_config.NOVELAI_CONFIG.get("ENABLED", False)
        )

    def reinitialize(self):
        """重新初始化服务"""
        self._api_token = None
        self._initialize()

    def update_config(self, **kwargs) -> Dict[str, Any]:
        """
        热更新配置并重新初始化服务。
        返回更新后的配置快照。
        """
        config = app_config.NOVELAI_CONFIG
        for key, value in kwargs.items():
            if key in config:
                config[key] = value
        self.reinitialize()
        return {
            "enabled": config.get("ENABLED"),
            "model": config.get("MODEL"),
            "available": self.is_available(),
        }

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
        model: Optional[str] = None,
        sampler: Optional[str] = None,
        steps: Optional[int] = None,
        scale: Optional[float] = None,
        seed: Optional[int] = None,
        cfg_rescale: Optional[float] = None,
        noise_schedule: Optional[str] = None,
        quality_toggle: Optional[bool] = None,
        uc_preset: Optional[int] = None,
        smea: Optional[bool] = None,
        smea_dyn: Optional[bool] = None,
        reference_image: Optional[str] = None,
        reference_strength: float = 0.6,
        reference_info_extracted: float = 1.0,
    ) -> Optional[NovelAIResult]:
        """
        生成图片

        Args:
            prompt: 正面提示词
            negative_prompt: 负面提示词（None 则使用默认）
            width: 图片宽度
            height: 图片高度
            model: 模型名称
            sampler: 采样器
            steps: 采样步数
            scale: 引导强度 (CFG Scale)
            seed: 随机种子
            cfg_rescale: CFG Rescale
            noise_schedule: 噪声调度
            quality_toggle: 质量切换
            uc_preset: UC 预设
            smea: SMEA
            smea_dyn: SMEA+DYN
            reference_image: base64 编码的参考图片（氛围转移）
            reference_strength: 参考图影响强度 (0-1)
            reference_info_extracted: 参考图信息提取程度 (0-1)

        Returns:
            成功返回 NovelAIResult，失败返回 None
        """
        if not self.is_available():
            log.error("NovelAI 服务不可用")
            return None

        config = app_config.NOVELAI_CONFIG

        # 使用传入值或默认配置
        final_model = model or config.get("MODEL", "nai-diffusion-4-5-full")
        final_width = width or config.get("DEFAULT_WIDTH", 832)
        final_height = height or config.get("DEFAULT_HEIGHT", 1216)
        final_steps = steps or config.get("DEFAULT_STEPS", 28)
        final_scale = scale if scale is not None else config.get("DEFAULT_SCALE", 5.0)
        final_sampler = sampler or config.get("DEFAULT_SAMPLER", "k_euler")
        final_seed = seed if seed is not None else random.randint(0, 2**32 - 1)
        final_cfg_rescale = cfg_rescale if cfg_rescale is not None else config.get("CFG_RESCALE", 0)
        final_noise_schedule = noise_schedule or config.get("NOISE_SCHEDULE", "native")
        final_quality_toggle = quality_toggle if quality_toggle is not None else config.get("QUALITY_TOGGLE", True)
        final_uc_preset = uc_preset if uc_preset is not None else config.get("UC_PRESET", 0)
        final_smea = smea if smea is not None else config.get("SMEA", False)
        final_smea_dyn = smea_dyn if smea_dyn is not None else config.get("SMEA_DYN", False)
        final_negative = negative_prompt if negative_prompt is not None else config.get("DEFAULT_NEGATIVE_PROMPT", "")

        # 判断是否为 V4/V4.5 模型
        is_v4_model = "nai-diffusion-4" in final_model

        # 构建 V4 结构化 prompt（V4/V4.5 必需）
        v4_prompt = None
        v4_negative_prompt = None

        if is_v4_model:
            # 分离 base_caption (场景/风格) 和 char_captions (角色描述)
            base_caption, char_captions = self._split_prompt_for_v4(prompt)

            v4_prompt = {
                "caption": {
                    "base_caption": base_caption,
                    "char_captions": char_captions,
                },
                "use_coords": False,
                "use_order": True,
            }

            # 构建 V4 负面提示词结构
            # 每个角色的 char_caption 也需要填入负面提示词，否则角色级负面约束完全无效
            # 使用通用的角色级负面提示词来防止畸形
            char_level_negative = "lowres, bad anatomy, bad hands, missing fingers, extra digit, fewer digits, extra arms, extra legs, malformed limbs, fused fingers, mutated hands, poorly drawn hands, poorly drawn face, mutation, deformed, ugly, blurry, amputation, bad proportions, gross proportions, long neck, cloned face, disfigured"
            char_negative_captions = [
                {"char_caption": char_level_negative, "centers": cap.get("centers", [{"x": 0.5, "y": 0.5}])}
                for cap in char_captions
            ]
            v4_negative_prompt = {
                "caption": {
                    "base_caption": final_negative,
                    "char_captions": char_negative_captions,
                },
                # legacy_uc=True 确保平面 negative_prompt 字符串也会生效（兜底）
                # 这样即使 V4 结构化路径有遗漏，传统负面提示词也能起作用
                "legacy_uc": True,
            }

        # 构建请求参数
        parameters: Dict[str, Any] = {
            "params_version": 3,
            "width": final_width,
            "height": final_height,
            "scale": final_scale,
            "sampler": final_sampler,
            "steps": final_steps,
            "seed": final_seed,
            "n_samples": 1,
            "negative_prompt": final_negative,
            "qualityToggle": final_quality_toggle,
            "ucPreset": final_uc_preset,
            "cfg_rescale": final_cfg_rescale,
            "noise_schedule": final_noise_schedule,
            "sm": final_smea,
            "sm_dyn": final_smea_dyn,
            # V4/V4.5 必需的额外参数
            "dynamic_thresholding": False,
            "controlnet_strength": 1,
            "legacy": False,
            "add_original_image": True,
            "uncond_scale": 1,
            "skip_cfg_above_sigma": 58,  # Variety+ (variety_boost) 开启
            "deliberate_euler_ancestral_bug": False,
            "prefer_brownian": True,
            # V4.5 推荐参数
            "use_coords": False,
        }

        # V4.5 模型专用参数
        if is_v4_model and "4-5" in final_model:
            # decrisp_mode: 减少 V4.5 偶尔出现的锐化伪影
            parameters["decrisp_mode"] = True
            # 确保 noise_schedule 使用 karras（V4.5 推荐）
            if final_noise_schedule == "native":
                parameters["noise_schedule"] = "karras"

        # 注入 V4 结构化 prompt
        if is_v4_model and v4_prompt and v4_negative_prompt:
            parameters["v4_prompt"] = v4_prompt
            parameters["v4_negative_prompt"] = v4_negative_prompt

        # 氛围转移（Vibe Transfer）
        if reference_image:
            parameters["reference_image"] = reference_image
            parameters["reference_strength"] = reference_strength
            parameters["reference_information_extracted"] = reference_info_extracted
            log.info(f"启用氛围转移: 强度={reference_strength}, 信息提取={reference_info_extracted}")

        # 构建请求体
        request_body = {
            "input": prompt,
            "model": final_model,
            "action": "generate",
            "parameters": parameters,
        }

        log.info(
            f"NovelAI 生成请求: 模型={final_model}, "
            f"尺寸={final_width}x{final_height}, 步数={final_steps}, "
            f"引导={final_scale}, 采样器={final_sampler}, 种子={final_seed}"
        )

        # 从配置读取最大重试次数
        max_retries = app_config.NOVELAI_CONFIG.get("MAX_RETRIES", 3)

        try:
            headers = {
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
                "Accept": "application/zip",
            }

            timeout = aiohttp.ClientTimeout(total=120)

            # 使用 semaphore 确保同一时间只有一个请求（队列功能）
            log.info("NovelAI 请求进入队列，等待获取许可...")
            async with self._request_semaphore:
                log.info("NovelAI 请求获取到许可，开始生成")

                last_error = None
                for attempt in range(1, max_retries + 1):
                    try:
                        async with aiohttp.ClientSession(timeout=timeout) as session:
                            async with session.post(
                                NOVELAI_API_URL,
                                json=request_body,
                                headers=headers,
                            ) as response:
                                if response.status in (200, 201):
                                    # 成功: 返回 ZIP 文件（官方文档为 201，但某些情况可能返回 200）
                                    zip_data = await response.read()
                                    image_data = self._extract_image_from_zip(zip_data)

                                    if image_data:
                                        log.info(f"NovelAI 图片生成成功 (第 {attempt} 次尝试), 大小: {len(image_data)} bytes")
                                        return NovelAIResult(
                                            image_data=image_data,
                                            seed=final_seed,
                                            prompt=prompt,
                                            negative_prompt=final_negative,
                                            width=final_width,
                                            height=final_height,
                                            model=final_model,
                                        )
                                    else:
                                        log.error("NovelAI 响应的 ZIP 文件中没有找到图片")
                                        return None

                                elif response.status == 401:
                                    log.error("NovelAI API 认证失败: Token 无效或过期")
                                    return None  # 不可重试
                                elif response.status == 402:
                                    log.error("NovelAI API: Anlas 不足")
                                    return None  # 不可重试
                                elif response.status in (429, 409):
                                    # 429 = 请求过多, 409 = 并发冲突 → 可重试
                                    wait_time = min(2 ** attempt, 30)  # 指数退避: 2s, 4s, 8s, 16s, 30s
                                    log.warning(
                                        f"NovelAI API 返回 {response.status} "
                                        f"(第 {attempt}/{max_retries} 次尝试), "
                                        f"等待 {wait_time}s 后重试..."
                                    )
                                    last_error = f"HTTP {response.status}"
                                    if attempt < max_retries:
                                        await asyncio.sleep(wait_time)
                                        continue
                                    else:
                                        log.error(
                                            f"NovelAI API: 达到最大重试次数 ({max_retries}), "
                                            f"最后一次状态码: {response.status}"
                                        )
                                        return None
                                else:
                                    # 其他错误
                                    try:
                                        error_bytes = await response.read()
                                        error_text = error_bytes.decode("utf-8", errors="replace")
                                    except Exception:
                                        error_text = f"(无法读取响应体, Content-Type: {response.content_type})"
                                    log.error(
                                        f"NovelAI API 请求失败: HTTP {response.status}, "
                                        f"响应: {error_text[:500]}"
                                    )
                                    return None  # 其他错误不重试

                    except aiohttp.ClientError as e:
                        # 网络错误也可以重试
                        wait_time = min(2 ** attempt, 30)
                        log.warning(
                            f"NovelAI API 网络错误 (第 {attempt}/{max_retries} 次尝试): {e}, "
                            f"等待 {wait_time}s 后重试..."
                        )
                        last_error = str(e)
                        if attempt < max_retries:
                            await asyncio.sleep(wait_time)
                            continue
                        else:
                            log.error(f"NovelAI API 网络错误，已达最大重试次数: {e}")
                            return None

                # 理论上不会走到这里，但安全起见
                log.error(f"NovelAI 请求失败, 最后错误: {last_error}")
                return None

        except Exception as e:
            log.error(f"NovelAI 图片生成异常: {e}", exc_info=True)
            return None

    def _split_prompt_for_v4(self, prompt: str):
        """
        将提示词拆分为 base_caption 和 char_captions，用于 V4 结构化 prompt。

        V4/V4.5 的结构化 prompt 核心逻辑：
        - base_caption: 场景、质量、构图、光影、背景、氛围等 **非角色** 相关内容
        - char_captions: 角色外貌、服饰、动作、表情等 **角色** 相关内容

        策略改进：
        - 采用"角色关键词白名单"识别角色 tag，而不是"场景黑名单兜底到角色"
        - 未能明确归类的 tag 优先归入 base_caption（比错误塞进角色更安全）
        - 保持画师串/artist tag 在 base_caption 中
        """
        tags = [t.strip() for t in prompt.split(",") if t.strip()]

        # ========= 角色指示 Tag（同时出现在 base 和 char 中）=========
        char_indicators = {
            "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls",
            "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys",
            "1other", "2others", "3others",
            "multiple girls", "multiple boys",
        }

        # ========= 明确属于 base_caption 的 tag 关键词 =========
        base_exact_keywords = {
            # 质量 / 风格
            "masterpiece", "best quality", "amazing quality", "very aesthetic",
            "absurdres", "highres", "ultra-detailed", "8k", "no text",
            "top quality", "official art", "fine art",
            # 内容分级
            "nsfw", "sfw", "rating:general", "rating:sensitive", "rating:questionable", "rating:explicit",
            # 人数/关系（场景级）
            "solo", "hetero", "harem", "yuri", "yaoi",
            # 构图
            "full body", "upper body", "lower body", "cowboy shot", "portrait",
            "close-up", "mid shot", "wide shot",
            "front view", "side view", "back view", "from below", "from above", "from behind",
            "pov", "male pov", "pov hands",
            "face focus", "ass focus", "feet focus", "breast focus", "crotch focus",
            "depth of field", "bokeh", "cinematic angle", "dutch angle", "dynamic angle",
            "wide-angle", "foreshortening", "fisheye",
            # 场景/环境
            "indoors", "outdoors", "indoor", "outdoor",
            "day", "night", "sunset", "sunrise", "dawn", "dusk", "golden hour",
            "rain", "snow", "cloudy", "sunny",
            # 背景
            "simple background", "white background", "black background", "gradient background",
            "no background", "detailed background",
            # 光影
            "backlighting", "rim lighting", "sidelighting", "dramatic shadows",
            "cinematic lighting", "soft lighting", "natural lighting",
            "moonlight", "sunlight", "neon light", "spotlight", "tyndall effect", "volumetric light",
            "dimly lit", "dark theme",
            # 氛围 / 特效（场景级）
            "falling leaves", "fireworks", "steam", "floating sakura", "light particles",
            "starry sky", "petals", "lens flare", "glowing",
            # 年代标签
            "year 2020", "year 2021", "year 2022", "year 2023", "year 2024", "year 2025",
        }

        # base 模糊匹配关键词（tag 中包含这些子串就归入 base）
        base_substring_keywords = [
            "background", "lighting", "quality", "masterpiece", "aesthetic",
            "absurdres", "highres", "resolution", "bokeh", "depth of field",
            "cinematic", "artist:", "year 20",
            # 场景地点
            "bedroom", "bathroom", "kitchen", "classroom", "library", "office",
            "hotel", "bar ", "elevator", "train interior", "car interior",
            "dungeon", "church", "beach", "forest", "park", "alley",
            "rooftop", "garden", "pool", "shrine", "street", "ruins",
            "onsen", "hot spring",
        ]

        # ========= 明确属于角色描述的 tag 关键词 =========
        char_exact_keywords = {
            # 发型/发色
            "long hair", "short hair", "ponytail", "high ponytail", "twintails",
            "braid", "bob cut", "ahoge", "bangs", "sidelocks", "wavy hair",
            "curly hair", "drill hair", "messy hair", "wet hair", "flowing hair",
            "black hair", "blonde hair", "brown hair", "silver hair", "white hair",
            "red hair", "blue hair", "pink hair", "purple hair", "green hair",
            "gradient hair",
            # 瞳色
            "blue eyes", "red eyes", "green eyes", "brown eyes", "purple eyes",
            "yellow eyes", "heterochromia", "heart-shaped pupils", "slit pupils",
            "grey eyes", "blue grey eyes",
            # 身材/身体
            "flat chest", "small breasts", "medium breasts", "large breasts",
            "huge breasts", "gigantic breasts", "petite", "curvy", "narrow waist",
            "wide hips", "thick thighs", "slender", "tall female", "short female",
            "dark skin", "pale skin", "tan", "tan lines",
            # 身份/角色类型
            "bishoujo", "maid", "loli", "milf", "office lady", "schoolgirl",
            "witch", "nurse", "policewoman", "bunny girl", "catgirl",
            "fox ears", "fox tail", "cat ears", "cat tail", "animal ears",
            # 表情
            "smile", "grin", "smug", "blush", "crying", "tears",
            "surprised", "flustered", "embarrassed", "pout", "expressionless",
            "ahegao", "heart eyes", "evil smile", "seductive smile",
            "open mouth", "tongue out", "clenched teeth", "parted lips", ":3",
            # 视线
            "looking at viewer", "looking down", "looking up", "looking back",
            "looking away", "sideways glance", "eye contact", "upturned eyes",
            # 姿势/动作
            "sitting", "standing", "lying", "kneeling", "all fours", "squatting",
            "bent over", "crawling", "walking", "running", "jumping",
            "arms up", "arms behind back", "arms behind head", "crossed arms",
            "peace sign", "v", "heart hands", "waving", "beckoning",
            "spread legs", "crossed legs", "leg up",
            "head tilt", "leaning forward", "contrapposto",
            # 服饰相关
            "nude", "completely nude", "topless", "bottomless",
            "open shirt", "no bra", "no panties", "see-through",
            "wet clothes", "torn clothes", "clothes lift", "skirt lift", "shirt lift",
            "school uniform", "sailor uniform", "maid outfit", "bikini", "swimsuit",
            "kimono", "yukata", "dress", "gothic lolita", "leotard", "bodysuit",
            "lingerie", "pajamas", "naked apron", "cheerleader",
            "choker", "collar", "thighhighs", "pantyhose", "high heels", "boots",
            "glasses", "earrings", "necklace",
        }

        # 角色 tag 模糊匹配关键词
        char_substring_keywords = [
            "hair", "eyes", "breasts", "nipple", "pussy", "penis", "ass",
            "skin", "navel", "thigh", "ear ", "ears", "tail",
            "panties", "bra", "skirt", "shirt", "dress", "uniform",
            "stockings", "socks", "shoes", "gloves", "hat", "ribbon",
            "ornament", "accessory", "jewelry", "tattoo", "piercing",
            "blush", "sweat", "drool",
            # 性行为相关（角色动作）
            "sex", "fellatio", "masturbation", "fingering", "handjob",
            "paizuri", "cum", "ejaculation", "orgasm",
            "bondage", "rope", "handcuffs", "leash",
            "grabbing", "holding", "hugging", "kissing",
        ]

        base_tags = []
        char_tags = []

        for tag in tags:
            tag_lower = tag.lower().strip()
            # 去除权重语法进行判断 (格式: n::Tag:: 或 n::Tag)
            clean_tag = tag_lower
            if "::" in clean_tag:
                parts = clean_tag.split("::")
                if len(parts) >= 2:
                    clean_tag = parts[1].strip()

            # 1) 角色数量指示 → 同时归入 base 和 char
            if clean_tag in char_indicators:
                base_tags.append(tag)
                char_tags.append(tag)
            # 2) 明确的 base 关键词
            elif clean_tag in base_exact_keywords:
                base_tags.append(tag)
            # 3) base 模糊匹配
            elif any(kw in clean_tag for kw in base_substring_keywords):
                base_tags.append(tag)
            # 4) 明确的角色关键词
            elif clean_tag in char_exact_keywords:
                char_tags.append(tag)
            # 5) 角色模糊匹配
            elif any(kw in clean_tag for kw in char_substring_keywords):
                char_tags.append(tag)
            # 6) 无法归类 → 优先归入 base（比误塞角色更安全，不会导致畸形）
            else:
                base_tags.append(tag)

        base_caption = ", ".join(base_tags) if base_tags else prompt
        char_caption = ", ".join(char_tags) if char_tags else ""

        char_captions = []
        if char_caption:
            char_captions.append({
                "char_caption": char_caption,
                "centers": [{"x": 0.5, "y": 0.5}],
            })
        else:
            # 即使没有明确的角色 tag，也要提供一个空的 char_caption 结构
            char_captions.append({
                "char_caption": "",
                "centers": [{"x": 0.5, "y": 0.5}],
            })

        log.debug(
            f"V4 prompt 拆分: base_caption={base_caption[:80]}... | "
            f"char_caption={char_caption[:80]}..."
        )

        return base_caption, char_captions

    def _extract_image_from_zip(self, zip_data: bytes) -> Optional[bytes]:
        """从 ZIP 响应中提取第一张 PNG 图片"""
        try:
            with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
                for file_name in zf.namelist():
                    if file_name.lower().endswith(('.png', '.webp', '.jpg', '.jpeg')):
                        return zf.read(file_name)
                # 如果没有匹配扩展名的文件，尝试读取第一个文件
                if zf.namelist():
                    return zf.read(zf.namelist()[0])
        except zipfile.BadZipFile:
            log.error("NovelAI 响应不是有效的 ZIP 文件")
        except Exception as e:
            log.error(f"解压 NovelAI 响应失败: {e}")
        return None

    async def test_connection(self) -> Dict[str, Any]:
        """
        测试 API 连接 - 使用 /user/subscription 端点验证 Token 有效性。
        不消耗 Anlas，快速验证认证是否有效。
        """
        if not self._api_token:
            return {"success": False, "error": "API Token 未配置"}

        try:
            headers = {
                "Authorization": f"Bearer {self._api_token}",
            }

            timeout = aiohttp.ClientTimeout(total=15)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                # 使用 /user/subscription 端点验证 token
                # 该端点返回用户订阅信息，不消耗任何资源
                async with session.get(
                    "https://api.novelai.net/user/subscription",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        try:
                            data = await response.json()
                            tier = data.get("tier", 0)
                            # tier: 0=free, 1=tablet, 2=scroll, 3=opus
                            tier_names = {0: "Free", 1: "Tablet", 2: "Scroll", 3: "Opus"}
                            tier_name = tier_names.get(tier, f"Tier {tier}")

                            # 获取 Anlas 信息
                            training_steps = data.get("trainingStepsLeft", {})
                            anlas = training_steps.get("fixedTrainingStepsLeft", 0) + training_steps.get("purchasedTrainingSteps", 0)

                            return {
                                "success": True,
                                "message": f"连接成功 | 订阅等级: {tier_name} | Anlas: {anlas}"
                            }
                        except Exception:
                            return {"success": True, "message": "连接成功，Token 有效"}

                    elif response.status == 401:
                        return {"success": False, "error": "Token 无效或已过期"}
                    else:
                        try:
                            error_bytes = await response.read()
                            error_text = error_bytes.decode("utf-8", errors="replace")
                        except Exception:
                            error_text = "未知错误"
                        return {
                            "success": False,
                            "error": f"HTTP {response.status}: {error_text[:200]}"
                        }

        except aiohttp.ClientError as e:
            return {"success": False, "error": f"网络错误: {str(e)}"}
        except Exception as e:
            return {"success": False, "error": f"未知错误: {str(e)}"}


# --- 单例实例 ---
novelai_service = NovelAIService()
