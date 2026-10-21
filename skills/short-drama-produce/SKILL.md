---
name: short-drama-produce
description: 在创作者明确确认后，执行短剧项目的图片、视频、TTS/配音或时间线音乐生产任务，并把结果与精简运行记录落回项目。用户说“生成这张图/这段视频/这句配音/这段配乐”“开始跑图/跑视频/合成语音/生成音乐”“把已确认提示词送去生产”，或要求批量执行已确认媒体任务时使用；不负责创作提示词、镜头、台词、歌词或声音身份，也绝不把预览、继续、预算说明或既有接受状态当作本次付费生产确认。
license: MIT
---

# 确认后生产

本技能把已经写好的生产规格送到运行环境配置的 adapter。图片提示词归
`$short-drama-image-prompts`，视频提示词归 `$short-drama-video-prompts`，台词归
`$short-drama-write`，声音身份归 `$short-drama-assets`。

## Quick Start

只在用户明确要求实际生成后，从当前 `图片提示词.md`、`分镜.md` 或 `视频提示词.md` 中
取出本次提示词，建立一个有边界的运行 job。creator-first job 的 `source` 必须指向拥有这条提示词的
当前 Markdown，`source_entry` 必须点名该文档允许的二级标题：`图片提示词.md` 用 `IMG-*`，
`视频提示词.md` 用 `MOTION-*`，`分镜.md` 用 `SHOT-*`（modality 为 `image`，正文取该镜的
`### 冻结关键帧提示词`）。此选择器只提取首帧；尾帧需先由图片提示词阶段建立独立 `IMG-*` 状态图条目，
再从 `图片提示词.md` 准备 image job。创作者也可以提供已有起始帧——
在自己的工具里出图再放进项目同样成立，图片始终不进项目时走分镜的 `PLAN-...`；
产出落在 `剧集/<EP>/制作成果/images/` 后，分镜 owner 才能把它绑成 `用途：起始帧` 的 `REF-...`。存在真实参考图时，
还必须逐张填写 `reference_bindings` 的槽位、顺序、路径、中文名、用途以及允许/禁止控制范围；
建立 job 时按当前文档的 `用途` 选择供应商 role，写入 `reference_bindings[].role`；
内置视频 adapter 读取这个 role，只接受已实现的取值
（MiniMax 为 `first_frame`/`last_frame`/`reference_image`/`reference_video`/`reference_audio`，
Seedance 也接受这五种 role，具体组合依账号配置的型号而定）；带参考图却没有绑定的 job 会直接失败，不猜测 role。
本地图片由内置 adapter 按 base64 data URI 直接送出，不需要自建上传服务。
`MOTION-*` 的「参考音频」行同样逐条进 `reference_bindings`，排在图片之后、role 固定为 `reference_audio`、`character` 照抄该行的 `角色`；
adapter profile 没有声明接受 `reference_audio` 时 `run` 在提交前失败，见
[adapter-contract.md](references/adapter-contract.md#adapter-config)。
需要把条目中的 `PLAN-...` 图片作为生成输入时，`prepare` 会要求先把真实文件放进项目、改写成
`REF-...`。直接从 `分镜.md` 绘制该 SHOT 自身首帧时，选择器可跳过 PLAN 槽位，因为本次生成
使用冻结关键帧正文，不发送创作者在项目外准备的那些图片；已有 REF 绑定仍须逐项一致。
这项豁免要求跳过 PLAN 后剩余 REF 的顺序仍从 1 连续；否则先交分镜 owner 调整源声明顺序，
不能只在 job 中重编号。生产接口还要求参考路径唯一：创作上同一图片可有两个用途槽位，但同路径
双槽目前不能提交。由负责技能与创作者决定修订参考职责或等待接口支持，不在 job 中擅自合并或漏槽。
`references` 可以省略并由绑定顺序生成，也可以作为相同顺序的显式镜像。输出放在
`剧集/<EP>/制作成果/`；这个 job 是生产工具的临时输入，不是第六份创作文档：

```bash
python3 {技能目录}/scripts/production_tool.py prepare <project> --job <临时-job.json>
```

先展示 `prepare` 的完整预览；此时不会调用供应商。

## 硬闸门

每次生产都必须经过以下四步，顺序不可合并：

1. 建立一个有边界的 job：一种 modality、明确数量、完整 prompt/spec、参考文件、参数、输出路径和 adapter profile。
2. 运行 `prepare`，把返回的完整预览展示给创作者，尤其是数量、prompt、source entry、
   reference bindings、references、outputs、overwrite 与 adapter。creator-first job 会在这一步机械核对
   所选标题里的可复制提示词，以及参考图槽位、顺序、路径、中文名和控制边界；任一漂移都 fail closed。
   内置 MiniMax 视频 job 还要附上 `--quote` 算出的官方价，见[选视频模型与看价格](#选视频模型与看价格)。
3. 等创作者在**看到这份预览之后**明确确认。**默认用你所在界面自带的选项卡提问**：Codex 的 `request_user_input`、
   Claude Code 的 `AskUserQuestion` 这类让创作者点选项回答的提问工具。把预览贴进对话后，不要结束这一轮、不要等他
   打字，直接发这张选项卡：
   - 问题写清本次任务、模型（配置档）、分辨率、时长或尺寸、数量和价格，一眼能核对。
   - 视频 job 的选项固定三项、顺序不变：「确认生成：<模型> <分辨率> <时长>，<价格>」「换个模型」
     「这次不生成」。图片、配音等 job 的第二项写「先改一改」。不要把确认和别的问题混在同一张卡里。
   - 创作者点「确认生成」，就把 `prepare` 预览里的 `confirmation` 原样交给 `confirm --confirmation`，接着在
     同一轮运行 `run`。他亲手点的这个选项就是本次确认。
   - 点「换个模型」，按[选视频模型与看价格](#选视频模型与看价格)用 `--estimate` 给出这一段在各模型上的价格，
     再用选项卡请他选；选定后交 `$short-drama-video-prompts` 按新模型的方言改写这一镜（时长不在新模型的
     可选范围内时先交分镜调整），重新 `prepare`，再发一次确认卡。点「先改一改」就问他要改什么。
   - 点「这次不生成」就停下，回报这一项没有生成。
   - 回答到达之前不运行 `confirm` 或 `run`；提问工具返回的「已发出」「accepted」只表示卡片发出去了，不是确认。
   界面没有这类选项卡工具时，才用系统弹窗 `confirm --dialog`：它列出本次任务、配置档、参数、参考图、输出、价格
   和提示词开头，默认按钮是「否」；创作者点「是」才写入确认（返回 `confirmed`），接着在同一轮运行 `run`；
   点「否」（返回 `declined`）就停下，回报这一项没有生成。命令会一直等到创作者点按钮，运行时把命令超时设长
   （至少 10 分钟），等待期间不要放弃、重试或另起一个弹窗。弹窗返回 `dialog_unavailable`（没有图形界面，
   例如远程或沙盒里），或创作者说过要打字确认时，才在对话里等他回复本次确认，再运行 `confirm --confirmation`。
   “继续”“都做完”“预算没问题”、上游内容已接受或之前确认过另一版，都不算本次生产确认。
4. 运行 `run`。它会在启动 adapter 前消费一次确认；成功或失败后再次执行都必须重新确认，
   防止失败重试意外产生第二笔费用。

job、prompt、参数、输出路径或直接输入任一变化，旧确认立即失效。除了把创作者在选项卡里亲手点的「确认生成」
转成 `confirm --confirmation`，不得代替创作者填写确认。
创作者在本次请求里已经点名后续步骤（例如「按流程走完」「出完首帧接着做视频」）时，一个 job 运行完可以在同一轮
接着 `prepare` 下一个，照样逐个确认，并在确认前把上一步的结果（例如刚出的首帧）先给他看；没点名的下一批、
审查不自动开始。

`分镜.md` 的「输入参考图」路径只是创作阶段的可读依据与使用意图，不是生产输入快照。进入生产时，
creator-first job 必须从拥有提示词的 `图片提示词.md`、`分镜.md` 或 `视频提示词.md` 对应条目建立绑定；`prepare` 展示的
`reference_bindings`、`references` 与已确认 job 才是本次 adapter 实际读取哪些文件字节、各自允许
影响什么的权威。image/video job 从对应的当前创作文档建立，使用匹配的 selector，不能靠省略字段绕过。
新生产结果不自动回填或刷新分镜；需要把它改为后续输入时，由分镜 owner 修订文档，再建立新 job
并重新预览、确认。

## 命令

只在进入生产边界后把当前提示词和运行参数写成临时 JSON；视频与图片 job 的
`parameters.prompt_language` 跟随当前可复制正文已经解析出的提示词语言，使 adapter 追加的参考约束
使用同一种语言，而不是重新回退成固定英文。不要在创作阶段为每条提示词预建 job。
格式和 adapter 契约见
[adapter-contract.md](references/adapter-contract.md)。命令由
[production_tool.py](scripts/production_tool.py) 提供，然后运行：

```text
python3 <本技能目录>/scripts/production_tool.py prepare <project> --job <job.json>
python3 <本技能目录>/scripts/production_tool.py confirm <project> --job-id <id> --dialog
python3 <本技能目录>/scripts/production_tool.py confirm <project> --job-id <id> --confirmation "CONFIRM <id> <code>"
python3 <本技能目录>/scripts/production_tool.py run <project> --job-id <id> --adapter-config <outside-project-config.json>
python3 <本技能目录>/scripts/production_tool.py status <project> --job-id <id>
python3 <本技能目录>/scripts/production_tool.py collect <project> --job-id <id> --adapter-config <outside-project-config.json>
python3 <本技能目录>/scripts/production_tool.py audit <project>
```

Windows 上的 `python3` 可能只是应用商店的占位程序，运行失败或弹出商店时改用 `python` 或 `py -3`。

`prepare` 只验证并预览，不生产。`confirm` 只保存与当前 job 指纹绑定的一次性确认；选项卡确认走 `--confirmation`，
填 `prepare` 预览里的 `confirmation`。`--dialog` 由创作者在系统弹窗里点「是」给出这次确认，退出码 0 表示已确认、
3 表示点了「否」、4 表示弹不出窗。
`run` 才启动 adapter。`collect` 只取回**已经提交过、已经计费**的那次任务的结果，
不重新提交、也不需要新的确认——见下面「中断不等于要重跑」。`audit` 只对账本地任务历史、失败后恢复、重复内容尝试和当前输出字节，
不会调用供应商，也不把技术成功、文件存在或哈希一致写成媒体质量结论。同一 job 存在未决
`running` attempt 时禁止重新 prepare、confirm 或 run；先等待完成或排查遗留 attempt。

## 输入选择

- **image**：读取 `图片提示词.md` 的当前 `IMG-*` 可复制正文，或 `分镜.md` 的当前 `SHOT-*`
  冻结关键帧正文，加上必要参考图和明确的输出尺寸/数量；creator-first job 使用 `source_entry`
  锁定这一条。资产板走 `IMG-*`，某一镜的起始画面走 `SHOT-*`；两者不互相替代。
- **video**：读取 `视频提示词.md` 的当前 `MOTION-*` 可复制正文，并核对 `分镜.md` 中对应镜头、
  冻结关键帧、时长与画幅；creator-first job 使用 `source_entry` 锁定这一条。续接方法需要上一段
  实际视频和从该视频取得的实际尾帧，分别承担动作/声音连续与新段可见起点；H3 的供应商组合为
  `reference_video + reference_image`。当前 creator-first 来源选择器只解析图片与角色参考音频，
  尚不能声明续接视频，因此这条内置生产路径不能提交续接段。报告接口缺口并保留已选路线，
  不省略来源选择器、不拿计划尾帧冒充真实输入，也不擅自改为独立文生视频。
  `视频提示词.md` 里的 `GROUP-*` 多镜分组正文同样还不能作为来源：选择器只认 `MOTION-*`。创作者要提交分组时，
  把分组正文复制到自己的生成工具；不要拿某个成员的 `MOTION-*` 代替整组提交。
- **tts**：从 `剧本.md` 读取原句与表演要求，声音参考由用户或现有媒体明确提供。不得在生产 job
  中改词，也不为 TTS 新建第六份创作文档。表演要求来自这句台词的括注和上下文。外部执行端若把
  prompt 解释为含表演指令的输入，可在台词前写一句语气说明（「憋屈地压低声音，咬着牙应一声：是。」）；
  支持参考音色时，同一角色每句使用同一段已接受参考。内置 MiniMax Speech 把 prompt 逐字作为台词，
  因此只放原句，以同一已选 `voice_id` 保持角色声音，再把表演要求映射到支持的情绪、语速、音量和音调参数。
- **music**：读取 `视频提示词.md` 中创作者已确认的时间线音乐章节；主题曲使用已确认歌词，纯配乐
  不携带歌词。供应商不能精确承诺时长时，生成源音轨后仍由 `$short-drama-edit` 按文档里的混音意图完成落点、
  循环、淡入淡出和对白 ducking。

一个 job 不混合 modality。大批量工作拆成创作者能看清数量和成本边界的小 job；不为方便把整季
隐式塞进一次确认。

## Adapter 边界

adapter 配置必须在项目外，包含 argv 命令、超时和可选的 `reference_roles`；凭据由 adapter 从进程环境、系统凭据
存储或用户主目录下的 `~/.drama-skills/providers.env` 读取（进程环境里已有的同名项优先）。项目 job、确认记录、
运行记录和 Dashboard 都不得保存密钥。

脚本以 JSON stdin 调用 argv 数组，不使用 shell，不拼接命令。adapter 返回本地临时文件；工具只
接受与已确认 targets 完全一致的结果，并把完整文件原子复制到项目的 `剧集/<EP>/制作成果/`
目录。项目和上游 Skill 不写死供应商、模型或即将变化的 API。

内置图片/视频 compiler 会根据已确认的 `reference_bindings`，按顺序向供应商 prompt 附加一段确定性的
引用语义说明（中文名、用途、允许控制与不得控制范围）；不会把槽位名误当成要渲染进画面的文字。
外部 adapter 也必须保留这组语义或明确拒绝，不能只上传文件而静默丢失控制边界。

本技能提供五个可选的 stdlib adapter，均通过项目外 adapter config 选择，凭据只从运行环境读取：

- [Seedance](references/providers/seedance.md)：模型/Endpoint ID 必须由账号显式配置；compiler 支持
  图片、视频和音频参考 role，本地参考文件由内置 runtime 编码为 data URI 发送。
- [GPT Image 2](references/providers/gpt-image-2.md)：无参考图走 generation，有参考图走 edit；
  固定高保真引用并校验尺寸、格式与透明背景限制。Key 和中转地址（默认模板填的是 yoyo）由用户自己配置。
- [MiniMax Music](references/providers/minimax-music.md)：使用 `music-3.0` 与 hex 结果，区分主题曲
  和纯配乐，不伪造时长请求字段。官方自 2026-08-20 起不对新用户开放音乐生成，新账号调用会被拒。
- [MiniMax 语音](references/providers/minimax-speech.md)：`tts` 模态；模型与 `voice_id` 都必须
  由账号与文档显式给出，不内置音色清单。只从预置音色合成，不做声音克隆；创作者授权的参考录音
  按普通创作者输入放在 `输入/`，不能据此声称本适配器会读取或克隆它。
- [MiniMax 视频](references/providers/minimax-h3-video.md)：内置 MiniMax-H3、MiniMax-H3-Max 与
  MiniMax-Hailuo-2.3-Fast / 2.3 / 02 五个模型的接口版本、分辨率、时长和官方价，用 `--model` 选；
  其他模型 ID 仍须由账号显式配置分辨率集合与时长区间。默认国内站，国际站账号设 `MINIMAX_REGION=global`；
  默认打开视频上的「AI 生成」标识。H3 系列的提示词进 `content` 的 text 项，本地参考按显式 role 绑定并编码为
  data URI，与画面同一次生成声音；Hailuo 系列走老接口，只收起始帧（Hailuo-02 还收结束帧），没有声音。
  写法影响见 `$short-drama-video-prompts` 的目标模型能力档案。

这些 adapter 实现本文列出的请求契约；账号可用性需由部署方核实，生成质量需另行观察和审查。
正式生产仍须经过本次预览后的明确确认。

仓库自带 `fixture_adapter.py` 只用于离线测试，不代表真实生成质量或默认生产 adapter。

## 出参考图和首帧

写完图片提示词或冻结关键帧、创作者要出图（或同意了推荐的出图）时，先运行：

```text
python3 <本技能目录>/scripts/provider_adapters.py --check-config
```

显示 `OPENAI_API_KEY：已配置`、`adapter 配置` 后面是文件路径，就用内置的 `gpt-image-2` 配置档出图，不要让创作者
自己去别处出图，也不要改用你所在环境自带的出图工具（例如 Codex 的 `image_gen` 或 imagegen 技能）：那样没有价格确认、
可能已经在创作者的账号或中转上计费，图也不进项目。创作者明确说要用自带工具时才用。内置 `gpt-image-2` 的做法是：job 的 `modality` 写 `image`、`adapter` 写 `gpt-image-2`，一个 job 只出一张图，输出放
`剧集/<EP>/制作成果/images/`。资产图从《图片提示词.md》的 `IMG-*` 取，首帧从《分镜.md》的 `SHOT-*` 取；先出资产图、
交分镜 owner 绑成 `REF-...`，首帧再挂着它们出。尺寸写进 `parameters` 的 `width` 和 `height`：两边都是 16 的倍数、
长短边之比不超过 3:1，9:16 竖屏首帧可用 864×1536，横屏反过来。每张都走上面的[硬闸门](#硬闸门)：`prepare`、
展示预览、请创作者确认（默认用选项卡）、再 `run`。出完把图片路径给创作者、把图显示出来请他看；满意后交分镜 owner
绑成 `REF-...`（首帧写 `用途：起始帧`），不满意就回提示词负责技能修改，再重新 `prepare`。接下来要做视频而目标
模型还没定时，先按下一节给价目表。没显示已配置时，按[安装维护](#安装维护)请创作者先配好。

## 选视频模型与看价格

**视频模型和分辨率只能由创作者亲手选，不能替他定。** 创作者选视频模型、换模型，或问“用哪个模型”“多少钱”时，
以及流程要做视频而目标模型还没定时，主动先运行：

```text
python3 <本技能目录>/scripts/provider_adapters.py --price-table
```

把输出的价目表原样给创作者看。镜头数量和每镜时长已经大致定下时，再按计划的每段时长运行一次（例如三段各 10 秒）：

```text
python3 <本技能目录>/scripts/provider_adapters.py --estimate 10 10 10
```

它算出这条片子在每个模型、每种分辨率上的合计价。然后**单独**用选项卡问一次「用哪个视频模型」：每个选项写
模型、分辨率和这条片子的合计价（取自 `--estimate`），你推荐的放第一个并在问题里说一句为什么，其余挑价位和效果
差别明显的几项（选项卡放不下全部时，留一项「看完整价目表再选」）。这个问题不和制作方案、画幅、风格或别的
问题合在同一张卡里，也不把模型写死在方案选项里让他顺带选中。创作者只说过「最便宜」「效果最好」这类方向，
也照样列出来请他点；他在请求里明确写了模型和分辨率（例如「用 Hailuo-2.3-Fast 768P」），就算已经选过。
改版、重做或镜头数量、时长变了，使合计价变了，就带着新价格重新问，不要写「沿用你选的」。
**只列官方价格**：不换算、不提任何订阅套餐的额度、百分比或“还剩多少”——各套餐包含的用量不同，统一以价格为准。
选定后，video job 的 `adapter` 写该模型的配置档名（价目表第二列，例如 `minimax-hailuo-2.3-fast`），`parameters` 的
`resolution` 与 `duration` 必须是该模型价目里有的组合。之后每段视频的确认卡里都留着「换个模型」，见[硬闸门](#硬闸门)。

每批 video job `prepare` 之后、请创作者确认之前，运行：

```text
python3 <本技能目录>/scripts/provider_adapters.py --quote <job.json> [<job.json> ...]
```

把逐条官方价和合计跟预览一起展示。报价报错说明这个分辨率、时长组合该模型不提供，job 提交时同样会失败；
回去改参数或换模型，重新 prepare。

Hailuo 系列与 H3 系列的差别会改变上游写法：Hailuo 只认起始帧（Hailuo-02 另认结束帧），不收人物、场景、
道具参考图或参考音频，没有声音；Hailuo-2.3-Fast 必须有起始帧；时长只有 6 或 10 秒，1080P 只有 6 秒；
提示词不超过 2000 字；画幅跟着起始帧走，文生视频只出 16:9。选 Hailuo 时，视频提示词按
`$short-drama-video-prompts` 的 Hailuo 方言写。

## 中断不等于要重跑

异步视频任务提交后就可能产生费用；轮询和下载中断不代表供应商没有执行。内置 adapter 在拿到
供应商任务 ID 后、第一次轮询前将其写进 `handle_path`，这个路径在本次尝试结束后保留。

中断后按以下顺序恢复：

1. `audit` 会把带着任务 ID 的未完成尝试报成 `orphaned_provider_job`，
   `action` 是 `collect_before_retry`；
2. `collect` 用那个 ID 取回结果并把这次尝试标成成功；
3. 只有确认原任务已终态失败、无需继续取回时，才为重投重新预览和确认；暂时无法轮询或下载仍应先排查取回路径。

`audit` 报出 `orphaned_provider_job` 时先 collect。它只轮询和下载既有任务，不提交新生成请求，
因此不消费新的生产确认；重投仍需重新确认。

## 结果与复核

成功后回报实际输出路径、媒体类型和运行状态；不要把“adapter 返回成功”写成质量结论。
多任务或重试后先运行 `audit`：终态失败按 `retryable` 路由，重试仍须新的明确确认；输出缺失或
文件的哈希或大小不再等于运行记录时，先复核当前字节或重新生产。`repeated_content` 只是成本与诊断信号，
不能自动判定同文重试合理或不合理；`running_attempt` 是未决运营状态，audit 必须返回 attention。
技术失败先排除仍可 collect 的已提交任务，再按 `retryable` 决定是否建议有上限的重投，每次重投仍须新确认。
失败证据若能定位被拒的提示词、参考图或音频，就把证据交给对应负责技能；证据不足时先核实，
不猜测被拒原因。生产技能不得把「一拳砸在对方脸上，血顺着下巴滴」擅自换成
「一拳挥空，对方侧身避开，桌上的杯子被带倒」：这已改变来源动作。文本、参考图或录音的修改
由负责技能完成并交创作者接受，再从更新后的文档建立 job，重新 prepare、展示变化并确认。
重复内容缺陷同样回到对应 prompt/spec 负责技能。
生产结束后不自动进入剪辑。素材要装配成成片时由用户点名 `$short-drama-edit`；它只取舍已有帧，
不回头改本阶段的 job，也不生成新素材。
如需质量复核，报告可把已有结果另行交给 `$short-drama-review`；不要在生产调用中自动启动复核。
Dashboard 只负责展示这些文件和运行摘要，不提供 adapter 设置或生产按钮。

## 安装维护

第一次使用内置 adapter 时运行一次：

```text
python3 <本技能目录>/scripts/provider_adapters.py --write-config
```

它在用户主目录生成 `~/.drama-skills/adapters.json`（每个内置视频模型一个配置档，另有 `gpt-image-2` 和
`minimax-speech`；`run`/`collect` 的 `--adapter-config` 就填这个文件）和空白凭据模板
`~/.drama-skills/providers.env`。请创作者**自己**用记事本打开 providers.env 填 Key：MiniMax Key 与国内/国际站、
画图用的 Key（`OPENAI_BASE_URL` 已预填 yoyo 中转地址，用别的中转就改这一行）。不要让创作者把 Key 发进对话，
也不要替他写进文件。填好后运行 `--check-config` 核对：它只显示每项有没有配置、MiniMax Key 像不像订阅 Key，
从不显示 Key 本身。

只有安装、升级或排障时运行离线自检；普通创作和生产准备不运行：

```bash
python3 scripts/selftest.py
python3 scripts/provider_adapters.py --selftest
```
