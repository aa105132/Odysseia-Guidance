# 麻将正面花色素材来源与修改

本目录的 34 张 SVG 是上一级目录对应麻将素材的修改版本，供游戏页面在统一的 HTML/CSS 牌壳中显示。透明 SVG 仅含花色，不含牌壳、阴影或牌背。

- 原作者：Cangjie6 与 Wikimedia Commons 的其他贡献者。
- 原始作品：https://commons.wikimedia.org/wiki/Category:SVG_Oblique_illustrations_of_Mahjong_tiles
- 作者页面：https://commons.wikimedia.org/wiki/User:Cangjie6
- 压缩 SVG 来源仓库：https://github.com/perthmahjongsoc/mahjong-tiles-svg
- 来源固定版本：`db321bb0eeffd040dfad70a9a5a84c4987c49f7c`。
- 上一级原始素材说明：[ATTRIBUTION.md](../ATTRIBUTION.md)。
- 原作品及本目录修改版许可：**Creative Commons Attribution-ShareAlike 4.0 International（CC BY-SA 4.0）**。
- 许可全文：[LICENSE.txt](../LICENSE.txt)。
- 许可链接：https://creativecommons.org/licenses/by-sa/4.0/

## 本项目所作修改

Odysseia-Guidance 项目于 2026-09-22 为游戏正面视图制作此修改版：

1. 移除原图的斜视牌壳、牌背、反光与牌壳渐变。
2. 完整保留原作者花色路径、颜色、描边与相对比例；没有重绘文字或图案。
3. 把花色样式及祖先变换写入独立路径，避免依赖原牌壳的样式与分组。
4. 使用统一的 `viewBox="0 0 100 140"`，将完整花色等比居中；万字牌共用包围盒，保留上下文字的共同基线。画布有至少约 4 个单位的安全边距。
5. 文件名保持游戏使用的 `m1`—`m9`、`p1`—`p9`、`s1`—`s9`、`z1`—`z7` 编码。

这些修改不表示原作者认可或背书本项目。后续分发或改作须保留相应署名与修改说明，并按同一许可分享改作；本许可不替代项目其余代码的许可。

## 接入方式

资源路径为 `/mahjong/faces/{牌编码}.svg`。由页面负责牌壳背景、圆角和阴影，图片使用 `width: 100%; height: 100%; object-fit: contain; display: block`。不要再同时叠加原始斜视牌壳，也无需用负边距或裁切放大花色。
