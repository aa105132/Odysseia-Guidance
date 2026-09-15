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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/character-refs")

REF_DIR = Path(os.getenv("CHARACTER_REF_DIR", "/app/data/character_refs"))
THUMB_DIR = REF_DIR / ".thumbs"
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20MB，防御性上限
NAME_RE = re.compile(r"^[\w\u4e00-\u9fa5·\-（）()]{1,32}$")
# register() 时由 api.py 传入（闭包捕获），用于 ?token= 兜底校验
DASHBOARD_SECRET_REF = ""


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


def register(app, verify_token, dashboard_secret: str = ""):
    """挂到主 FastAPI app；鉴权与既有端点一致。
    图片类端点（thumb/原图）支持 ?token= 查询参数兜底——浏览器 <img> 标签
    发不了 Authorization 头，不放开 query 的话缩略图全是 401 裂图。"""
    import asyncio
    from fastapi import Query

    global DASHBOARD_SECRET_REF
    DASHBOARD_SECRET_REF = dashboard_secret

    _query_security = HTTPBearer(auto_error=False)

    def _verify_flex(
        credentials: HTTPAuthorizationCredentials = Depends(_query_security),
        token: str = Query(""),
    ):
        """Bearer 头优先，?token= 兜底（<img> 标签用）。"""
        if credentials is not None and credentials.credentials == DASHBOARD_SECRET_REF:
            return credentials.credentials
        if token and token == DASHBOARD_SECRET_REF:
            return token
        raise HTTPException(status_code=401, detail="无效的认证令牌")

    @router.get("")
    async def list_refs(token: str = Depends(verify_token)):
        return {"items": await asyncio.to_thread(_list_dir)}

    @router.get("/{name}/thumb")
    async def get_thumb(
        name: str,
        token: str = Depends(_verify_flex),
    ):
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
    async def get_ref(name: str, token: str = Depends(_verify_flex)):
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
        if name == "import-zip":
            # 交给下方批量导入端点（/{name} 先注册会截胡，这里转发）
            return await import_zip(file=file, token=token)
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

    @router.post("/import-zip")
    async def import_zip(
        file: UploadFile = File(...),
        token: str = Depends(verify_token),
    ):
        """批量导入：zip 包内顶层图片按文件名（去扩展）当角色名入库。
        跳过目录项/非图片/非法名；同名覆盖。zip 单文件上限 500MB。"""
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="空文件")
        if len(data) > 500 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="zip 上限 500MB")

        import zipfile
        from datetime import datetime as _dt

        results: List[Dict[str, Any]] = []
        skipped: List[str] = []
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except Exception:
            raise HTTPException(status_code=400, detail="不是有效的 zip 文件")

        # zip 内中文名编码修复：Windows 打的包没有 UTF-8 标志位（0x800）时，文件名是 cp437 毛刺，还原成 GBK
        def _fix_zipname(info: zipfile.ZipInfo) -> str:
            n = info.filename
            if info.flag_bits & 0x800:  # 已声明 UTF-8
                return n
            try:
                return n.encode("cp437").decode("gbk")
            except Exception:
                return n

        REF_DIR.mkdir(parents=True, exist_ok=True)
        with zf:
            names = zf.namelist()
            for info in zf.infolist():
                if info.is_dir():
                    continue
                raw = _fix_zipname(info)
                base = os.path.basename(raw)
                stem, ext = os.path.splitext(base)
                if ext.lower() not in ALLOWED_EXT:
                    skipped.append(f"{base}（不是图片）")
                    continue
                try:
                    name = _safe_name(stem)
                except HTTPException:
                    skipped.append(f"{base}（角色名不合规）")
                    continue
                try:
                    img_bytes = zf.read(info)
                    from PIL import Image

                    img = Image.open(io.BytesIO(img_bytes))
                    img.load()
                except Exception:
                    skipped.append(f"{base}（损坏/非图片）")
                    continue

                for old in REF_DIR.glob(f"{name}.*"):
                    if old.suffix.lower() in ALLOWED_EXT:
                        old.unlink()
                dst = REF_DIR / f"{name}.png"

                def _save_zip(img=img, dst=dst):
                    if img.mode == "P" and getattr(img, "is_animated", False):
                        img.save(dst, format="GIF")
                    else:
                        if img.mode not in ("RGB", "RGBA", "L"):
                            img = img.convert("RGBA")
                        img.save(dst, format="PNG")

                await asyncio.to_thread(_save_zip)
                results.append({
                    "name": name,
                    "size": dst.stat().st_size,
                    "zip_mtime": (
                        _dt(*info.date_time).timestamp() if info.date_time else time.time()
                    ),
                })

        return {
            "success": True,
            "imported": len(results),
            "items": results,
            "skipped": skipped,
            "message": f"导入 {len(results)} 个角色，跳过 {len(skipped)} 个文件",
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
