#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
批量替换脚本：将所有"月月"替换为"月月"
"""

import os
import re
import sys

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

# 要排除的目录
EXCLUDE_DIRS = {'.git', '__pycache__', 'node_modules', '.venv', 'venv', 'pgdata', '.devcontainer'}

# 要处理的文件扩展名
INCLUDE_EXTENSIONS = {'.py', '.md', '.json', '.yml', '.yaml', '.xml', '.txt', '.vue', '.html', '.js'}

# 替换规则
REPLACEMENTS = [
    ('月月', '月月'),
]

def should_process_file(filepath):
    """判断是否应该处理该文件"""
    _, ext = os.path.splitext(filepath)
    return ext.lower() in INCLUDE_EXTENSIONS

def process_file(filepath):
    """处理单个文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        original_content = content
        for old, new in REPLACEMENTS:
            content = content.replace(old, new)
        
        if content != original_content:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            return True
        return False
    except Exception as e:
        print(f"  ⚠️ 处理失败: {filepath} - {e}")
        return False

def main():
    """主函数"""
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    
    print("[*] 开始批量替换 '月月' -> '月月'")
    print(f"[*] 根目录: {root_dir}")
    print("-" * 50)
    
    modified_files = []
    
    for dirpath, dirnames, filenames in os.walk(root_dir):
        # 排除不需要处理的目录
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            
            if should_process_file(filepath):
                if process_file(filepath):
                    rel_path = os.path.relpath(filepath, root_dir)
                    modified_files.append(rel_path)
                    print(f"  [OK] 已修改: {rel_path}")
    
    print("-" * 50)
    print(f"[*] 共修改了 {len(modified_files)} 个文件")
    
    if modified_files:
        print("\n修改的文件列表:")
        for f in modified_files:
            print(f"  - {f}")

if __name__ == "__main__":
    main()