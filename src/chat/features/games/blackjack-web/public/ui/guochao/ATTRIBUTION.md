# 国潮茶楼素材说明

制作日期：2026-09-21。

## 视觉参考

用户提供的[站酷作品（J琪姐姐）](https://www.zcool.com.cn/work/ZNjUzNjAyNjA=.html)用于参考暖色茶楼、蓝紫牌桌、金色包边与立体按钮的视觉方向。本目录未复制该作品的原始插画、人物、标志或界面贴图；参考不代表取得该作品素材的转载许可。

## 本次生成素材

由用户指定的图片服务 `https://bufan.live/v1`、模型 `chatgpt-gpt-image-2` 离线生成，实际提示词保存在 `PROMPTS.md`。

| 文件 | 内容及处理 |
| --- | --- |
| `teahouse-room.webp` | 无人物和界面文字的茶楼背景，生成后转为 WebP |
| `game-blackjack.webp` | 21 点纸牌与灵石物件插画 |
| `game-landlord.webp` | 斗地主官帽与纸牌物件插画 |
| `game-mahjong.webp` | 麻将牌物件插画 |
| `game-texas.webp` | 德州扑克纸牌与筹码物件插画 |
| `game-golden_flower.webp` | 炸金花纸牌与金花物件插画 |
| `burst-strip.webp` | 爆牌特效，4×4 原始序列去背景后切成 16 个 256×256 帧，横向拼成 4096×256 透明条带 |
| `burst-frames.json` | 帧坐标与时间元数据，总时长 1040 毫秒 |

五款入口插画由同一图集裁切、分割背景并修整透明遮罩；爆牌素材经色键去背景与边缘处理。上述生成素材没有套用第三方素材包的 MIT 或 CC0 许可声明；使用条件以生成服务适用条款为准。

## 本地 SVG

`table-felt.svg`、`mahjong-felt.svg`、`cloud-pattern.svg` 在本次改动中绘制，分别用于椭圆扑克桌、方形麻将桌和如意云纹；按钮、结果绶带及模式图标由项目内的 CSS 与 SVG 绘制。

扑克、角色、麻将牌和既有素材的来源不受本次改动影响，见相邻 `../../mahjong/ATTRIBUTION.md` 与 `../../table-assets/ATTRIBUTION.md`。接口认证信息不属于素材，不保存在仓库中。
