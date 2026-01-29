# -*- coding: utf-8 -*-
"""
配置热更新 HTTP 服务器
用于接收来自 Dashboard 的配置更新请求
"""

import os
import logging
from aiohttp import web

log = logging.getLogger(__name__)

# 用于存储 gemini_service 实例的引用
_gemini_service = None


def set_gemini_service(service):
    """设置 gemini_service 实例引用"""
    global _gemini_service
    _gemini_service = service
    log.info("ConfigReloadServer: GeminiService 实例已注册")


async def reload_api_keys(request):
    """处理 API 密钥热更新请求"""
    # 验证请求来源（简单的共享密钥验证）
    auth_header = request.headers.get("Authorization", "")
    expected_secret = os.getenv("DASHBOARD_SECRET", "")
    
    if not expected_secret or auth_header != f"Bearer {expected_secret}":
        return web.json_response({"success": False, "error": "未授权"}, status=401)
    
    if _gemini_service is None:
        return web.json_response({"success": False, "error": "GeminiService 未初始化"}, status=500)
    
    try:
        # 尝试从请求体获取新密钥，如果没有则从环境变量重新加载
        data = await request.json() if request.content_length else {}
        new_keys = data.get("api_keys")
        
        result = _gemini_service.reload_api_keys(new_keys)
        return web.json_response(result)
        
    except Exception as e:
        log.error(f"热更新 API 密钥失败: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=500)


async def health_check(request):
    """健康检查端点"""
    return web.json_response({
        "status": "ok",
        "service": "config_reload_server",
        "gemini_service_available": _gemini_service is not None
    })


async def start_config_reload_server(host: str = "0.0.0.0", port: int = 8081):
    """启动配置热更新服务器"""
    app = web.Application()
    app.router.add_post("/api/reload/api-keys", reload_api_keys)
    app.router.add_get("/health", health_check)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    
    log.info(f"✅ 配置热更新服务器已启动在 {host}:{port}")
    return runner