# 借鉴来源

本技能的做法综合了下面几个开源项目和公开方法。原文没有被大段照搬；直接带上的只有拉片工具
（许可证和改动说明在 `vendor/video-shots/` 里）和风格库（在 `assets/styles/` 里，见下面第二张表）。

| 借鉴了什么 | 来源 | 许可证 |
|---|---|---|
| 拉片工具：切点和运动量实测、八种节奏角色、15 道质量门（随技能附带） | github.com/eternityspring/reelbench-skills | Apache-2.0 |
| 本机识别台词：CPU INT8 的 faster-whisper，失败时改读画面字幕 | github.com/liuliu-66-create/ll-video-decomposer | MIT |
| 钩子的文案线和画面线、首帧测试、可复制要素清单 | github.com/alchaincyf/huashu-skills（huashu-douyin-script） | MIT |
| 镜头表字段、原创护栏、给生成环节的结构化交接 | github.com/sharon-laicc/viral-video-decomposer | MIT |
| 复刻尺度的逐元素对照、原片没有的不加、片尾落版替换、成片分级检查、关键镜头抽帧看动作 | github.com/Yf040528/video-recreation-skill | MIT |
| 结构、内容、手艺三层拆开 | beatra-ai/viral-video-remake-skill 的公开说明 | MIT-0 |

图文动效路线用到的工具（全电脑装一份，在用户目录的 `.drama-skills/hyperframes` 里，不随技能附带；
画面模板 `assets/motion-kit` 是本技能自己写的）：

| 用来做什么 | 来源 | 许可证 |
|---|---|---|
| 用网页写动画、在本机渲成 MP4 | github.com/heygen-com/hyperframes（npm 包 `hyperframes`） | Apache-2.0 |
| 时间线动画 | GSAP（npm 包 `gsap`） | GSAP 标准免费许可，可商用 |
| 思源黑体、马善政毛笔体 | Fontsource 打包的 Google Fonts（`@fontsource/noto-sans-sc`、`@fontsource/ma-shan-zheng`） | OFL-1.1 |
| 转场、弹出、冲击音效 | HyperFrames 包里自带的音效，取自 Pixabay | Pixabay 内容许可，可商用、不必署名 |
| 配乐（32 首精选，用时按编号现下，不随技能附带） | Mixkit（Envato 旗下），见 `music-catalog.json` | Mixkit Stock Music Free License：可商用、可发社交平台，不必署名；不可用于 CD/DVD、广播电视、游戏，不可混音成纯音乐作品，不可登记到版权识别系统 |
| 风格库：15 种动态设计风格的提示词、12 种的样片画面代码、评审要点（随技能附带，改动见 `assets/styles/README.md`） | github.com/Vincentwei1021/mg-styles-15 | MIT；08 形变的字形轮廓按 OFL-1.1 |
