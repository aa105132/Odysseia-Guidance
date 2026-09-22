"""无名杀只读宿主：真实静态资源与文件协议兼容性。"""

import asyncio
import importlib

import httpx
import pytest
from fastapi import FastAPI


host_module = importlib.import_module("src.chat.features.games.blackjack-web.noname_host")


@pytest.fixture
def assets(tmp_path):
    root = tmp_path / "game"
    root.mkdir()
    (root / "index.html").write_text("<title>无名杀</title>", encoding="utf-8")
    (root / "noname.js").write_text("export const name = '无名杀';", encoding="utf-8")
    (root / "extension").mkdir()
    (root / "extension" / "sample").mkdir()
    (root / ".secret").write_text("secret", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("outside", encoding="utf-8")
    return root


def run(assets, scenario):
    app = FastAPI()
    app.mount("/noname", host_module.create_noname_app(assets))

    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
            await scenario(client)

    asyncio.run(execute())


def test_static_and_browser_file_protocol(assets):
    async def scenario(client):
        index = await client.get("/noname/")
        assert index.status_code == 200 and "无名杀" in index.text
        assert (await client.get("/noname/noname.js")).status_code == 200
        result = await client.get("/noname/checkFile", params={"fileName": "noname.js"})
        assert result.json() == {"success": True, "code": 200, "data": "file"}
        result = await client.get("/noname/checkDir", params={"dir": "extension"})
        assert result.json()["data"] == "directory"
        result = await client.get("/noname/checkFile", params={"fileName": "absent.js"})
        assert result.json()["data"] is None
        listing = await client.get("/noname/getFileList", params={"dir": ""})
        assert listing.json()["data"] == {"folders": ["extension"], "files": ["index.html", "noname.js"]}
        text = await client.get("/noname/readFileAsText", params={"fileName": "noname.js"})
        binary = await client.get("/noname/readFile", params={"fileName": "noname.js"})
        assert bytes(binary.json()["data"]).decode("utf-8") == text.json()["data"]
    run(assets, scenario)


@pytest.mark.parametrize("path", ["../outside.txt", "..\\outside.txt", "/etc/passwd", "C:/Windows/win.ini", ".secret", "extension/../.secret", "noname.js\x00"])
def test_queries_cannot_escape_asset_directory(assets, path):
    async def scenario(client):
        for endpoint, field in [("readFile", "fileName"), ("checkFile", "fileName"), ("getFileList", "dir")]:
            response = await client.get(f"/noname/{endpoint}", params={field: path})
            assert response.status_code == 403
            assert response.json()["success"] is False
    run(assets, scenario)


def test_static_files_hide_secrets_and_write_routes_do_not_exist(assets):
    async def scenario(client):
        assert (await client.get("/noname/.secret")).status_code == 403
        assert (await client.get("/noname/%2e%2e/outside.txt")).status_code == 403
        for endpoint in ["removeFile", "removeDir", "createDir", "writeFile", "exec"]:
            result = await client.get(f"/noname/{endpoint}", params={"fileName": "noname.js", "dir": "extension"})
            assert result.status_code == 404
            assert (await client.post(f"/noname/{endpoint}", json={"path": "noname.js", "data": []})).status_code == 405
        assert (assets / "noname.js").read_text(encoding="utf-8").startswith("export")
    run(assets, scenario)


def test_large_read_is_bounded_and_invalid_text_is_reported(assets, monkeypatch):
    monkeypatch.setattr(host_module, "MAX_READ_BYTES", 4)
    (assets / "bad.txt").write_bytes(b"\xff")
    async def scenario(client):
        assert (await client.get("/noname/readFile", params={"fileName": "noname.js"})).status_code == 413
        assert (await client.get("/noname/readFileAsText", params={"fileName": "bad.txt"})).status_code == 400
        assert (await client.get("/noname/readFile", params={"fileName": "missing"})).status_code == 404
    run(assets, scenario)


def test_symbolic_links_cannot_publish_files_outside_assets(assets):
    try:
        (assets / "outside-link.txt").symlink_to(assets.parent / "outside.txt")
    except OSError:
        pytest.skip("当前系统不允许创建测试符号链接")

    async def scenario(client):
        assert (await client.get("/noname/outside-link.txt")).status_code == 403
        assert (await client.get("/noname/readFile", params={"fileName": "outside-link.txt"})).status_code == 403
        listing = await client.get("/noname/getFileList", params={"dir": ""})
        assert "outside-link.txt" not in listing.json()["data"]["files"]
    run(assets, scenario)


def test_missing_installation_is_explicit(tmp_path):
    async def scenario(client):
        response = await client.get("/noname/")
        assert response.status_code == 503
        assert "尚未安装" in response.json()["errorMsg"]
    run(tmp_path / "absent", scenario)


def test_precompiled_pnpm_modules_are_accessible(assets):
    module = assets / 'node_modules/.pnpm/vue/node_modules/vue/index.js'
    module.parent.mkdir(parents=True)
    module.write_text('export default {};', encoding='utf-8')
    async def scenario(client):
        response = await client.get('/noname/node_modules/.pnpm/vue/node_modules/vue/index.js')
        assert response.status_code == 200
        assert response.text == 'export default {};'
    run(assets, scenario)
