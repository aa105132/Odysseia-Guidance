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

        # 构建请求参数
        parameters: Dict[str, Any] = {
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
            "params_version": 3,
        }

        # 氛围转移（Vibe Transfer）
        if reference_image:
            parameters["reference_image"] = reference_image
            parameters["reference_strength"] = reference_strength
            parameters["reference_information_extracted"] = reference_info_extracted
            log.info(f"启用氛围转移: 强度={reference_strength}, 信息提取={reference_info_extracted}")

        # 构建请求体
        request_body = {
            "model": final_model,
            "action": "generate",
            "input": prompt,
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
            test_body = {
                "model": app_config.NOVELAI_CONFIG.get("MODEL", "nai-diffusion-4-5-full"),
                "action": "generate",
                "input": "test",
                "parameters": {
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
                    "params_version": 3,
                },
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
