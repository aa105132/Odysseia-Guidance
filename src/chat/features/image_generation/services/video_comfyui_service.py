# -*- coding: utf-8 -*-
"""
ComfyUI 视频生成服务（独立于画图服务）
使用 Wan 2.2 Bernini-R + LightX2V 4-step LoRA 工作流生成视频。
Workspace: bufan.live/krea-2（独立 CNB workspace，按需启停）
"""

import asyncio
import json
import logging
import os
import random
import urllib.parse
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp

from src.chat.config.chat_config import COMFYUI_VIDEO_CONFIG

log = logging.getLogger(__name__)

# ===== 工作流模板（API 格式）=====
# 3 种模式: t2v(文生视频) / i2v(图生视频) / r2v(参考图生视频)
# 变量: %prompt% %negative_prompt% %image0% %image1%
# 运行时由 _build_workflow() 替换

# 公共节点（所有模式共用）
_COMMON_NODES = {
    "5": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "wan2.2_bernini_r_high_noise_int8_convrot.safetensors",
            "weight_dtype": "default"
        }
    },
    "12": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": "wan2.2_bernini_r_low_noise_int8_convrot.safetensors",
            "weight_dtype": "default"
        }
    },
    "35": {
        "class_type": "CLIPLoader",
        "inputs": {
            "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
            "type": "wan"
        }
    },
    "7": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": "Wan2_1_VAE_bf16.safetensors"
        }
    },
    "13": {
        "class_type": "PathchSageAttentionKJ",
        "inputs": {
            "model": ["5", 0],
            "sage_attention": "auto",
            "allow_compile": False
        }
    },
    "14": {
        "class_type": "PathchSageAttentionKJ",
        "inputs": {
            "model": ["12", 0],
            "sage_attention": "auto",
            "allow_compile": False
        }
    },
    "11": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["13", 0],
            "lora_name": "Bernini-R_LightX2V_high_noise.safetensors",
            "strength_model": 1.0
        }
    },
    "29": {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["14", 0],
            "lora_name": "Bernini-R_LightX2V_high_noise.safetensors",
            "strength_model": 1.0
        }
    },
    "36": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["11", 0],
            "positive": ["38", 0],
            "negative": ["38", 1],
            "latent_image": ["38", 2],
            "add_noise": "enable",
            "noise_seed": 0,
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 0,
            "end_at_step": 2,
            "return_with_leftover_noise": "enable"
        }
    },
    "37": {
        "class_type": "KSamplerAdvanced",
        "inputs": {
            "model": ["29", 0],
            "positive": ["38", 0],
            "negative": ["38", 1],
            "latent_image": ["36", 0],
            "add_noise": "disable",
            "noise_seed": 0,
            "steps": 4,
            "cfg": 1.0,
            "sampler_name": "euler",
            "scheduler": "simple",
            "start_at_step": 2,
            "end_at_step": 4,
            "return_with_leftover_noise": "disable"
        }
    },
    "16": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["37", 0],
            "vae": ["7", 0]
        }
    },
    "26": {
        "class_type": "VHS_VideoCombine",
        "inputs": {
            "images": ["16", 0],
            "frame_rate": 32,
            "loop_count": 0,
            "filename_prefix": "Wan22_Bernini",
            "format": "video/h264-mp4",
            "pix_format": "yuv420p",
            "crf": 19,
            "pingpong": False, "save_metadata": True,
            "save_output": True
        }
    },
    # easy cleanGpuUsed removed (plugin not installed)
}


def _make_bernini_node(task_type: str, extra_inputs: dict = None) -> dict:
    """创建 BerniniStudio 节点"""
    inputs = {
        "clip": ["35", 0],
        "vae": ["7", 0],
        "width": 832,
        "height": 480,
        "length": 81,
        "batch_size": 1,
        "task_type": task_type,
        "prompt": "%prompt%",
        "negative_prompt": "%negative_prompt%",
        "use_default_neg": True,
        
    }
    if extra_inputs:
        inputs.update(extra_inputs)
    return {"class_type": "BerniniStudio", "inputs": inputs}


# T2V: 纯文生视频（无参考图）
_WORKFLOW_T2V = dict(_COMMON_NODES)
_WORKFLOW_T2V["38"] = _make_bernini_node("t2v")

# I2V: 图生视频（1张参考图）
_WORKFLOW_I2V = dict(_COMMON_NODES)
_WORKFLOW_I2V["43"] = {
    "class_type": "LoadImage",
    "inputs": {"image": "%image0%"}
}
_WORKFLOW_I2V["38"] = _make_bernini_node("i2v", {"image0": ["43", 0]})

# R2V: 参考图生视频（1张参考图, image1按需添加）
_WORKFLOW_R2V = dict(_COMMON_NODES)
_WORKFLOW_R2V["43"] = {
    "class_type": "LoadImage",
    "inputs": {"image": "%image0%"}
}
_WORKFLOW_R2V["38"] = _make_bernini_node("r2v", {
    "image0": ["43", 0],
})

# 模板映射
_WORKFLOW_TEMPLATES = {
    "t2v": _WORKFLOW_T2V,
    "i2v": _WORKFLOW_I2V,
    "r2v": _WORKFLOW_R2V,
}


class VideoComfyUIService:
    """ComfyUI 视频生成服务（独立于画图服务）"""

    _CNB_API_BASE = "https://api.cnb.cool"
    _CNB_REPO = "bufan.live/krea-2"

    def __init__(self):
        config = COMFYUI_VIDEO_CONFIG
        self.server_address = str(config.get("SERVER_ADDRESS", "")).strip()
        self._CNB_TOKEN = str(config.get("CNB_TOKEN", "")).strip()
        self._workflow_templates = None
        self._request_semaphore = asyncio.Semaphore(1)
        self._load_workflow()

    def _load_workflow(self) -> None:
        """加载工作流模板（3 种模式: t2v/i2v/r2v）"""
        # 优先从文件目录加载（WORKFLOW_DIR 配置项）
        workflow_dir = str(COMFYUI_VIDEO_CONFIG.get("WORKFLOW_DIR", "")).strip()
        if workflow_dir and Path(workflow_dir).is_dir():
            try:
                templates = {}
                for mode in ("t2v", "i2v", "r2v"):
                    p = Path(workflow_dir) / f"bernini_{mode}_api.json"
                    if p.exists():
                        with open(p, "r", encoding="utf-8") as f:
                            templates[mode] = json.load(f)
                if len(templates) == 3:
                    self._workflow_templates = templates
                    log.info(f"VideoComfyUI: 3 个工作流模板已从目录加载: {workflow_dir}")
                    return
                log.warning(f"VideoComfyUI: 目录 {workflow_dir} 只找到 {len(templates)} 个模板，补充嵌入式")
            except Exception as e:
                log.warning(f"VideoComfyUI: 工作流目录加载失败: {e}, 使用嵌入式模板")

        # 用嵌入式模板
        self._workflow_templates = {
            mode: json.loads(json.dumps(tpl))
            for mode, tpl in _WORKFLOW_TEMPLATES.items()
        }
        log.info("VideoComfyUI: 使用嵌入式工作流模板 (t2v/i2v/r2v)")

    @property
    def prompt_url(self) -> str:
        return f"{self.server_address}/prompt"

    @property
    def view_url(self) -> str:
        return f"{self.server_address}/view"

    def is_enabled(self) -> bool:
        return bool(COMFYUI_VIDEO_CONFIG.get("ENABLED", False))

    def is_server_ready(self) -> bool:
        return self.is_enabled() and bool(self.server_address)

    # ===== CNB workspace 自动启停 =====

    async def _check_comfyui_online(self, timeout_seconds: float = 5.0) -> bool:
        if not self.server_address:
            return False
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=timeout_seconds)
            ) as session:
                async with session.get(f"{self.server_address}/system_stats") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def _find_running_workspace(self, headers: dict) -> Optional[str]:
        try:
            list_url = f"{self._CNB_API_BASE}/workspace/list?page=1&pageSize=50"
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                async with session.get(list_url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    list_data = await resp.json(content_type=None)
            for item in list_data.get("list", []):
                slug = str(item.get("slug") or "").strip()
                status = str(item.get("status") or "").strip()
                bid = str(item.get("business_id") or "").strip()
                if (
                    slug == self._CNB_REPO
                    and status not in ("closed", "building", "pending", "queued", "", None)
                    and bid
                ):
                    return f"https://{bid}-8188.cnb.run"
            return None
        except Exception as e:
            log.warning(f"VideoComfyUI: 查找已有 workspace 异常: {e}")
            return None

    async def _wait_comfyui_ready(
        self, url: str, max_attempts: int = 72, interval: float = 5.0
    ) -> bool:
        """视频 workspace 启动需要更长时间（模型 38GB），用更多轮询"""
        for attempt in range(max_attempts):
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as session:
                    async with session.get(f"{url}/system_stats") as resp:
                        if resp.status == 200:
                            log.info(f"VideoComfyUI: ComfyUI 已就绪! (轮询 {attempt + 1}/{max_attempts})")
                            return True
            except Exception:
                pass
            if (attempt + 1) % 6 == 0:
                log.info(f"VideoComfyUI: 等待 ComfyUI 视频服务启动... (轮询 {attempt + 1}/{max_attempts})")
            await asyncio.sleep(interval)
        return False

    async def _start_cnb_workspace(self, notify_callback=None) -> Optional[str]:
        """自动启动 CNB 视频 workspace 并等待 ComfyUI 就绪"""
        if not self._CNB_TOKEN:
            log.error("VideoComfyUI: CNB_TOKEN 未设置")
            return None

        headers = {
            "Authorization": f"Bearer {self._CNB_TOKEN}",
            "Accept": "application/vnd.cnb.api+json",
            "Content-Type": "application/json",
        }

        try:
            # Step 0: 先检查已有 workspace
            existing_url = await self._find_running_workspace(headers)
            if existing_url:
                log.info(f"VideoComfyUI: 发现已有 running 的 workspace: {existing_url}")
                if await self._wait_comfyui_ready(existing_url):
                    self.server_address = existing_url
                    COMFYUI_VIDEO_CONFIG["SERVER_ADDRESS"] = self.server_address
                    return existing_url
                else:
                    log.warning("VideoComfyUI: 已有 workspace 的 ComfyUI 未就绪，将启动新 workspace")

            # Step 1: 提交启动请求
            start_url = f"{self._CNB_API_BASE}/{self._CNB_REPO}/-/workspace/start"
            log.info(f"VideoComfyUI: 正在启动 CNB workspace: POST {start_url}")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.post(start_url, json={"branch": "main"}, headers=headers) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        log.error(f"VideoComfyUI: 启动 workspace 失败: status={resp.status}, body={body[:200]}")
                        return None
                    start_data = await resp.json(content_type=None)
                    workspace_sn = str(start_data.get("sn") or "").strip()
                    if not workspace_sn:
                        log.error(f"VideoComfyUI: 启动 workspace 返回缺少 sn: {start_data}")
                        return None
                    log.info(f"VideoComfyUI: Workspace 启动请求已提交, sn={workspace_sn}")

            # Step 2: 轮询 workspace list
            # 视频模型 38GB，冷启动约 5-8 分钟，先等 120 秒再开始轮询
            if notify_callback:
                try:
                    await notify_callback("🎬 视频生成服务正在预热中，约需5-8分钟（下载38GB模型），请稍等一下哦~")
                except Exception:
                    pass

            business_id = None
            initial_delay = 120
            max_poll = 60
            poll_interval = 5

            log.info(f"VideoComfyUI: 等待 {initial_delay}s 后开始轮询 workspace 状态...")
            await asyncio.sleep(initial_delay)

            for attempt in range(max_poll):
                await asyncio.sleep(poll_interval)
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=15)
                    ) as session:
                        list_url = f"{self._CNB_API_BASE}/workspace/list?page=1&pageSize=20"
                        async with session.get(list_url, headers=headers) as resp:
                            if resp.status != 200:
                                continue
                            list_data = await resp.json(content_type=None)
                        for item in list_data.get("list", []):
                            if item.get("sn") == workspace_sn:
                                status = str(item.get("status") or "").strip()
                                bid = str(item.get("business_id") or "").strip()
                                log.info(
                                    f"VideoComfyUI: Workspace 轮询 {attempt + 1}/{max_poll}: "
                                    f"status={status}, business_id={bid}"
                                )
                                if status and status not in (
                                    "closed", "building", "pending", "queued", ""
                                ) and bid:
                                    business_id = bid
                                    break
                    if business_id:
                        break
                except Exception as e:
                    log.warning(f"VideoComfyUI: 轮询 workspace 状态异常: {e}")

            if not business_id:
                log.error(f"VideoComfyUI: Workspace 启动超时 (sn={workspace_sn})")
                return None

            # Step 3: 构造新公网 URL
            new_url = f"https://{business_id}-8188.cnb.run"
            log.info(f"VideoComfyUI: Workspace 已就绪, 新公网地址: {new_url}")

            # Step 4: 等待 ComfyUI 服务启动
            if not await self._wait_comfyui_ready(new_url):
                log.error(f"VideoComfyUI: ComfyUI 在新 workspace 上未就绪: {new_url}")
                return None

            # Step 5: 更新服务地址
            self.server_address = new_url
            COMFYUI_VIDEO_CONFIG["SERVER_ADDRESS"] = self.server_address
            log.info(f"VideoComfyUI: 服务地址已更新为: {self.server_address}")
            return new_url

        except Exception as e:
            log.error(f"VideoComfyUI: 启动 CNB workspace 异常: {e}", exc_info=True)
            return None

    # ===== 工作流提交 =====

    def _build_workflow(
        self,
        prompt: str,
        negative_prompt: str = "",
        seed: Optional[int] = None,
        width: int = 832,
        height: int = 480,
        length: int = 81,
        task_type: str = "t2v",
        frame_rate: int = 32,
        use_rife: bool = False,
        use_rtx_upscale: bool = False,
        image_filenames: Optional[list] = None,
    ) -> Dict[str, Any]:
        """构建工作流 payload，根据 task_type 选择对应模板并替换变量"""
        if seed is None:
            seed = random.randint(0, 2**32 - 1)

        if not negative_prompt:
            negative_prompt = "低质量视频, 模糊, 变形"

        # 根据模式选模板
        template = self._workflow_templates.get(task_type, self._workflow_templates["t2v"])
        workflow = json.loads(json.dumps(template))

        # 替换 BerniniStudio 参数 (node 38)
        node38 = workflow["38"]["inputs"]
        node38["prompt"] = prompt
        node38["negative_prompt"] = negative_prompt
        node38["width"] = width
        node38["height"] = height
        node38["length"] = length
        node38["task_type"] = task_type

        # 替换 KSampler seeds (node 36, 37)
        workflow["36"]["inputs"]["noise_seed"] = seed
        workflow["37"]["inputs"]["noise_seed"] = seed

        # 替换视频输出帧率 (node 26)
        workflow["26"]["inputs"]["frame_rate"] = frame_rate

        # 替换参考图文件名 (i2v/r2v)
        if image_filenames:
            if "43" in workflow and "%image0%" in workflow["43"]["inputs"].get("image", ""):
                if len(image_filenames) > 0 and image_filenames[0]:
                    workflow["43"]["inputs"]["image"] = image_filenames[0]
            if "46" in workflow and "%image1%" in workflow["46"]["inputs"].get("image", ""):
                if len(image_filenames) > 1 and image_filenames[1]:
                    workflow["46"]["inputs"]["image"] = image_filenames[1]
            # r2v: 如果有第2张图但模板没有node 46, 动态添加
            if task_type == "r2v" and len(image_filenames) > 1 and image_filenames[1] and "46" not in workflow:
                workflow["46"] = {"class_type": "LoadImage", "inputs": {"image": image_filenames[1]}}
                workflow["38"]["inputs"]["image1"] = ["46", 0]

        # RIFE/RTX 后处理已移除 (v0.27不兼容RIFE VFI, 共享GPU不支持RTX)
        # VHS_VideoCombine 直接连接 VAEDecode(16)
        return workflow

    async def _queue_prompt_and_wait(
        self, workflow_payload: Dict[str, Any], timeout_seconds: int = 600
    ) -> Optional[Dict[str, str]]:
        """提交工作流并轮询等待结果。视频生成需要更长时间（默认 10 分钟超时）"""
        client_timeout = aiohttp.ClientTimeout(total=timeout_seconds + 60)
        payload = {
            "prompt": workflow_payload,
            "client_id": str(uuid.uuid4()),
        }

        async with self._request_semaphore:
            async with aiohttp.ClientSession(timeout=client_timeout) as session:
                try:
                    async with session.post(self.prompt_url, json=payload) as response:
                        if response.status != 200:
                            response_text = await response.text()
                            log.error(
                                f"VideoComfyUI: 提交任务失败: status={response.status}, body={response_text[:500]}"
                            )
                            return None
                        response_data = await response.json(content_type=None)
                except Exception as error:
                    log.error(f"VideoComfyUI: 提交任务异常: {error}", exc_info=True)
                    return None

                prompt_id = str(response_data.get("prompt_id") or "").strip()
                if not prompt_id:
                    log.error(f"VideoComfyUI: 返回缺少 prompt_id: {response_data}")
                    return None

                log.info(f"VideoComfyUI: 任务已提交, prompt_id={prompt_id}")

                # 轮询 /history/{prompt_id}
                poll_interval = 3.0
                elapsed = 0
                while elapsed < timeout_seconds:
                    await asyncio.sleep(poll_interval)
                    elapsed += poll_interval

                    try:
                        async with session.get(
                            f"{self.server_address}/history/{prompt_id}"
                        ) as hist_resp:
                            if hist_resp.status != 200:
                                continue
                            hist_data = await hist_resp.json(content_type=None)
                    except Exception:
                        continue

                    if prompt_id not in hist_data:
                        if elapsed % 30 < poll_interval:
                            log.info(f"VideoComfyUI: 等待生成中... ({elapsed}s)")
                        continue

                    # 任务完成，提取输出
                    prompt_result = hist_data[prompt_id]
                    # 检测执行错误（如 OOM / missing node）
                    status_info = prompt_result.get("status", {})
                    status_str = status_info.get("status_str", "")
                    if status_str == "error":
                        err_msg = ""
                        err_type = "execution_error"
                        for msg in status_info.get("messages", []):
                            if isinstance(msg, list) and len(msg) >= 2 and msg[0] == "execution_error":
                                err_info = msg[1] if isinstance(msg[1], dict) else {}
                                err_msg = err_info.get("exception_message", str(msg[1]))
                                err_type = err_info.get("exception_type", "execution_error")
                        # Also check for validation errors
                        for msg in status_info.get("messages", []):
                            if isinstance(msg, list) and len(msg) >= 2 and "validation" in str(msg[0]).lower():
                                err_msg = str(msg[1])[:300]
                                err_type = "validation_error"
                        if not err_msg:
                            err_msg = str(status_info.get("messages", []))[:300]
                        log.error(f"VideoComfyUI: 任务执行失败: {err_type}: {err_msg[:200]}")
                        return {"error": True, "type": err_type, "message": err_msg}
                    # Fallback: old-style execution_error key
                    exec_error = status_info.get("execution_error", {})
                    if exec_error:
                        err_msg = exec_error.get("exception_message", "")
                        err_type = exec_error.get("exception_type", "")
                        log.error(f"VideoComfyUI: 任务执行失败: {err_type}: {err_msg[:200]}")
                        return {"error": True, "type": err_type, "message": err_msg}
                    outputs = prompt_result.get("outputs", {})

                    # 找 VHS_VideoCombine (node 26) 的输出
                    for node_id, node_output in outputs.items():
                        gifs = node_output.get("gifs", [])
                        if gifs:
                            meta = gifs[0]
                            return {
                                "filename": str(meta.get("file") or meta.get("filename") or ""),
                                "subfolder": str(meta.get("subfolder") or ""),
                                "type": str(meta.get("type") or "output"),
                                "format": str(meta.get("format") or "video/h264-mp4"),
                            }
                        images = node_output.get("images", [])
                        if images:
                            meta = images[0]
                            return {
                                "filename": str(meta.get("filename") or ""),
                                "subfolder": str(meta.get("subfolder") or ""),
                                "type": str(meta.get("type") or "output"),
                            }

                    # outputs 不为空但没找到视频
                    log.warning(f"VideoComfyUI: 任务完成但未找到视频输出: {outputs}")
                    return None

                log.error(f"VideoComfyUI: 任务超时 ({timeout_seconds}s)")
                return None

    async def _download_video(self, media_meta: Dict[str, str]) -> Optional[bytes]:
        """从 ComfyUI 下载视频文件"""
        filename = str(media_meta.get("filename") or "").strip()
        if not filename:
            return None

        params: Dict[str, str] = {"filename": filename}
        subfolder = str(media_meta.get("subfolder") or "").strip()
        image_type = str(media_meta.get("type") or "").strip()
        if subfolder:
            params["subfolder"] = urllib.parse.quote(subfolder)
        if image_type:
            params["type"] = image_type

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=300)
            ) as session:
                async with session.get(self.view_url, params=params) as response:
                    if response.status != 200:
                        response_text = await response.text()
                        log.error(
                            f"VideoComfyUI: 下载视频失败: status={response.status}, body={response_text[:200]}"
                        )
                        return None
                    return await response.read()
        except Exception as error:
            log.error(f"VideoComfyUI: 下载视频异常: {error}", exc_info=True)
            return None

    # ===== 上传参考图（用于 r2v 模式）=====

    async def upload_input_image(self, image_bytes: bytes, filename: Optional[str] = None) -> Optional[str]:
        """上传图片到 ComfyUI input 目录，返回文件名"""
        if not filename:
            filename = f"ref_{uuid.uuid4().hex[:8]}.png"

        try:
            form = aiohttp.FormData()
            form.add_field("image", image_bytes, filename=filename, content_type="image/png")
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60)
            ) as session:
                async with session.post(
                    f"{self.server_address}/upload/image", data=form
                ) as resp:
                    if resp.status != 200:
                        log.error(f"VideoComfyUI: 上传图片失败: status={resp.status}")
                        return None
                    data = await resp.json(content_type=None)
                    return str(data.get("name") or "")
        except Exception as e:
            log.error(f"VideoComfyUI: 上传图片异常: {e}")
            return None

    # ===== 主入口 =====

    async def generate_video(
        self,
        prompt: str,
        negative_prompt: str = "",
        seed: Optional[int] = None,
        width: int = 832,
        height: int = 480,
        length: int = 81,
        task_type: str = "t2v",
        frame_rate: int = 32,
        use_rife: bool = False,
        use_rtx_upscale: bool = False,
        notify_callback=None,
        reference_images: Optional[list] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        生成视频。返回 {"video_bytes": bytes, "filename": str, "seed": int} 或 None。

        Args:
            prompt: 视频描述提示词
            negative_prompt: 负面提示词
            seed: 随机种子
            width: 视频宽度
            height: 视频高度
            length: 帧数 (81 帧 ≈ 2.5秒 @32fps)
            task_type: t2v (文生视频) / r2v (参考图生视频) / v2v (视频生视频)
            frame_rate: 输出帧率
            use_rife: 是否使用 RIFE 帧插值
            use_rtx_upscale: 是否使用 RTX 视频超分
            notify_callback: async 回调函数，用于通知用户进度
            reference_images: 参考图片 bytes 列表 (r2v 模式)
        """
        try:
            if not self.is_server_ready():
                log.warning("VideoComfyUI: 服务未启用或地址未配置")
                return None

            # 检查 ComfyUI 是否在线，不在线则自动启动
            if not await self._check_comfyui_online():
                log.info("VideoComfyUI: 服务不可达，正在自动启动 workspace...")
                new_url = await self._start_cnb_workspace(notify_callback=notify_callback)
                if not new_url:
                    return None

            # 如果是 i2v/r2v 模式且有参考图，先上传图片到 ComfyUI
            image_filenames = None
            if task_type in ("i2v", "r2v") and reference_images:
                image_filenames = []
                for i, img_bytes in enumerate(reference_images[:2]):
                    name = await self.upload_input_image(img_bytes, f"ref_{i}.png")
                    if name:
                        image_filenames.append(name)
                    else:
                        image_filenames.append(None)
                if not any(image_filenames):
                    log.warning(f"VideoComfyUI: {task_type} 模式但参考图上传失败，降级为 t2v")
                    task_type = "t2v"
                    image_filenames = None

            # 构建工作流
            workflow = self._build_workflow(
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=width,
                height=height,
                length=length,
                task_type=task_type,
                frame_rate=frame_rate,
                use_rife=use_rife,
                use_rtx_upscale=use_rtx_upscale,
                image_filenames=image_filenames,
            )

            # 通知开始生成
            if notify_callback:
                try:
                    await notify_callback("🎬 视频正在生成中，这需要几分钟时间...")
                except Exception:
                    pass

            # 提交并等待结果 — 跟画图一样自动重试（共享GPU显存可能被占，等一会就空出来）
            retry_max_attempts = 15
            retry_delay_seconds = 10
            media_meta = None
            for retry_attempt in range(retry_max_attempts):
                media_meta = await self._queue_prompt_and_wait(workflow, timeout_seconds=600)
                if isinstance(media_meta, dict) and not media_meta.get("error"):
                    break
                if retry_attempt < retry_max_attempts - 1:
                    err_info = ""
                    if isinstance(media_meta, dict) and media_meta.get("error"):
                        err_info = " (%s: %s)" % (media_meta.get("type", ""), media_meta.get("message", "")[:100])
                    log.warning(
                        "VideoComfyUI: 生成失败 (尝试 %d/%d)%s，等待 %ds 后重试"
                        % (retry_attempt + 1, retry_max_attempts, err_info, retry_delay_seconds)
                    )
                    if notify_callback and retry_attempt == 0:
                        try:
                            await notify_callback("⚠️ 显存暂时不足，正在排队等待重试...")
                        except Exception:
                            pass
                    # 释放显存
                    try:
                        async with aiohttp.ClientSession() as session:
                            await session.post(
                                f"{self.server_address}/free",
                                json={"unload_models": True, "free_memory": True},
                                timeout=aiohttp.ClientTimeout(total=15)
                            )
                    except Exception:
                        pass
                    await asyncio.sleep(retry_delay_seconds)
                else:
                    log.error("VideoComfyUI: 生成失败，已达到最大重试次数 %d" % retry_max_attempts)

            if not media_meta or not isinstance(media_meta, dict) or media_meta.get("error"):
                log.error("VideoComfyUI: 视频生成失败")
                return None

            # 下载视频
            video_bytes = await self._download_video(media_meta)
            if not video_bytes:
                log.error("VideoComfyUI: 视频下载失败")
                return None

            return {
                "video_bytes": video_bytes,
                "filename": media_meta.get("filename", "output.mp4"),
                "seed": seed if seed is not None else 0,
                "width": width,
                "height": height,
                "length": length,
                "task_type": task_type,
            }

        except Exception as e:
            log.error(f"VideoComfyUI: generate_video 异常: {e}", exc_info=True)
            return None


# 单例
video_comfyui_service = VideoComfyUIService()
