# 牌桌素材来源

## 本轮实际使用的桌面与房间素材

- 项目：[CragonGame/CasinosClient](https://github.com/CragonGame/CasinosClient)
- 固定版本：`9446e1011baf86d684e20f2c962c7e8f45cf74c5`
- 原文件：`ProjectUiKing/assets/Desktop/Image/ClassicDesk.png`、`ClassicBg.png`、`CardBack.png`
- 作者：上海螭龙网络科技有限公司（2018）。许可：MIT，全文见 `LICENSE-Cragon.txt`。
- `poker-room.svg`：提取原桌面皮革与毡面，重新构成全幅透视牌桌与桌沿，不含上游品牌。
- `room-background.webp`：原房间背景转为 WebP。
- `card-back.png`：原牌背。
- `mahjong-room.svg`：本项目独立绘制的透视麻将桌，参考用户提供的四向排布；未复制 mj-yl 的代码或贴图。

## 筹码

- 素材：[Screaming Brain Studios — 2D Poker Pack](https://opengameart.org/content/2d-poker-pack)
- CC0，包内声明见 `LICENSE-SBS.txt`。
- `chip-red.png` 从 `Top-Down/Chips/Chips A - Flat 64x72.png` 提取红筹码。

## 调研保留的 Vintage Poker 原始桌面（当前 UI 未使用）

- 项目：[Pobermeier/vintage-poker](https://github.com/Pobermeier/vintage-poker)
- 版本：`408fa89c24f940f801c158da696a79c1b92ca2c0`
- 原文件：`client/src/assets/game/table.svg`
- 作者：Patrick Obermeier
- 许可：MIT，原文位于 `vintage-poker/LICENSE.txt`。
- `vintage-poker/table.svg`：上游原始文件。
- `vintage-poker/table-midnight.svg`：保留上游牌桌几何、光影与内圈，调整为深蓝配色，移除原项目品牌路径。

布局在本项目 Vue 组件中适配，未引入上游账号、后端和货币逻辑。素材随应用本地提供，不依赖外部图片服务器。
