---
name: short-drama-remake
description: 爆款复刻：创作者丢来一条抖音、快手或小红书视频的分享链接（或整段分享文字、或自己保存的视频文件），说「复刻这条」「仿一个」「做个同款」「拆解一下为什么火」「照这个套路做一条」时使用。先下载原片、量出镜头、拆清楚它为什么火，停下让创作者选新题材和尺度或说出自己的想法，再把复刻方案交给剧本、分镜、生产、剪辑各技能做出一条新片，最后和原片对照检查。只学结构和节奏，不搬原片的台词、画面、人物、品牌和声音。
license: MIT
---

# 爆款复刻

这个技能只做一件事：照一条爆款视频的**结构和节奏**，做出一条内容全新的视频。
原片只拿来学，学的是钩子怎么下、段落怎么排、哪几秒转折、镜头多长、口气和声音是什么劲头；原片的
台词、画面、人物、品牌、配音和音乐一律不进新片，尺度见 [复刻尺度](references/remake-policy.md)。

出片分两条路线，拆解时定下走哪条：

- **剧情和实拍类**（画面里有人、有实景、有商品）：交给 AI 出图、出视频的出片流程。
- **图文动效类**（画面里没有人，主体是 K 线、图表、数据、文字在动）：用 HyperFrames 在本机做动画，
  见 [图文动效路线](references/motion-graphics-route.md)。这类片**不准**用画静态图的库逐帧拼图，
  那样做出来就是幻灯片。也不打开 HyperFrames 的网页编辑页（`hyperframes preview` 会自动弹浏览器），
  给创作者看渲好的 MP4。

创作者多半不懂技术：跟他说大白话，先说结果；工具和格式的事你自己处理，不拿来问他。

## 需要的程序

- Python 3.9 或更新版本（下载、识别台词的脚本只用标准库）。
- `ffmpeg` 和 `ffprobe`：拉片、抽帧、剪辑都要用。没有就先帮创作者装上。
- `node` 18 或更新：拉片工具用。用 `npx` 装过技能的电脑本来就有。
- 一个能执行页面脚本的浏览器：只有抖音需要，用你所在环境自带的就行。
- 可选：本机识别台词，见下文「识别台词（可选）」。不装就读画面上的字幕。
- 图文动效路线另外要 Node.js 22 或更新的版本，第一次用时装一次 HyperFrames，见那份文档的第 0 步。

## 流程

参考片目录统一放在项目的 `项目开发/remake/<R编号>/`（第一条是 `R01`）。下文的
`<本技能目录>` 指本文件所在目录。

### 1. 下载原片（免费）

```bash
python3 <本技能目录>/scripts/fetch_reference.py fetch "<创作者发来的整段分享文字或链接>" --out <参考片目录>
```

最后一行是 JSON。`ok: true` 就得到了 `source.mp4` 和 `source.json`。其他情况：

- `need_browser: true`（抖音）：先用电脑自带的 Edge 或 Chrome 在后台取，不弹窗口，几秒钟：

  ```bash
  node <本技能目录>/scripts/douyin_edge.mjs <作品号> --out <参考片目录>
  python3 <本技能目录>/scripts/fetch_reference.py douyin-detail <参考片目录>/douyin_detail.json --out <参考片目录>
  ```

  第一条返回 `ok: true` 就马上跑第二条（视频地址几分钟就过期）。返回 `ok: false`，再照
  [抖音：用你自己的浏览器取作品详情](references/douyin-in-browser.md) 用你自己的浏览器试；打开页面先静音，用完关掉标签页。
  不要没试这两步，就直接让创作者上传视频。
- 快手和小红书报错：多半是链接放久了失效，或者是电脑网页版链接。请创作者在 App 里重新
  「分享 → 复制链接」。
- 都不行，或者遇到验证码：请创作者在 App 里保存视频，然后
  `fetch_reference.py local <视频文件> --out <参考片目录>`。

不登录、不填密码、不扫码、不绕验证码。cookie 和账号这类事，创作者不提就不碰。

### 2. 拉片：把镜头量出来（免费）

照 [拉片工具使用说明](vendor/video-shots/guide.md) 在参考片目录里跑：`seed` → `frames` →
`sheet` → 看联系表填景别、类别、运镜、画面和**节奏角色（整片标全）** → 有漏刀就 `recut` →
`validate` 过门 → `render` 出 `shots.md` 和 `shots-report.html`。文中的 `{baseDir}` 是
`<本技能目录>/vendor/video-shots`。跳过的门要明说原因。

有台词的片子先识别台词（见下文，可选），把画面字幕和识别结果对照着读；以画面字幕为准。

然后量声音和画面（免费，几十秒）：

```bash
python3 <本技能目录>/scripts/media_profile.py <参考片目录>/source.mp4
```

它在旁边写一份 `source.profile.json`，内容包括：响度；人声之间停顿多不多（几乎没有停顿，说明底下
垫着配乐）；画面最长多久不动；有识别结果时，还有每秒说几个字。原片的声音你听不见，这些数字就是
你对原片声音的全部依据，写进拆解报告，成片出来后还要拿它对照。

### 3. 拆解：为什么火（免费）

照 [拆解方法](references/analysis-framework.md) 写 `拆解报告.md`，模板在
[报告模板](references/report-templates.md)。事实和推测分开写；每条「为什么火」都要指到秒和镜号。
关键镜头抽 4 帧时间条看清动作。报告里要写明片型和出片路线（剧情实拍 / 图文动效），还要写
原片的口气：是自信下判断，还是娓娓道来，是吐槽，还是煽情。

### 4. 复刻方案初稿，然后第一次停下

照 [复刻尺度](references/remake-policy.md) 和报告模板写 `复刻方案.md` 初稿：推荐一个新题材和尺度，
写出段落一级的逐镜对照和节奏档案提议。

**停下之前先出网页预览。** 创作者看 Markdown 原文很吃力，方案要给他排好版的网页：

```bash
python3 <本技能目录>/scripts/plan_preview.py <参考片目录>
```

它把 `复刻方案.md` 和 `拆解报告.md` 排成一页 `复刻方案预览.html`，放在参考片目录里：左边是目录，段落对照
做成卡片，每段配原片对应镜头的截图（取自同目录的 `frames/`），长的技术参数折叠起来。用任何浏览器都能直接打开，
不用联网。

- 消息里写一个 Markdown 链接指向这个网页，链接目标用脚本输出里 `link` 那一项的完整路径（用正斜杠的绝对路径），
  创作者自己点开看。不必替他打开。
- 方案以 Markdown 为准，网页只是给人看的。按创作者的话改方案时只改 `复刻方案.md`，改完重新运行一次上面的命令，
  再把链接发给他。
- 网页预览只用在这一步。后面的试听选声、定配乐和交付照原来的做法，不另外做网页。

**停下。** 先用三五句话讲清它为什么火、能仿几成，再用选项卡给创作者三条路：
照推荐的走 / 换题材或换尺度 / 说出自己的想法让你改。选第三条就照他的话改方案，逐条讲改了什么，
再问一次，可以来回改很多轮，**他说满意之前不往下走**。做法见报告模板最后一节。

### 5. 交给出片流程

**图文动效类**照 [图文动效路线](references/motion-graphics-route.md) 做：旁白稿仍交给
`$short-drama-write`，配音走 `$short-drama-produce` 的报价和确认，动画、混音和渲染在 HyperFrames 里做，
不经过出图、出视频和剪辑那几个技能。第二次停下是**试听选声和定配乐**：创作者存过「我的音色」就先问他用不用；
不用就从 58 个中文音色里按原片挑四个给他试听，听中的可以存起来以后复用。然后让他
再听两首候选配乐（从技能附带的 Mixkit 免费曲库里按情绪挑，`scripts/fetch_music.py` 负责下载和定音量），
选定了才配全片。

**剧情和实拍类**：方案定稿后补全逐镜对照和全片统一设定，然后照 [交接](references/handoff.md) 依次交给
`$short-drama`（建项目、写节奏档案）、`$short-drama-write`、`$short-drama-assets`、
`$short-drama-image-prompts`、`$short-drama-storyboard`、`$short-drama-video-prompts`、
`$short-drama-produce`、`$short-drama-edit`。每份文档仍由它原来的技能写，本技能不越俎代庖。

**第二次停下**在生产环节：每批图片和视频都先报价，创作者确认才花钱。这道闸门归
`$short-drama-produce`，你不代他确认，也不为省事跳过。

### 6. 成片对照，第三次停下

成片出来后先让脚本量一遍，和原片比：

```bash
python3 <本技能目录>/scripts/media_profile.py <成片.mp4> --compare <参考片目录>/source.profile.json
```

它会逐项说通过还是不通过：画面有没有长时间不动、开头三秒动没动、有没有冷场、原片垫了配乐新片有没有垫、
音量够不够、语速拖不拖。不通过的项先自己修；修不了的写清原因。再按复刻尺度里的「成片对照检查」
把新片和原片并排对一遍，写进 `复刻方案.md` 最后一节，和成片一起交给创作者，附一句「这条照原片学了
哪几招」。不通过的项，只把那一段交回负责的技能重做。

**交付成片时，一定让创作者能直接找到文件夹**（草稿版和正式版都照做）：

1. 消息里单独一行，用代码块写出成片所在**文件夹**的完整路径。Windows 用反斜杠，方便他复制到资源管理器的地址栏。
2. 再给一个指向这个文件夹的链接。链接要指向文件夹，不是视频文件；视频和字幕的链接放在它后面。
3. 直接替他打开文件夹并选中成片。Windows 在 PowerShell 里这样写：

```powershell
$video = '<成片的完整路径，用反斜杠>'
Start-Process explorer.exe -ArgumentList ('/select,"' + $video + '"')
```

   不要写成 `explorer.exe /select,"$video"`。PowerShell 会把引号转义坏，结果打开的是「文档」文件夹。
   macOS 用 `open -R "<成片的完整路径>"`。

## 识别台词（可选）

```bash
python3 <本技能目录>/scripts/transcribe.py <参考片目录>/source.mp4
```

它用 faster-whisper 在本机识别（CPU、INT8、默认 `small` 模型），结果写成同目录的
`transcript.json` 和 `transcript.txt`。纯音乐段容易幻听出「请点赞订阅」这类话，脚本会标成「存疑」，
以画面为准。

脚本说 `need_setup` 时，问创作者要不要装：第一次要下载几百 MB（程序加模型），装一次以后都能用。
他同意就建一个独立环境，不装进系统 Python：

```bash
python3 -m venv ~/.drama-skills/asr-venv
~/.drama-skills/asr-venv/bin/pip install faster-whisper      # Windows：~/.drama-skills/asr-venv/Scripts/pip
```

以后用这个环境里的 Python 运行 `transcribe.py`。在国内下载模型很慢或失败时，运行前设
`HF_ENDPOINT=https://hf-mirror.com` 走镜像。他不想装就跳过，台词改读画面字幕。

口播多的片子（讲解、科普、财经）建议装：新稿该写多少字，要按原片的语速算，语速要靠识别结果才量得准。
跑完识别再跑一次 `media_profile.py`，语速就会进 `source.profile.json`。

## 守则

- 只学结构和节奏，原片内容一样都不搬；照搬的要求不照做，说清原因，给一个最接近的改法。
- 不用真人的脸和声音去克隆，不模仿原作者本人。
- 生成的视频保留 AI 生成标识。
- 原片和拆解材料只留在项目里自己用，不外传、不转发。
- 媒体生成一律走 `$short-drama-produce` 的报价和确认，不用你所在环境自带的出图或出视频工具绕过去。

## 参考文档

- [拆解方法](references/analysis-framework.md)：事实和推测怎么分、钩子两条线、结构和节奏数字、三层拆开、能仿几成
- [复刻尺度](references/remake-policy.md)：中度和大胆两档、写新内容的规矩、成片对照检查
- [报告模板](references/report-templates.md)：拆解报告、复刻方案、第一个停顿点的三条路
- [交接](references/handoff.md)：剧情和实拍类：建项目、节奏档案、交给各技能的顺序和要点
- [图文动效路线](references/motion-graphics-route.md)：K 线、图表、文字讲解类怎么写稿、试听选声、定配乐、
  用 HyperFrames 做动画、混音和检查；附 [K 线起步模板](assets/kline-starter/index.html)、
  [免费配乐清单](references/music-catalog.json) 和配乐脚本 [fetch_music.py](scripts/fetch_music.py)
- [抖音：用你自己的浏览器取作品详情](references/douyin-in-browser.md)
- [拉片工具使用说明](vendor/video-shots/guide.md)：随技能附带，来源见同目录 `ATTRIBUTION.md`
- [方案网页预览脚本](scripts/plan_preview.py)：把复刻方案和拆解报告排成一页网页
- [中文音色清单](references/voice-catalog.json) 和 [我的音色脚本](scripts/my_voices.py)：试听挑候选、存下听中的音色以后复用
- [借鉴来源](references/credits.md)
