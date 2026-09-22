# 三国杀独立子应用

固定上游：<https://github.com/libnoname/noname>，提交 `1bbf1759962f326e2c53d672b16ba0e214918c2d`（1.11.6）。上游 GPL-3.0，本仓库 AGPL-3.0；保留上游 LICENSE、README、完整对应源码归档和适配脚本。README 另声明请勿商业使用，素材并不因为代码开源而取得额外授权。

## 本地构建

需要 Node.js 22.12+、pnpm 9+。在仓库根运行，所有产物仅在指定源码目录及 `data/noname/dist` 下：

```powershell
git clone https://github.com/libnoname/noname.git tmp/noname-upstream
git -C tmp/noname-upstream checkout 1bbf1759962f326e2c53d672b16ba0e214918c2d
Push-Location tmp/noname-upstream
pnpm --filter 'noname...' --filter '@noname/server' install --ignore-scripts
pnpm --filter . install --ignore-scripts
pnpm --filter 'noname...' --filter '@noname/server' -r build
Pop-Location
node src/chat/features/games/blackjack-web/noname/prepare.mjs tmp/noname-upstream data/noname/dist
node tmp/noname-upstream/packages/server/dist/activity-server.cjs --port 8082
```

资源和源码归档很大，应预留数 GB 空间。大厅构建不复制这些资源。安装 Python `requirements.txt` 后启动游戏 FastAPI，自动挂载 `/noname/`。`NONAME_DIST_DIR` 可改变发布目录，`NONAME_WS_URL` 可改变内部联机地址；默认 `ws://127.0.0.1:8082`。Docker 内网关必须能访问该地址；节点进程可运行在同容器或私有网络，不应公开原生 8082 端口。

## 适配范围

- 同源独立 iframe，保留原版武将、牌和界面；父页通过消息传入显示昵称。昵称是显示信息，不作为钱包身份。
- 启动前显示 GPL 许可确认，避免 Discord 拦截 iframe 原生确认框。联机强制使用上游隔离沙盒，不要求信任房主代码。
- 使用预编译脚本，不注册上游即时编译 Service Worker；浏览器端动态安装 TS 扩展不在支持范围。
- 文件协议仅提供发布目录内的只读资源访问，不运行上游文件写入服务。
- 每次联机从已认证父页面申请 60 秒一次性票据；不向子页面传 Discord Bearer Token。返回大厅后子页面卸载；房主离开会解散其房间。
- 房间列表是无名杀自己的联机大厅。没有接灵石、现有六位房间号和 Discord 指定房间邀请；房主浏览器控制规则，不能拿其上报胜负结算灵石。

## 验证边界

已在本地真实上游构建验证单机选将与发牌、两客户端创建房间、加入、选将及双方发牌。网关真实 WebSocket 回显及票据、来源、只读路径边界测试通过。真实 Discord WebView、完整多局技能和断线恢复仍需进一步验收，不据此声称已验证全部武将。

本地测试宿主启动后，可用 `node src/chat/features/games/blackjack-web/noname/smoke.mjs 'http://127.0.0.1:8491/?dev_user_id=123456789012345678'` 重跑真实双客户端联机验收，最长 60 秒。此脚本只允许本机地址，测试宿主需提供开发身份认证；正式部署不要启用开发认证。网关允许同源请求和配置的 Discord 应用 ID 对应的 `https://<ID>.discordsays.com` 来源，不接受其他活动域名。
