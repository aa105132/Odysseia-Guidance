"""无名杀浏览器资源的只读宿主，不提供上游文件写入和执行接口。"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


MAX_READ_BYTES = 8 * 1024 * 1024


def _asset_path(root: Path, value: str) -> Path:
    """同时约束查询接口和静态路径，禁止隐藏文件、越界及符号链接逃逸。"""
    normalized = value.replace("\\", "/")
    if "\x00" in normalized or ":" in normalized or normalized.startswith("/"):
        raise HTTPException(403, "只能访问游戏资源")
    if any(part.startswith(".") and part not in (".", ".pnpm") for part in normalized.split("/")):
        raise HTTPException(403, "只能访问游戏资源")
    target = (root / normalized).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(403, "只能访问游戏资源")
    return target


class ReadOnlyGameFiles(StaticFiles):
    async def get_response(self, path, scope):
        _asset_path(Path(self.directory).resolve(), path)
        return await super().get_response(path, scope)


def create_noname_app(asset_dir: str | Path) -> FastAPI:
    """挂载至 /noname；资源必须是独立发布目录，不能指向整个仓库。"""
    root = Path(asset_dir).resolve()
    host = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @host.exception_handler(StarletteHTTPException)
    async def http_error(_request, exc):
        return JSONResponse(
            {"success": False, "code": exc.status_code, "errorMsg": str(exc.detail)},
            status_code=exc.status_code,
        )

    def ready():
        if not (root / "index.html").is_file():
            raise HTTPException(503, "三国杀资源尚未安装")

    def success(data):
        return {"success": True, "code": 200, "data": data}

    def kind(value):
        ready()
        target = _asset_path(root, value)
        if target.is_file():
            return "file"
        if target.is_dir():
            return "directory"
        return None

    @host.get("/checkFile")
    def check_file(fileName: str = Query(max_length=1024)):
        return success(kind(fileName))

    @host.get("/checkDir")
    def check_dir(dir: str = Query(max_length=1024)):
        return success(kind(dir))

    @host.get("/getFileList")
    def list_files(dir: str = Query(max_length=1024)):
        ready()
        target = _asset_path(root, dir)
        if not target.is_dir():
            raise HTTPException(404, "资源目录不存在")
        folders, files = [], []
        for child in sorted(target.iterdir()):
            if child.name.startswith((".", "_")) or not child.resolve().is_relative_to(root):
                continue
            if child.is_dir():
                folders.append(child.name)
            elif child.is_file():
                files.append(child.name)
        return success({"folders": folders, "files": files})

    def read(file_name):
        ready()
        target = _asset_path(root, file_name)
        if not target.is_file():
            raise HTTPException(404, "资源文件不存在")
        # 大型图片和音频直接使用静态地址，避免 JSON 字节数组占用过多内存。
        with target.open("rb") as source:
            content = source.read(MAX_READ_BYTES + 1)
        if len(content) > MAX_READ_BYTES:
            raise HTTPException(413, "资源过大，请使用静态地址读取")
        return content

    @host.get("/readFile")
    def read_file(fileName: str = Query(max_length=1024)):
        return success(list(read(fileName)))

    @host.get("/readFileAsText")
    def read_text(fileName: str = Query(max_length=1024)):
        try:
            return success(read(fileName).decode("utf-8"))
        except UnicodeDecodeError:
            raise HTTPException(400, "资源不是 UTF-8 文本")

    if root.is_dir():
        host.mount("/", ReadOnlyGameFiles(directory=root, html=True), name="noname-assets")
    else:
        @host.get("/{path:path}")
        def unavailable(path: str):
            raise HTTPException(503, "三国杀资源尚未安装")

    return host
