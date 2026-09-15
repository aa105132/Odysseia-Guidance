# -*- coding: utf-8 -*-
# 角色参考图库管理 API —— 追加到 dashboard/api.py 的独立模块
# 由 api.py 尾部 include 就近挂载；端点：
#   GET  /api/character-refs            列表（角色名/大小/尺寸/修改时间/缩略图URL）
#   GET  /api/character-refs/{name}     原图下载
#   GET  /api/character-refs/{name}/thumb  缩略图（256px JPEG，缓存）
#   POST /api/character-refs/{name}     上传/替换（multipart，单图）
#   DELETE /api/character-refs/{name}   删除
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/character-refs")

REF_DIR = Path(os.getenv("CHARACTER_REF_DIR", "/app/data/character_refs"))
THUMB_DIR = REF_DIR / ".thumbs"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，防御性上限
NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5·\-]{1,32}$")


def _safe_name(name: str) -> str:
    """角色名校验：中英文/数字/点/下划线/连字符，防路径穿越。"""
    name = name.strip()
    if not NAME_RE.match(name):
        raise HTTPException(status_code=400, detail="角色名仅支持中英文/数字/·/-/_，最长32字")
    return name


def _thumb_path(name: str, mtime: float) -> Path:
    THUMB_DIR.mkdir(parents=True, exist_ok=True)
    return THUMB_DIR / f"{name}_{int(mtime)}.jpg"


def _make_thumb(src: Path, dst: Path, size: int = 256) -> bool:
    try:
        from PIL import Image

        img = Image.open(src)
        img.thumbnail((size, size))
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(dst, format="JPEG", quality=82)
        return True
    except Exception as e:
        log.warning(f"[参考图库] 缩略图生成失败 {src.name}: {e}")
        return False


def _list_dir() -> List[Dict[str, Any]]:
    REF_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for p in sorted(REF_DIR.iterdir()):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_EXT:
            continue
        st = p.stat()
        # 尺寸懒读取（打开图片文件很轻，列表几十张没压力）
        dims = None
        try:
            from PIL import Image

            with Image.open(p) as im:
                dims = {"w": im.width, "h": im.height}
        except Exception:
            pass
        items.append(
            {
                "name": p.stem,
                "filename": p.name,
                "size": st.st_size,
                "mtime": int(st.st_mtime),
                "dims": dims,
            }
        )
    return items


def register(app, verify_token):
    """挂到主 FastAPI app；鉴权与既有端点一致。"""
    import asyncio

    @router.get("")
    async def list_refs(token: str = Depends(verify_token)):
        return {"items": await asyncio.to_thread(_list_dir)}

    @router.get("/{name}/thumb")
    async def get_thumb(name: str, token: str = Depends(verify_token)):
        name = _safe_name(name)
        src = next(
            (p for p in REF_DIR.glob(f"{name}.*") if p.suffix.lower() in ALLOWED_EXT), None
        )
        if not src:
            raise HTTPException(status_code=404, detail="未找到该角色参考图")
        st = src.stat()
        tp = _thumb_path(name, st.st_mtime)
        if not tp.exists():
            if not await asyncio.to_thread(_make_thumb, src, tp):
                # 缩略图失败就回原图
                return FileResponse(src)
            # 清掉同角色旧缩略图
            for old in THUMB_DIR.glob(f"{name}_*.jpg"):
                if old != tp:
                    old.unlink(missing_ok=True)
        return FileResponse(tp, headers={"Cache-Control": "private, max-age=3600"})

    @router.get("/{name}")
    async def get_ref(name: str, token: str = Depends(verify_token)):
        name = _safe_name(name)
        src = next(
            (p for p in REF_DIR.glob(f"{name}.*") if p.suffix.lower() in ALLOWED_EXT), None
        )
        if not src:
            raise HTTPException(status_code=404, detail="未找到该角色参考图")
        return FileResponse(src, filename=src.name)

    @router.post("/{name}")
    async def upload_ref(
        name: str,
        file: UploadFile = File(...),
        token: str = Depends(verify_token),
    ):
        name = _safe_name(name)
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="单图上限 20MB")

        # 校验真的是图片 + 统一转存为 png（与工具侧预期一致，gif 动图取首帧）
        try:
            from PIL import Image

            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception:
            raise HTTPException(status_code=400, detail="不是有效的图片文件")

        REF_DIR.mkdir(parents=True, exist_ok=True)
        # 同名不同扩展的旧文件先清掉，保证 glob(name.*) 唯一
        for old in REF_DIR.glob(f"{name}.*"):
            if old.suffix.lower() in ALLOWED_EXT:
                old.unlink()
        dst = REF_DIR / f"{name}.png"

        def _save():
            nonlocal img
            if img.mode == "P" and getattr(img, "is_animated", False):
                img.save(dst, format="GIF")  # 动图保留
            else:
                if img.mode not in ("RGB", "RGBA", "L"):
                    img = img.convert("RGBA")
                img.save(dst, format="PNG")
            return dst

        await asyncio.to_thread(_save)
        st = dst.stat()
        return {
            "success": True,
            "name": name,
            "size": st.st_size,
            "message": f"参考图 {name} 已入库",
        }

    @router.delete("/{name}")
    async def delete_ref(name: str, token: str = Depends(verify_token)):
        name = _safe_name(name)
        src = next(
            (p for p in REF_DIR.glob(f"{name}.*") if p.suffix.lower() in ALLOWED_EXT), None
        )
        if not src:
            raise HTTPException(status_code=404, detail="未找到该角色参考图")
        src.unlink()
        for old in THUMB_DIR.glob(f"{name}_*.jpg"):
            old.unlink(missing_ok=True)
        return {"success": True, "message": f"已删除 {name}"}

    app.include_router(router)
    log.info("[参考图库] /api/character-refs 已挂载，目录: %s", REF_DIR)
