# 三国杀独立子应用

固定上游：<https://github.com/libnoname/noname>，提交 `1bbf1759962f326e2c53d672b16ba0e214918c2d`（1.11.6）。上游 GPL-3.0，本仓库 AGPL-3.0；保留 LICENSE、README、本精简构建对应源码和适配脚本，不附带全量游戏素材。README 另声明请勿商业使用，素材并不因为代码开源而取得额外授权。

## 本地构建

需要 Node.js 22.12+、pnpm 9+。上游开发 checkout 仍用于编译，发布目录按白名单独立生成，不复制完整资源包。输出目录必须为空：

```powershell
git clone https://github.com/libnoname/noname.git tmp/noname-upstream
git -C tmp/noname-upstream checkout 1bbf1759962f326e2c53d672b16ba0e214918c2d
Push-Location tmp/noname-upstream
pnpm --filter 'noname...' --filter '@noname/server' install --ignore-scripts
pnpm --filter . install --ignore-scripts
pnpm --filter 'noname...' --filter '@noname/server' -r build
Pop-Location
node src/chat/features/games/blackjack-web/noname/prepare.mjs tmp/noname-upstream data/noname/classic-dist
$env:NONAME_DIST_DIR = (Resolve-Path data/noname/classic-dist).Path
node tmp/noname-upstream/packages/server/dist/activity-server.cjs --port 8082
```

上游开发目录仍需足够磁盘空间，但发布包只包含经典模式所需文件；`classic-manifest.json` 记录文件清单与字节数。大厅构建不复制子游戏资源。安装 Python `requirements.txt` 后启动游戏 FastAPI，自动挂载 `/noname/`。用 `NONAME_DIST_DIR` 指向精简目录，`NONAME_WS_URL` 设置内部联机地址（默认 `ws://127.0.0.1:8082`）。不要公开原生 8082 端口。

`upstream-source.tar.gz` 是源码子集，不是完整素材包；包含共享引擎、构建工具及标准武将借用技能的 refresh/sp 源码，运行时不会加载这两个扩展包。`integration-source/classic-skill-excerpts.js` 保存实际提取的四个技能。重建目前使用上面固定提交的 checkout；未宣称仅解压源码子集即可离线独立重建，所需白名单素材来自同一固定提交。

## 适配范围

- 同源独立 iframe，仅保留经典身份（`identity/normal`）、标准武将与标准牌；父页通过消息传入显示昵称。昵称是显示信息，不作为钱包身份。
- 启动前显示 GPL 许可确认，避免 Discord 拦截 iframe 原生确认框。联机强制使用上游隔离沙盒，不要求信任房主代码。
- 使用预编译脚本，不注册上游即时编译 Service Worker；浏览器端动态安装 TS 扩展不在支持范围。
- 文件协议仅提供发布目录内的只读资源访问，不运行上游文件写入服务。
- 每次联机从已认证父页面申请 60 秒一次性票据；不向子页面传 Discord Bearer Token。返回大厅后子页面卸载；房主离开会解散其房间。
- 单机和好友联机均固定经典标准包，移除扩展预设；加载旧配置后固定普通身份子模式。旧续局/直开标记不会恢复其他模式。失效扩展皮肤、壁纸、背景音乐和字体回到默认，战绩、音量及数据库里的用户自定义资源不整库清除。
- 好友联机自动连接内部大厅，外层提供创建房间、实时房间列表、复制房号、房号加入和房主开局；保留 2/4/6/8 人。至少两名真人，空位由上游电脑补齐。列表和房号加入只接受已声明普通身份与标准双包的房间；旧扩展房不再可加入。
- 房间数据仍来自无名杀联机服务，使用其独立房号，没有接灵石、现有六位房间号和 Discord 指定房间邀请；房主浏览器控制规则，不能拿其上报胜负结算灵石。多人入座即表示可以由房主开局，暂不额外提供准备开关。

## 验证边界

本地精简包已通过真实引擎单机选将发牌、双客户端建房/加入/双方发牌（两真人加两 AI）、房主退出，以及旧扩展配置和房间拒绝测试；该次运行无页面异常、无资源 404。全部 33 名标准武将的技能、继承及分组依赖存在性检查通过，不等于每项技能的完整对局语义测试。配置/白名单单测 6 项、Python 网关及只读资源测试 20 项、大厅入口测试 2 项及类型检查/构建通过。真实 Discord WebView、完整多局技能和断线恢复仍未全面验收；没有部署生产。

本地测试宿主启动后，可用 `node src/chat/features/games/blackjack-web/noname/smoke.mjs 'http://127.0.0.1:8491/?dev_user_id=123456789012345678'` 重跑真实双客户端联机验收，最长 60 秒。此脚本只允许本机地址，测试宿主需提供开发身份认证；正式部署不要启用开发认证。网关允许同源请求和配置的 Discord 应用 ID 对应的 `https://<ID>.discordsays.com` 来源，不接受其他活动域名。

### 经典模式回归

仓库根运行配置测试：

```powershell
node --test src/chat/features/games/blackjack-web/noname/classic-policy.test.mjs src/chat/features/games/blackjack-web/noname/classic-build.test.mjs
```

更新时必须同时发布大厅构建与 `prepare.mjs` 生成的精简子游戏资源（适配版本 2）。只更新 Vue 入口不能限制内嵌游戏。运行资源不含其他模式、扩展卡包/武将/皮肤、全量音频及重复的 `dist/src`；保留共享引擎、许可证和源码子集。不要把新文件覆盖进旧完整目录后当成精简发布包。清单的 `bytes` 不包含清单文件本身。

独立的本地测试宿主及联机进程启动后，运行真实单机/双客户端回归（输出目录使用会话 scratch）：

```powershell
node src/chat/features/games/blackjack-web/noname/classic-smoke.mjs 'http://127.0.0.1:8492/?dev_user_id=123456789012345678' "$env:PI_SCRATCH_DIR/noname-classic"
```

该脚本使用隔离浏览器上下文，注入旧国战/扩展配置及一个合成扩展房列表，核对加载包、经典子模式、拒绝扩展房、两人选将发牌及房主退出。它不是生产部署或真实 Discord 验收，也不是对自行篡改客户端的反作弊边界。
