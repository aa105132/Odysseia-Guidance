# -*- coding: utf-8 -*-
"""
月月脚本执行工具 — 让月月自己写 Python 脚本、调外部 API、设定时任务。

与 self_maintain 的区别：
  - self_maintain: 仅开发者可用，用于诊断/修改月月自己的代码
  - run_script: 月月自己可用，用于写脚本调外部 API（如 TikHub 抖音热点）、
   处理数据、设定时任务。在隔离的 sidecar 容器中执行，无权修改月月核心代码。

安全边界：
  - 不挂 Docker Socket
  - 不能重启/修改任何容器
  - 可访问外网（调 API）
  - 可读写 /workspace/data/（月月的数据目录）
  - 可设定时任务（crontab，存宿主机 /opt/Odysseia-Guidance/data/cron/）
"""

import asyncio
import json
import logging
import os
import shlex
import tempfile
import time
from typing import Any, Dict, Optional

from src.chat.features.tools.tool_metadata import tool_metadata
from src.config import DEVELOPER_USER_IDS

log = logging.getLogger(__name__)

# 侧车容器名
_SIDECAR_CONTAINER = "yueyue-self-maintainer"

# 月月可用的 crontab 脚本目录（持久化）
_CRON_DIR = "/workspace/data/cron"

# --- 内置 API token 环境变量（注入到 sidecar） ---
# 月月写脚本时可以直接 os.environ["TIKHUB_TOKEN"] 获取
_INJECTED_ENV_KEYS = {
    "TIKHUB_TOKEN": "1K9TUMBP0W60lEyfIq6QmxrqGxGka8d97S7/vFyre9C6lVqXHSyiSymhAQ==",
    "TIKHUB_BASE_URL": "https://api.tikhub.dev",
}


async def _exec_in_sidecar(command: str, timeout: int = 60) -> Dict[str, Any]:
    """在侧车容器中执行命令，返回结果。"""
    try:
        import docker
    except ImportError:
        return {"error": "Docker SDK 未安装。"}

    try:
        client = docker.from_env()
        container = client.containers.get(_SIDECAR_CONTAINER)
    except Exception as e:
        return {"error": f"无法连接侧车容器: {e}"}

    try:
        # 注入环境变量
        env_parts = [f'export {k}={shlex.quote(v)}' for k, v in _INJECTED_ENV_KEYS.items()]
        env_block = "\n".join(env_parts)
        wrapped = f'export PATH=/opt/hermes-venv/bin:$PATH\n{env_block}\n{command}'

        result = container.exec_run(
            ["bash", "-c", wrapped],
            workdir="/workspace",
            demux=True,
        )
        stdout = result.output[0].decode("utf-8", errors="replace") if result.output[0] else ""
        stderr = result.output[1].decode("utf-8", errors="replace") if result.output[1] else ""
        exit_code = result.exit_code

        return {
            "exit_code": exit_code,
            "stdout": stdout[:8000],
            "stderr": stderr[:4000],
            "success": exit_code == 0,
        }
    except Exception as e:
        return {"error": f"执行失败: {e}"}


def _validate_script_safety(code: str) -> tuple[bool, str]:
    """基本安全检查——防止月月误操作。"""
    # 禁止操作 docker
    dangerous_patterns = [
        ("docker ", "禁止操作 docker 命令"),
        ("systemctl", "禁止操作系统服务"),
        ("reboot", "禁止重启"),
        ("shutdown", "禁止关机"),
        ("rm -rf /", "禁止删除根目录"),
        ("os.system", "请使用 subprocess 而非 os.system"),
    ]
    lower = code.lower()
    for pattern, msg in dangerous_patterns:
        if pattern.lower() in lower:
            return False, msg
    return True, "ok"


@tool_metadata(
    name="运行脚本",
    description="自己写 Python 脚本调外部 API、处理数据、设定时任务（如获取抖音热点）",
    emoji="⚡",
    category="系统",
)
async def run_script(
    action: str = "exec",
    code: str = "",
    language: str = "python",
    timeout: int = 60,
    cron_schedule: str = "",
    cron_name: str = "",
    **kwargs,
) -> Dict[str, Any]:
    """
    月月的脚本执行工具——你可以自己写脚本干活。

    支持的操作 (action):
    - exec: 执行一段代码（默认 Python），返回输出
    - cron: 创建一个定时任务（需提供 code, cron_schedule, cron_name）
    - cron_list: 列出所有定时任务
    - cron_remove: 删除一个定时任务（需提供 cron_name）
    - api_get: 发一个 GET 请求到指定 URL（需提供 code 作为 URL）
    - api_post: 发一个 POST 请求（code 为 JSON: {"url": "...", "data": {...}}）

    内置环境变量（脚本中可直接用）:
    - TIKHUB_TOKEN: TikHub API token
    - TIKHUB_BASE_URL: https://api.tikhub.dev

    常见用法：
    1. 获取抖音热点：
       action="api_get", code="https://api.tikhub.dev/api/v1/douyin/app/v3/fetch_hot_search_list"
       （会自动带上 TIKHUB_TOKEN 认证头）

    2. 写 Python 脚本处理数据：
       action="exec", code="import json\\nprint(json.dumps({'hello': 'world'}))"

    3. 设定时任务：
       action="cron", cron_schedule="0 */2 * * *", cron_name="douyin-hot",
       code="import urllib.request..."

    Args:
        action: 操作类型
        code: 代码内容或 URL
        language: 脚本语言（默认 python，支持 bash）
        timeout: 执行超时秒数（默认60，最大300）
        cron_schedule: crontab 格式的时间表（如 "0 9 * * *" 每天9点, "*/30 * * * *" 每30分钟）
        cron_name: 定时任务名称（英文）
    """
    action_normalized = str(action or "exec").strip().lower()

    # --- 执行脚本 ---
    if action_normalized == "exec":
        if not code:
            return {"error": "exec 操作需要提供 code 参数。"}

        ok, msg = _validate_script_safety(code)
        if not ok:
            return {"error": f"安全检查未通过: {msg}"}

        timeout = min(max(int(timeout or 60), 5), 300)

        if language == "bash":
            cmd = code
        else:
            # 写临时文件执行 Python
            cmd = f'python3 -c {shlex.quote(code)}'

        result = await _exec_in_sidecar(cmd, timeout=timeout)
        return result

    # --- API GET ---
    if action_normalized == "api_get":
        if not code:
            return {"error": "api_get 操作需要提供 code 作为 URL。"}
        url = code.strip()
        # 用 curl 发请求，自动带上 TikHub token（如果是 TikHub API）
        if "tikhub" in url.lower():
            curl_cmd = (
                f'curl -s -m {timeout} -H "Authorization: Bearer $TIKHUB_TOKEN" '
                f'{shlex.quote(url)}'
            )
        else:
            curl_cmd = f'curl -s -m {timeout} {shlex.quote(url)}'
        result = await _exec_in_sidecar(curl_cmd, timeout=timeout + 10)
        return result

    # --- API POST ---
    if action_normalized == "api_post":
        if not code:
            return {"error": "api_post 操作需要 code 为 JSON: {url, data, headers}"}
        try:
            req = json.loads(code)
        except json.JSONDecodeError as e:
            return {"error": f"JSON 解析失败: {e}"}
        url = req.get("url", "")
        data = req.get("data", {})
        headers = req.get("headers", {})
        if not url:
            return {"error": "缺少 url"}

        header_args = ""
        for k, v in headers.items():
            header_args += f' -H {shlex.quote(f"{k}: {v}")} '
        # 如果是 TikHub API，自动加 token
        if "tikhub" in url.lower() and "Authorization" not in str(headers):
            header_args += ' -H "Authorization: Bearer $TIKHUB_TOKEN" '

        data_arg = f' -d {shlex.quote(json.dumps(data))}' if data else ""
        curl_cmd = f'curl -s -m {timeout} {header_args} {data_arg} {shlex.quote(url)}'
        result = await _exec_in_sidecar(curl_cmd, timeout=timeout + 10)
        return result

    # --- 定时任务 ---
    if action_normalized == "cron":
        if not code or not cron_schedule or not cron_name:
            return {
                "error": "cron 操作需要 code（脚本内容）、cron_schedule（时间表）和 cron_name（名称）"
            }

        ok, msg = _validate_script_safety(code)
        if not ok:
            return {"error": f"安全检查未通过: {msg}"}

        safe_name = "".join(c for c in cron_name if c.isalnum() or c in "-_").lower()
        if not safe_name:
            return {"error": "cron_name 无效"}

        # 确保目录存在
        mkdir_cmd = f"mkdir -p {_CRON_DIR}"
        await _exec_in_sidecar(mkdir_cmd, timeout=10)

        # 写脚本文件
        script_path = f"{_CRON_DIR}/{safe_name}.py"
        write_cmd = f"cat > {script_path} << 'CRON_SCRIPT_EOF'\n{code}\nCRON_SCRIPT_EOF"
        result = await _exec_in_sidecar(write_cmd, timeout=10)
        if not result.get("success"):
            return {"error": f"写入脚本失败: {result.get('stderr', '')}"}

        # 写 crontab 条目
        cron_entry = f"{cron_schedule} /opt/hermes-venv/bin/python3 {script_path} >> {_CRON_DIR}/{safe_name}.log 2>&1"

        # 读取当前 crontab，追加新条目
        cron_cmd = f'(crontab -l 2>/dev/null; echo "{cron_entry}") | sort -u | crontab -'
        result = await _exec_in_sidecar(cron_cmd, timeout=10)

        return {
            "success": True,
            "message": f"定时任务 '{safe_name}' 已创建",
            "schedule": cron_schedule,
            "script_path": script_path,
            "log_path": f"{_CRON_DIR}/{safe_name}.log",
        }

    if action_normalized == "cron_list":
        result = await _exec_in_sidecar("crontab -l 2>&1", timeout=10)
        return result

    if action_normalized == "cron_remove":
        if not cron_name:
            return {"error": "cron_remove 需要 cron_name"}
        safe_name = "".join(c for c in cron_name if c.isalnum() or c in "-_").lower()
        # 从 crontab 中删除包含该名称的行
        remove_cmd = f'crontab -l 2>/dev/null | grep -v "{safe_name}" | crontab -'
        result = await _exec_in_sidecar(remove_cmd, timeout=10)
        # 删脚本文件
        await _exec_in_sidecar(f"rm -f {_CRON_DIR}/{safe_name}.py {_CRON_DIR}/{safe_name}.log", timeout=10)
        return {
            "success": True,
            "message": f"定时任务 '{safe_name}' 已删除",
        }

    return {
        "error": f"未知的 action: '{action}'。支持: exec, api_get, api_post, cron, cron_list, cron_remove"
    }
