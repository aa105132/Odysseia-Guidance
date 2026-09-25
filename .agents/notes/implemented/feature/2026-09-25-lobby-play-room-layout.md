# Agent Note: 月月大厅玩法区与找房布局

Status: implemented

## Problem

用户认可生成的现代大厅参考图，要求将实际 Discord Activity 大厅改为对应布局。旧大厅将辅助功能与游戏混排，房间列表主要通过独立弹窗进入，选玩法与找房间的路径分离。参考图曾出现炸金花、掼蛋置于辅助区域及灵田入口重复的问题，实际实现必须避免。

用户随后指出首版实装虽然功能通过，但与确认稿不相似：大幅人物图叠深色大框和均匀矩形卡片，丢失了参考稿的横条入口、明亮色彩和装饰边框。功能测试通过不构成视觉相似度验收；本次按用户要求保留图标，重做卡片与周边 UI。

用户进一步要求二级模式、场次与同桌方式也沿用大厅场景，仅动画切换右侧 UI，而不是进入另一张全屏选择页。

后续用户明确否定二级加宽、选择区滚动、遮住人物的容器背景，以及桌面把卡片拉高拉宽。最终要求：外壳几何固定且透明，桌面也用认可的 Discord·麻将紧凑比例；21点房间设置改为居中小弹窗，不再内联堆在场景上。以下 Decision 为最终状态，早期验证小节仅作历史证据。

## Decision

- 大厅采用左侧辅助导航、中间人物场景、右侧玩法区。21点、德州扑克、斗地主、四人麻将、炸金花、掼蛋全部属于主玩法区；可用时三国杀为第七张卡。
- 房间列表在主玩法区内切换，不再通过 `rooms-open` 加宽右区；外壳透明，保留行/控件背景。复用 RoomDirectory 查询、筛选、轮询、准入与入座逻辑；其他位置继续使用原弹窗。可变长度房间结果列表可以内部滚动，不等于选择面板允许滚动。
- 修仙灵圃仅保留一个大厅入口，和战绩、排行榜位于辅助导航，不占棋牌游戏卡片。
- 背景使用独立新生成的 `/ui/lobby/yueyue-teahouse.webp`，不是概念截图。图片不包含文字、按钮、边框或计数器，所有 UI 仍由 Vue/CSS 渲染。采用桌前半身构图，脚部不进入画面。
- 保留 Discord 可用视口、横竖旋转和月月点击招手。使用命名容器查询适配逻辑窗口，不改牌局、钱包、战绩或农场业务；保留任务开始前的未提交修改。
- 桌面主区采用四条长卡和底部炸金花/掼蛋双卡，右区约占视口 37%–43%；紧凑逻辑高度 ≤600px 使用两列三行，第七种玩法单独一行。移除玩法区整体深色外框，让独立场景露出。
- 卡片使用饱和红、蓝、绿、金、紫、青底色，叠加低对比织纹、九宫格双层金边、角花和圆形金色箭头；侧栏、切换按钮、头像框沿用红金材质。现有图标放大但不替换。
- 二级选择使用常驻大厅外壳与右侧 `Transition`（前进/返回方向相反，150ms 淡入淡出位移）。21点模式、桌游场次/麻将变体/同桌方式、可用时三国杀启动/联机选择在右区切换；实际入桌才全屏。21点多人设置是用户明确要求的原生 `dialog`，不再是右区内联表单。
- TableGames/NonameGame 通过 `roomActive` 通知父级切换布局。同一个 GameViewport/游戏组件分支保持挂载，不按是否入桌重新建组件，避免重复恢复/加入/轮询，离桌后仍保留选择。桌游请求中禁止从侧栏进入农场。
- 一级/二级的 `.lobby-gameplay`、stage、场景和侧栏边界保持一致，移除 secondary/rooms 加宽。容器无背景、阴影或外框，只有按钮/卡片/条目填色。桌游和21点模式内容上限 440×340，靠右居中；空余区域留白，不用拉伸或整体缩放填满。
- 二级样式限定在 `embedded-selection`，不改真实牌桌。主选择控件同时可见，不设下滑提示，不以裁切或测试自动滚动掩盖溢出；陪玩勾选与等待时长放到“入席设置”，沿用同一状态与请求参数。失败提示进入正常流，不盖住重试按钮。
- 21点设置弹窗最大宽 430px，随 Activity 可用尺寸收缩，使用既有原生弹窗物理中心和旋转规则；集中建房时长、房间号、提交和房间列表。关闭/Esc 保留输入并返回“多人对战”焦点；异步 Discord 自动连接失败后也显式回焦。请求中禁用关闭和重复提交，成功入桌自动关闭。
- Discord 多人入口仍保留已有房间恢复和当前会话自动连接；成功可直接入桌，失败才显示设置。前往房间列表先关闭设置，避免叠加遮罩；错误/状态显示在设置内，不重复显示到全局。模式面板使用同一 key，焦点动画不得抢走弹窗焦点。只保留一个可交互月月，尊重减少动画偏好。

## Assets and implementation

背景原始 PNG 保存在 `screenshots/lobby-redesign/yueyue-teahouse-source.png`；正式 WebP 为 1672×941、208814 字节，来源与 SHA-256 位于 `public/ui/lobby/manifest.json`（相对 blackjack-web）。沿用 Codex 线程 `01a0c854-22ea-74b0-96ce-006b128e8616` 中场景与 UI 素材分开生成、压缩、维护清单的方式，复用 `farm-v2/games/*.webp`，不覆盖旧资源。

`src/App.vue` 负责布局与现有事件绑定，`src/lobby-hub.css` 仅限定大厅，`src/RoomDirectory.vue` 保留嵌入样式。卡片重制这一轮未修改 App.vue 或 RoomDirectory.vue；App.vue 与轮次开始前备份 SHA-256 一致。新增 code-native `card-frame.svg`（九宫格金边）与 `card-brocade.svg`（重复底纹）。最小逻辑窗口 568×320 实测六卡约 169×63px，七卡约 170×47px，仍高于 44px 点击高度下限。

二级改版涉及 `src/App.vue`、`src/TableGames.vue`、`src/NonameGame.vue`，新增 `src/lobby-table-select.css`。`TableGames` 原来的恢复、离桌与轮询仍是唯一业务实现，`roomActive`/`entryBusy` 仅传递呈现状态。不是把组件移动到不同父级，也不以房间状态改变 key。

## Verification

在 `src/chat/features/games/blackjack-web` 执行：

```powershell
npm run typecheck
npx playwright test tests/lobby-grid.spec.ts tests/lobby-window-fit.spec.ts tests/room-directory.spec.ts --output="$env:PI_SCRATCH_DIR\card-polish-tests-final" --global-timeout=180000
npm run build -- --outDir "$env:PI_SCRATCH_DIR\card-polish-build"
```

类型检查通过；本轮 29/29 Playwright 用例通过；生产构建退出码 0。覆盖六/七卡、1920×1080 至 568×320、Discord 工具条、390×844 旋转、连续缩放、按钮与文案边界、单一灵圃入口、背景引用、月月交互、房间筛选/入座/失败/分页/轮询清理及原弹窗。新增断言验证桌面横条长宽比、四条长卡与底部双卡排布、独立金边/纹理资源、箭头可见、移除外层背景、减少动画和键盘 Enter 进入游戏。10 组额外窗口诊断无失败。`git diff --check` 通过。

此前人工检查过背景全图及放大的手部/手腕。本轮人工查看桌面、Discord 横屏、568×320 和横屏房间列表实装截图；卡片角花、金边、底纹和箭头已实际显示。当前截图在 `screenshots/lobby-redesign/cards-v2-review.html` 与 `cards-v2-compact-review.html`。原参考与被否定首版的对照保存在 `reference-comparison.html`，不能将首版测试通过视为用户认可外观。测试使用模拟 API/Discord SDK；没有真实 Discord 客户端、生产多人后端验收或部署。

### Secondary panel verification

新增 `tests/lobby-secondary.spec.ts`：7 项用例覆盖五种桌游、麻将变体、21点二/三级设置、三国杀启用入口、四组窗口（含 Discord 工具条及旋转）、原大厅节点身份、入桌/离桌组件身份与选项保留、失败保留选择、请求参数与无额外 join、实际 transitionrun、减少动画和键盘返回焦点。均通过。类型检查和最终生产构建通过，`git -c core.safecrlf=false diff --check` 退出码 0。

扩展批次运行 65 项，初次最终结果为 63 通过、2 失败：房间列表旋转窗口轮询测试原样复跑 3 次通过；其虚拟时钟安装晚于组件定时器，现移至打开列表之前。另一条 `sichuan-mahjong.spec.ts` 的“本人已胡只读”用例在结算后检查 south 玩家头像四角时失败。通过 scratch Vite load 插件载入 `before-secondary/App.vue`，修改前的全屏选择结构也复现同一失败；不修改麻将牌桌或削弱遮挡断言。此既有问题仍未解决。


时钟安装时机修正后，最终大厅/二级/房间限定回归 **36/36 通过**（`secondary-scoped-final.log`），重新执行类型检查通过；最终代码构建输出为 scratch `secondary-build-final`。命令：

```powershell
npm run typecheck
npx playwright test tests/lobby-grid.spec.ts tests/lobby-window-fit.spec.ts tests/room-directory.spec.ts tests/lobby-secondary.spec.ts --output="$env:PI_SCRATCH_DIR\secondary-scoped-final" --global-timeout=150000
npm run build -- --outDir "$env:PI_SCRATCH_DIR\secondary-build-final"
```

扩展回归的麻将既有失败依然保留，不将其统计为通过。最小窗口实图另存 `secondary-compact-review.html`。
本轮实图位于 `screenshots/lobby-redesign/secondary-desktop-review.html`（麻将、21点模式及多人设置）与 `secondary-review.html`（Discord 横屏）。实际查看截图后修正了旧模板重复月月、旧 flex-direction 导致模式卡竖排，以及小屏说明占位问题。截图是模拟 API/SDK 的真实浏览器渲染，不是新的概念图，也不代表真实 Discord 客户端验收或用户视觉批准。

### Final fixed-shell / compact-content / room-modal verification

透明固定外壳初轮扩大回归为 66/68：极小窗口场次图标与既有麻将头像失败。恢复图标后针对性 5/5 通过；后续那条被中断的检查命令不计为完成。紧凑布局修正错误提示盖住重试按钮后，最终 48/48 通过，类型检查及构建通过（`compact-content-final.log`、`compact-content-build.log`）。该结果早于21点弹窗。

弹窗轮次初次 58 项为 56 通过、2 项 Discord 关闭回焦失败。补上 post-flush 显式焦点恢复并清除关闭时的旧提示后，3 项小窗口/Discord 针对性复测通过，最终以下 **58/58 通过**；类型检查与生产构建退出码均为 0：

```powershell
Set-Location src\chat\features\games\blackjack-web
npm run typecheck
npx playwright test tests/lobby-grid.spec.ts tests/lobby-window-fit.spec.ts tests/room-directory.spec.ts tests/lobby-secondary.spec.ts tests/table-timeout.spec.ts tests/discord-room-launch.spec.ts tests/responsive.spec.ts --output="$env:PI_SCRATCH_DIR\room-modal-final-recheck" --global-timeout=180000
npm run build -- --outDir "$env:PI_SCRATCH_DIR\room-modal-build-final"
```

最终二级测试为 11 项，覆盖固定几何、透明样式、双轴无溢出及不滚动可点击、440×340 紧凑上限、组件身份/选择/请求保留；弹窗覆盖最小568×320、Discord横屏844×390、旋转390×844的居中、可达、失败重试、输入保留、关闭回焦、成功入桌、无额外下注/准备/开始及不叠遮罩。最终日志在 scratch `room-modal-final-recheck.log`、`room-modal-typecheck-final.log`、`room-modal-build-final.log`。

最终截图索引 `screenshots/lobby-redesign/room-modal-review.html`，对应 `room-modal-*` PNG。早期 `secondary-*`、`fixed-transparent-*`、`compact-content-*` 页面保留以追踪被否定方案，不代表最终验收。58 项集合不含既有麻将结算头像用例；该问题仍未解决，不能把它计为通过。没有真实 Discord/生产多人验收或用户最终外观批准，未部署。

## Alternatives considered

- 只换颜色和卡片装饰：没有解决用户明确提出的布局和找房路径问题。
- 另写房间 API 与入座逻辑：增加重复维护和行为不一致风险，因此复用 RoomDirectory。
- 将房间列表维持为大厅之外的独立弹窗：无法在玩法区连续完成选玩法与找房。
- 将概念图直接作为背景：会烘焙假按钮、重复入口和不可交互内容，用户明确否定；单独生成无 UI 插画。
- 保留首版均匀大矩形网格仅调色：仍偏离确认稿的横条造型与层次，用户已否定；因此桌面改排布并重做边框/底纹。紧凑横屏保留两列以保证可读性，而非强行塞入五行小条。
- 重新生成游戏图标：用户明确允许保留现有图标；本轮仅调整尺寸，新增装饰使用 SVG/CSS，避免额外生成成本。
- 给选场页换肤但仍全屏跳转：没有满足保留大厅场景的要求；因此统一右侧内容容器。
- 用条件分支把 TableGames 在大厅/全屏两个父节点间搬动：会重建组件，导致选择丢失、恢复/加入重复；选用固定组件树与布局类切换。
- 二级加宽、整体容器填底色、用内部滚动和下滑提示安置选择：均被用户明确否定；改为固定透明外壳、紧凑内容和补充设置弹窗。保留最低控件可达性，不通过缩放、裁切或测试滚动伪造通过。
- 桌面把麻将选场和21点表单撑满可用高度：用户认可的是 Discord 的紧凑比例；上限440×340，桌面也不拉伸。21点内联表单又被否定，最终替换为居中原生弹窗。

## Consequences

玩法与找房形成同一区域，辅助入口不混排；独立背景和卡片装饰可分别替换。饱和色、双层金边与横条入口向确认稿靠拢，但不是将概念图直接粘贴进页面。新增两张合计约 1.4 KiB 的 SVG；窄屏为可读性保留两列布局。根布局无溢出，房间条目内部滚动。真实 Discord 客户端观感与网络环境仍需发布前确认；不声称已获用户视觉认可。未提交或部署。

二级选择保持大厅空间连续性，透明留白避免额外遮挡人物；21点设置仅在明确打开时使用独立弹窗背景。真实牌桌保留原尺寸容器。广泛回归发现的既有麻将结算头像遮挡仍是已知独立问题，不扩大本次设置弹窗的修改范围。
