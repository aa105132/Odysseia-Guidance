"""Read-only PokéRogue runtime; no SPA fallback for missing game assets."""
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse

POKEROGUE_DIST_DIR = Path(os.environ.get("POKEROGUE_DIST_DIR", "").strip() or Path(__file__).with_name("pokerogue-dist"))
BRIDGE_PATH = Path(__file__).with_name("pokerogue") / "cloud-bridge.js"


def pokerogue_available(root: Path = POKEROGUE_DIST_DIR) -> bool:
    return (root / "index.html").is_file() and (root / "assets").is_dir() and BRIDGE_PATH.is_file()


def create_pokerogue_app(root: Path = POKEROGUE_DIST_DIR):
    root = Path(root).resolve()
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.api_route("/{asset_path:path}", methods=["GET", "HEAD"])
    async def asset(asset_path: str, request: Request):
        if asset_path == "cloud-bridge.js":
            return FileResponse(BRIDGE_PATH, media_type="application/javascript", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        if not pokerogue_available(root):
            raise HTTPException(503, "宝可梦资源尚未安装完整，请稍后再试")
        relative = asset_path or "index.html"
        if "\\" in relative or any(part.startswith(".") for part in relative.split("/")):
            raise HTTPException(404, "资源不存在")
        path = (root / relative).resolve()
        if path.is_relative_to(root) and path.is_dir():
            path = (path / "index.html").resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise HTTPException(404, "资源不存在")
        # Revalidate mutable manifests and source links; filenames need not be hashed.
        return FileResponse(path, headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})

    return app
