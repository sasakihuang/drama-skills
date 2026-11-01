# 来源与改动

本目录是 **reelbench-skills** 中 `skills/video-shots` 的一份副本：

- 项目：github.com/eternityspring/reelbench-skills
- 取用提交：`df39d5d`（2026-10-24）
- 许可证：Apache License 2.0，全文见同目录 `LICENSE`
- 版权归原作者所有

## 改动

- `SKILL.md` 改名为 `guide.md`，去掉了开头的技能元数据（名字、触发词、所需程序），
  免得安装工具把它当成一个独立技能。正文未改。
- 没有带上原项目的截图（`assets/*.png`）。
- `scripts/`、`references/`、`examples/` 里的文件与原项目逐字节一致。

## 在本技能里怎么用

`$short-drama-remake` 用它给参考片拉片：切点、镜长和运动量由脚本实测，景别、运镜、
画面和节奏角色由模型看联系表判断，再过 15 道质量门。用法见 `guide.md`；文中的 `{baseDir}`
就是本目录。只需要 `node`（18 或更新）和 `ffmpeg`，不联网，不需要任何 Key。

更新时整目录替换为原项目新提交的同名文件，再在本技能目录里跑
`node vendor/video-shots/scripts/selftest.mjs`。
