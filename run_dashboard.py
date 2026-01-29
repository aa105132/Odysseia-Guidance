#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Dashboard 启动脚本
运行此脚本来启动管理面板 Web 服务器
"""

import os
import sys

# 确保可以导入项目模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()

if __name__ == "__main__":
    import uvicorn
    
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))
    
    print(f"\n🦊 月月 Dashboard 正在启动...")
    print(f"📍 访问地址: http://localhost:{port}")
    print(f"🔑 请在 .env 中设置 DASHBOARD_SECRET 作为登录密钥\n")
    
    uvicorn.run(
        "src.dashboard.api:app",
        host=host,
        port=port,
        reload=True,
        log_level="info"
    )