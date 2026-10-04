# 月月大厅 · PokéRogue 云存档接入

PokéRogue 单人冒险独立运行，进度使用月月大厅账号云存档，不使用官网账号，不扣灵石。本轮不包含多人对战或组队。

## 部署前提

必须同时部署大厅前后端、`pokerogue/cloud-bridge.js`、完整独立游戏产物。默认产物目录为 `blackjack-web/pokerogue-dist/`，也可设置服务端环境变量 `POKEROGUE_DIST_DIR` 指向独立目录。该目录未纳入 Git，不会随源码提交自动发布。游戏包必须使用本项目构建适配，普通官网构建没有云存档启动门控，不能直接替代。

代理必须将 `/pokerogue/` 和 `/api/pokerogue/` 转发至大厅后端；不要把 `/pokerogue/` 重写到大厅 `index.html`。若 Nginx/CDN 默认限制请求为 1 MiB，需要为存档接口允许至少 8 MiB 请求体；后端仍严格限制为 8 MiB。SQLite 应放在持久化卷中，沿用现有 `chat_db_manager.db_path` 并纳入数据库备份。

代码适用 AGPL，素材各自许可及宝可梦知识产权不是同一授权；保留许可证、署名和对应源码下载，不接付费素材售卖。

## 验证安装

1. 大厅 `/api/config` 的 `pokerogue_available` 为 true 时展示入口。
2. 登录后 `/api/pokerogue/status` 应返回 `available: true`。
3. 点击大厅「宝可梦」即自动检查资源、读取账号存档并启动原版引擎，不再经过自建启动页。第一次游玩显示原版教程是正常情况。
4. 进度变化后等待「云存档已同步」，用「返回大厅 → 同步并返回」退出。
5. 同账号在另一浏览器进入，检查恢复；不同账号应为独立进度。

上游独立构建的固定版本、生成命令和资源清单见仓库 `scripts/pokerogue/`。不要在大厅 `public/` 放入整套游戏素材，否则每次大厅构建都将重复复制几百 MiB 文件。

在仓库根目录执行 `node scripts/pokerogue/build.mjs` 完整构建。若编译成功但源码打包/发布中断，保留原 `PI_SCRATCH_DIR` 后执行 `node scripts/pokerogue/package.mjs`，只重新打包与发布，不重新下载或编译。脚本校验固定版本及云启动门控，先写 `.partial` 压缩包并完整读取校验，再通过暂存目录发布，避免半成品被大厅读取。不要删除 scratch 中的源码再尝试恢复。

`node scripts/pokerogue/verify.mjs` 验证真实 Phaser 中文启动、资源路径以及云启动门控。简体中文代码是 `zh-Hans`，不是 `zh-CN`。

## 原版游戏界面

大厅只保留一个「宝可梦」入口。游戏内的图鉴、成就、个人统计、历史战绩、存档管理、孵蛋相关和设置均沿用原版菜单；外层只保留云同步状态、返回大厅和必要的加载/错误提示。Discord 控件安全区继续保留，使用游戏深色背景而不是茶楼背景。

此前自建的档案/排行榜网页和大厅统计面板入口已撤下，不再加载。跨玩家榜单改为游戏原版菜单中的「本服排行榜」，使用 Phaser 原生像素窗口，读取本大厅的认证接口，不连接官方榜单。包含无尽最高层、经典通关、图鉴收集、每日累计通关；支持分页、本人排名和失败重试。榜单依据云存档快照，标注「存档记录·非反作弊认证」，没有灵石奖励。图鉴包含初始拥有的宝可梦；每日榜是累计通关次数，不是今天的分数。旧账号摘要按请求分批补齐，迁移期间榜单人数可能逐步增加。

打开方式：游戏中按 `Esc/M` 打开原版菜单，选择统计下方的「本服排行榜」。默认 `空格/Z` 确认，`退格/X` 返回；榜单内 `←→` 切换分类、`↑↓` 翻页、确认键刷新或重试。触屏沿用原版方向键与 A/B，不增加网页按钮。

每日挑战在游戏「新游戏 → 每日挑战」开始。新局通过大厅服务器获取 UTC 日期的 base64 种子；失败显示重试，不使用本机日期。继续已有冒险沿用云档种子。无尽、融合无尽等仍按原版通关条件解锁，未强行开放；离线构建附带的 Admin 菜单已关闭。官方账号、官方挑战榜、多人/交易不在本接入范围。

图鉴、糖果、蛋券、成就、蛋、游戏设置、按键及触屏位置和五存档槽均由同一云档保存。保留游戏原生导入/导出和重置行为：这些操作会改变当前账号云档，请先在游戏内备份。

## 云存档行为

| 场景 | 行为 |
| --- | --- |
| 进入游戏 | 从服务端读取完整快照，成功后才加载游戏模块 |
| 游戏生成存档 | 300ms 合并窗口后串行上传完整快照，包含删除操作 |
| 返回大厅 | 等待已生成的存档上传成功后卸载 iframe；不是强制为任意战斗帧生成新保存节点 |
| 换账号 | 新 iframe 内存存储重新从当前服务端账号加载，不读取浏览器已有 localStorage |
| 多设备同时写 | 服务端版本比较；旧版本返回 409，不自动覆盖最新进度 |
| 响应丢失 | 读取云端，只有内容与刚发送快照完全相同时才确认成功 |
| 网络失败 | 保留当前内存状态，显示错误遮罩和重试；可下载未同步快照备份 |
| 主动放弃未同步进度 | 二次确认后回大厅，不删除服务器已有存档 |
| 直接访问游戏地址 | 没有大厅消息桥时不启动，提示从大厅进入 |

云存档只存储游戏字符串，不能作为未来多人竞技中的可信战斗结果。客户端会持有当前进度的内存副本；关闭浏览器前尚未上传的变更不保证保留。备份下载为适配器 JSON 快照，不是上游 `.prsv` 导入格式；发生冲突时可留档交管理员核对，当前界面不提供强制覆盖云档。

## 接口与接线

`pokerogue_cloud.create_pokerogue_router(get_current_user_profile, db_path_provider)` 复用现有认证。身份只从服务端认证依赖取得，客户端传入 user_id 不参与选档。

- `GET /api/pokerogue/save` → `{ "revision": 0, "entries": {} }`（新账号）。
- `PUT /api/pokerogue/save`，请求 `{ "revision": 0, "entries": { "data_Guest": "..." } }` → 新版本和完整快照。
- `409`：版本过期；`413`：超过体积限制；`422`：格式、字段或存储键不合法；`503`：存储暂不可用。
- 数据表：`pokerogue_cloud_saves(user_id, revision, entries_json, updated_at)`，参数化 SQL 与 `BEGIN IMMEDIATE` 原子比较更新。
- `GET /api/pokerogue/profile`：当前账号档案，包含 `trust: "client-save"`、版本、统计、槽位、历史、成就、蛋及每日状态。
- `GET /api/pokerogue/leaderboard?metric=endless&limit=20&offset=0`：`metric` 可为 `endless|wins|collection|daily`；limit 1–100，offset 0–10000。仅返回名次、昵称、数值及本人标记，不公开账号 ID 或原始存档。
- `GET /api/pokerogue/daily-seed`：`{date, seed, resets_at}`，全部使用服务器 UTC；三个新增接口均需大厅认证。
- `pokerogue_records` 摘要索引与成功 CAS 保存同事务更新；解析限制 2 MiB 编码值、32 层 JSON 深度和 10 万节点。格式异常保留原始云档保存能力，档案返回警告，异常系统档不参加排名。

大厅与同源 iframe 通过 `postMessage` 通信，两端检查 origin 与 source。OAuth token 不发送到游戏消息中。此通信约束不是同源第三方代码的安全沙箱；仅部署已固定并审查的游戏构建。

## 本地回归

仓库根目录：

```powershell
python -m pytest tests/test_pokerogue_cloud.py tests/test_pokerogue_records.py tests/test_pokerogue_host.py tests/test_farm_api.py -q
node --check src/chat/features/games/blackjack-web/pokerogue/cloud-bridge.js
Set-Location src/chat/features/games/blackjack-web
npm run build
npx playwright test tests/pokerogue.spec.ts tests/game-stats.spec.ts tests/noname.spec.ts tests/lobby-grid.spec.ts tests/game-audio.spec.ts tests/game-audio-queue.spec.ts
```

浏览器的真实 SQLite 联调测试需要 `POKEROGUE_TEST_API` 指向**隔离测试服务**，该服务使用测试头 `X-Test-User` 选择 1/2 两个虚拟账号。未提供时该项会跳过，不应将此变量指向生产服务。其余浏览器测试使用模拟 API 和真实云存档桥；它们不等于完整游戏实机验收。

已构建游戏时，再设置 `POKEROGUE_REAL_RUNTIME=1` 可启用真实引擎测试（仍须提供上述隔离服务），覆盖中文启动、素材加载、真实 SQLite 写入、同步退出和重新进入。完整战斗及长局续档仍需游戏内人工验收；测试启动成功不等于所有玩法已遍历。

2026-09-26 本地验收：120 项后端测试、39 项浏览器测试（含真实 SQLite 与真实引擎）通过；大厅 TypeScript/Vite 构建、独立引擎构建、源码包完整性及启动门控验证通过。实际每日挑战开局与重新进入已验证：模式 3、第 1 层、服务器种子 `MjAyNi0wOS0yNg==`；续档不重复获取种子，档案解析无警告。图鉴养成/设置等跨浏览器覆盖为云档字段整体恢复测试，并非逐个稀有道具/完整孵化周期实玩。未穷尽通关、长局和所有宝可梦组合。

画面证据见 [单人接入验收截图](features-screenshots.html)。当前仅本地完成，未提交或部署生产环境。

后续按用户要求简化为单入口直达：39 项浏览器回归（含真实引擎与 SQLite）和大厅构建通过；旧界面截图仅为历史验收记录，当前画面见 [直达原版截图](direct-screenshots.html)。

本服排行榜追加验收：42 项浏览器测试全部通过（含真实原版菜单键盘导航、四分类、翻页、错误重试、返回重开、真实 SQLite 与引擎），120 项后端测试通过；大厅构建、独立引擎构建和启动门控验证通过。1440×900 与 568×320 截图见 [原版榜单验收截图](native-screenshots.html)，其中模拟玩家截图仅用于布局/分页测试，不代表真实运营数据。未单独完成触屏点击实测。
