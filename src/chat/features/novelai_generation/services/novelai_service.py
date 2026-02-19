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
            char_negative_captions = [
                {"char_caption": "", "centers": [{"x": 0.5, "y": 0.5}]}
                for _ in char_captions
            ]
            v4_negative_prompt = {
                "caption": {
                    "base_caption": final_negative,
                    "char_captions": char_negative_captions,
                },
                "legacy_uc": False,
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
            "skip_cfg_above_sigma": 58,  # Variety+ 开启
            "deliberate_euler_ancestral_bug": False,
            "prefer_brownian": True,
        }

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

        try:
            headers = {
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
                "Accept": "application/zip",
            }

            timeout = aiohttp.ClientTimeout(total=120)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    NOVELAI_API_URL,
                    json=request_body,
                    headers=headers,
                ) as response:
                    if response.status == 201:
                        # 成功: 返回 ZIP 文件
                        zip_data = await response.read()
                        image_data = self._extract_image_from_zip(zip_data)

                        if image_data:
                            log.info(f"NovelAI 图片生成成功, 大小: {len(image_data)} bytes")
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
                        return None
                    elif response.status == 402:
                        log.error("NovelAI API: Anlas 不足")
                        return None
                    elif response.status == 409:
                        log.error("NovelAI API: 并发请求冲突，请稍后重试")
                        return None
                    else:
                        error_text = await response.text()
                        log.error(
                            f"NovelAI API 请求失败: HTTP {response.status}, "
                            f"响应: {error_text[:500]}"
                        )
                        return None

        except aiohttp.ClientError as e:
            log.error(f"NovelAI API 网络错误: {e}")
            return None
        except Exception as e:
            log.error(f"NovelAI 图片生成异常: {e}", exc_info=True)
            return None

    def _split_prompt_for_v4(self, prompt: str):
        """
        将提示词拆分为 base_caption 和 char_captions，用于 V4 结构化 prompt。

        策略：
        - 识别角色相关 Tag (如 1girl, 1boy 等) 作为角色分离依据
        - 非角色相关的 Tag (场景、光影、质量等) 归入 base_caption
        - 角色外貌/服饰/动作等 Tag 归入 char_caption
        """
        tags = [t.strip() for t in prompt.split(",") if t.strip()]

        # 角色指示 Tag
        char_indicators = {
            "1girl", "2girls", "3girls", "4girls", "5girls", "6+girls",
            "1boy", "2boys", "3boys", "4boys", "5boys", "6+boys",
            "1other", "2others", "3others",
            "multiple girls", "multiple boys",
        }

        # 场景/质量/构图类 Tag（归入 base_caption）
        base_keywords = {
            "masterpiece", "best quality", "amazing quality", "very aesthetic",
            "absurdres", "highres", "ultra-detailed", "8k",
            "nsfw", "sfw", "rating:general", "rating:sensitive", "rating:questionable", "rating:explicit",
            "solo", "hetero", "harem", "yuri", "yaoi",
            "full body", "upper body", "lower body", "cowboy shot", "portrait",
            "close-up", "mid shot", "wide shot",
            "front view", "side view", "back view", "from below", "from above", "from behind", "pov",
            "face focus", "ass focus", "feet focus",
            "depth of field", "bokeh", "cinematic angle", "dutch angle",
            "indoors", "outdoors", "indoor", "outdoor",
            "day", "night", "sunset", "sunrise", "dawn", "dusk",
            "rain", "snow", "cloudy", "sunny",
            "backlighting", "rim lighting", "sidelighting", "dramatic shadows",
            "cinematic lighting", "soft lighting", "natural lighting",
            "simple background", "white background", "black background", "gradient background",
            "no background", "detailed background",
        }

        base_tags = []
        char_tags = []

        for tag in tags:
            tag_lower = tag.lower().strip()
            # 去除权重语法进行判断
            clean_tag = tag_lower
            if "::" in clean_tag:
                parts = clean_tag.split("::")
                if len(parts) >= 2:
                    clean_tag = parts[1].strip()

            if clean_tag in char_indicators:
                base_tags.append(tag)
                char_tags.append(tag)
            elif clean_tag in base_keywords or any(kw in clean_tag for kw in [
                "background", "lighting", "quality", "masterpiece", "aesthetic",
                "absurdres", "highres", "resolution", "detailed",
                "bokeh", "depth of field", "cinematic",
            ]):
                base_tags.append(tag)
            else:
                char_tags.append(tag)

        base_caption = ", ".join(base_tags) if base_tags else prompt
        char_caption = ", ".join(char_tags) if char_tags else ""

        char_captions = []
        if char_caption:
            char_captions.append({
                "char_caption": char_caption,
                "centers": [{"x": 0.5, "y": 0.5}],
            })
        else:
            char_captions.append({
                "char_caption": "",
                "centers": [{"x": 0.5, "y": 0.5}],
            })

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
        测试 API 连接（使用一个最小参数的请求测试，实际上不生成）。
        这里通过检查认证头是否有效来测试。
        """
        if not self._api_token:
            return {"success": False, "error": "API Token 未配置"}

        try:
            headers = {
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            }

            # 使用一个最小的请求来测试连接
            # NovelAI 没有专门的 ping 端点，所以我们尝试一个小请求
            # 使用最小参数和最小尺寸
            test_model = app_config.NOVELAI_CONFIG.get("MODEL", "nai-diffusion-4-5-full")
            test_params = {
                "params_version": 3,
                "width": 512,
                "height": 512,
                "scale": 5.0,
                "sampler": "k_euler",
                "steps": 1,
                "seed": 1,
                "n_samples": 1,
                "negative_prompt": "",
                "qualityToggle": False,
                "ucPreset": 0,
                "sm": False,
                "sm_dyn": False,
                "dynamic_thresholding": False,
                "controlnet_strength": 1,
                "legacy": False,
                "add_original_image": True,
                "uncond_scale": 1,
                "cfg_rescale": 0,
                "noise_schedule": "karras",
                "skip_cfg_above_sigma": None,
                "deliberate_euler_ancestral_bug": False,
                "prefer_brownian": True,
            }
            # 为 V4 模型添加结构化 prompt
            if "nai-diffusion-4" in test_model:
                test_params["v4_prompt"] = {
                    "caption": {
                        "base_caption": "test",
                        "char_captions": [{"char_caption": "", "centers": [{"x": 0.5, "y": 0.5}]}],
                    },
                    "use_coords": False,
                    "use_order": True,
                }
                test_params["v4_negative_prompt"] = {
                    "caption": {
                        "base_caption": "",
                        "char_captions": [{"char_caption": "", "centers": [{"x": 0.5, "y": 0.5}]}],
                    },
                    "legacy_uc": False,
                }
            test_body = {
                "input": "test",
                "model": test_model,
                "action": "generate",
                "parameters": test_params,
            }

            timeout = aiohttp.ClientTimeout(total=30)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    NOVELAI_API_URL,
                    json=test_body,
                    headers=headers,
                ) as response:
                    if response.status == 201:
                        return {"success": True, "message": "连接成功，Token 有效"}
                    elif response.status == 401:
                        return {"success": False, "error": "Token 无效或已过期"}
                    elif response.status == 402:
                        # 402 说明 token 有效但 Anlas 不足 - 连接本身是成功的
                        return {"success": True, "message": "Token 有效（Anlas 余额不足）"}
                    elif response.status == 409:
                        return {"success": True, "message": "Token 有效（请求冲突，稍后重试）"}
                    else:
                        error_text = await response.text()
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
