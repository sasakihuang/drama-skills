# Agent Note: 生产报价算上图片、跑不了的视频 job 在 prepare 就拒收、job 可以不抄提示词

Status: implemented

## Problem

爆款复刻的剧情路线排练（2026-11-12，没花钱，停在确认卡前）走到生产技能时碰到四处：

- `--quote` 对图片 job 写「不是视频任务，不计价」，合计「约 0.00 元」，而那条 20 秒的片子要出 9 张图。
  图片走 yoyo 中转的 gpt-image-2，每张都实际扣钱，创作者看到的报价却是 0。
- 给 Hailuo-02 的视频 job 多绑一张人物参考图：五份文档检查、`prepare`、`--quote` 都通过；`adapters.json` 里
  Hailuo-02 只认起始帧和结束帧，这种 job 要到 `run`（创作者点了确认以后）才被拒。不花钱，但创作者白点一次确认。
- job 的 `prompt` 必须和文档里的可复制正文一字不差，套件没有取正文的办法；排练的助手自己写了 40 行脚本。
- 原片一镜到底，平均镜长 20 秒，比所有模型一段的最长时长都长；`status` 的 `profile_notes` 只提示过短，不提示过长。

## Decision

- 出图价：按 yoyo 中转站给的价，每张 0.40 元（2026-11-12）。`provider_adapters.py` 记成 `GPT_IMAGE_PRICE`，
  `--quote` 按它给 `gpt-image-2` 的图片 job 报价并算进合计，`--price-table` 列出来，`--estimate` 新增
  `--draw <张数>` 把要出的图加进每个选项的合计；`OPENAI_BASE_URL` 不指向 yoyo 时多一行提醒，说图片价以那边为准。
  系统弹窗确认也显示这个价。配音、音乐仍不报价，合计下面另说一句。
- 提前拒收：`check_minimax_video_job` 按 `run` 的做法把视频 job 编一遍，参考文件换成占位数据，不读文件。
  `production_tool.py prepare` 对映射到内置 MiniMax 模型的配置档调用它，编不过就拒收、什么也不存。
  不认识的配置档（外部 adapter）不检查。
- 提示词：job 写了 `source_entry` 时可以不写 `prompt`，`prepare` 从那一条原样取（`分镜.md` 取冻结关键帧提示词）。
  写了 `prompt` 的照旧要求一字不差。
- `profile_notes` 加一条：平均镜长高于目标模型一段最长时长时，提示要拆段、会多出接缝。多镜容器解决不了这件事，
  所以不像过短那条一样可以用 `multi_shot_containers` 消掉。

## Alternatives considered

- 图片价写进每个人自己的配置文件，由创作者自己填。最强理由是换中转、调价都不用改代码；不用是因为用这个分支的人
  走的都是 yoyo，多一项配置就多一处会填错、会忘填，而报价是每次花钱前都要看的。换了中转的人会看到提醒。
- 只在 `--quote` 里检查。最强理由是报价本来就是「这个组合模型做不做」的地方，改动更小；不用是因为 `--quote`
  读的是助手写的 job 文件，现在可能不带 `prompt`，查不了 Hailuo 的 2000 字上限，而 `prepare` 手里是完整的 job。
- 照 `adapters.json` 声明的 `reference_roles` 在 `prepare` 检查，不碰模型知识。最强理由是和 `run` 用同一份声明、
  对外部 adapter 也有效；不用是因为 `run` 用哪份配置由命令行指定，`prepare` 只能猜默认那份，而且只查角色，
  查不了首帧、时长、分辨率这些同样到 `run` 才失败的地方。
- 新做一条命令从文档生成整个 job（连参考图绑定）。最强理由是助手一步到位；不用是因为参考图的 `role` 是生产侧的
  翻译，原设计明确不猜，绑定仍要助手按文档的「用途」写；提示词是唯一又长、又必须逐字一致的字段，省掉它就解决了
  排练里的问题。

## Consequences

- 报价里的图片不再是 0 元；第一次停下时，剧情类「这条片子合计」也能把图片算进去。
- 换了中转或直连 OpenAI 的人，图片价是估的，输出里会说。yoyo 调价时改 `GPT_IMAGE_PRICE` 和日期。
- `prepare` 现在会读同目录的 `provider_adapters.py` 和 `~/.drama-skills/adapters.json`（只用来查配置档对应哪个模型），
  读不到就不检查，交给 `run`。
- 弹窗确认的测试原来用一个没有起始帧的 Hailuo-2.3-Fast job，它在 `run` 时本来就会被拒，现在 `prepare` 就不收；
  测试改用 H3。
