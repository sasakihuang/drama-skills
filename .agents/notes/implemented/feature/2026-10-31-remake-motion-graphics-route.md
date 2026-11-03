# Agent Note: 爆款复刻加图文动效路线和声音、画面实测

Status: implemented

日期：2026-10-31

## Problem

复刻技能第一次在 Codex 里实跑，原片是一条 K 线讲解（黑底、红青 K 线、毛笔大字、全程垫配乐）。
交接表只通向 AI 出图、出视频的短剧流程，图文类原片没有做动画的路。助手只好用 mplfinance 和 PIL
逐帧拼图，成片 99.6% 的时间画面不动，最长 12.9 秒没变化，十段套同一个后台报表版式。
拆解阶段没跑台词识别，也没量声音，配音是盲选的：用了 MiniMax 的新闻播报女声，十段之间空
1 到 3 秒，还用 atempo 放慢到 0.9 倍去凑原片切点，没有配乐。新片 -16.5 LUFS，原片 -9.4 LUFS，
原片声音几乎不断。文案句句打折扣，原片那种干货口气没了。创作者的评价是别扭、动效 low、
配音像机器人。

## Decision

- 拆解时判片型。图文动效类走新的 `references/motion-graphics-route.md`：用 HyperFrames
  （heygen-com/hyperframes，Apache-2.0，钉 0.8.181）在本机做动画，明令不准用静态图库逐帧拼图。
  附 `assets/kline-starter/`：K 线生长、镜头推近、发光圈、大字标注、划线、标题冲击、火星，
  渲染工具、GSAP 3.14.2、两款 OFL 字体都写进 `package.json`，装在项目目录，渲染不连外网。
  HyperFrames 的匿名统计用 `HYPERFRAMES_NO_TELEMETRY=1` 关掉。
- 新增 `scripts/media_profile.py`（标准库加 ffmpeg）：量响度、停顿、配乐（停顿极少即判有垫底）、
  画面最长不动（freezedetect）、有识别结果时的语速；`--compare` 拿成片对原片逐项判。用翻车成片
  实测：它报出 6 项不通过，原片全部达标。
- 声音：画面跟着配音走，不把配音塞进原片切点；配全片前先出三四个音色试听（用账号的
  get_voice 列表核过编号），播音腔不进默认候选；快慢只用 `speed` 参数调，后期 atempo 不小于 1；
  配乐来源依次是创作者自有授权、登录 HeyGen 曲库、都没有就只加音效并先告知；
  音效用 HyperFrames 包里自带的 Pixabay 音效。混音命令（sidechaincompress 压配乐、loudnorm）实测过。
- 文案：字数按原片实测语速算，口气照原片，风险提示只在片尾说一次。
- 复刻尺度的声音一行补「播音腔不算同语气」「原片垫配乐新片也垫」，加「口气」一行；成片对照检查加
  画面不僵、声音接得上、口气对上三项。

## Alternatives considered

- HyperFrames 的 `reverse-any-video`：要 HeyGen 付费版，而且是逐帧照抄原片，违反只学结构的规矩。
- Remotion：公司超过 3 人商用要买授权；套件里只在剪辑字幕叠层可选使用。
- 继续交给 `$short-drama-produce` 出视频：AI 视频画不准 K 线和数字，贵，还要每镜报价。
- 让 HyperFrames 的技能装进 Codex：`skills` 命令会改全局技能目录，改为直接读
  `node_modules/hyperframes/dist/skills/hyperframes/SKILL.md` 和 `hyperframes docs`。
- MiniMax 音乐：2026-08-20 起不对新账号开放。

## Consequences

- 图文动效类要 Node 22 以上，项目里第一次 `npm install` 约 100 MB，渲染用的 Chrome 另下。
- `media_profile.py` 的「垫了配乐」只看停顿多少，人声极密的纯口播也可能被判为有垫底；它只是给创作者看的
  手艺检查，不是发布门槛。
- 本次只改技能，没有在 Codex 里重跑；下一步由用户在 Codex 实测。全套测试 766 项，失败 16 项，
  和 10-29 记录的 Windows 老失败数目一致，都在没改动的剪辑、导出、看板模块；ruff 0.15.11 全过。

## Follow-up 2026-11-01: 配乐改用 Mixkit

- 实测国内真直连（绑实体网卡、不走代理）：HeyGen 的官网、app、api 都在 TLS 握手时被重置，原先的「登录 HeyGen
  取曲库」在没有代理的电脑上用不了。用户第二次实跑时用的就是 HeyGen 曲库，靠的是本机代理。
- 比过的来源：Mixkit 直连 1 到 2 秒能下，免费商用、不用署名，选它；Incompetech 要署名；FMA 每首许可不同；
  FreePD 已关站；Pixabay、Chosic、爱给网对脚本回 403；剪映曲库要看画面挑，多数是会员歌，用户定为不进流程。
- `references/music-catalog.json`：32 首，8 种情绪，每首都下载并量过响度和开头静音。只收录许可为
  Mixkit Stock Music Free License 的；Mixkit 条款不宜原样再分发，所以不打包音频，用时按编号现下。
- `scripts/fetch_music.py`：`list` 看清单；`fetch` 下载（3 次重试、带浏览器 UA，否则 403），按
  `--voice` 实测响度把配乐定在旁白下 13 LU，`--length` 时生成 `配乐垫底.wav`（去掉开头静音和结尾
  -35 dB 以下的淡出尾巴，1.5 秒交叉淡化循环，淡入 0.5 秒、淡出 1.5 秒），另写 `配乐来源.md`。
  清单外编号一律拒绝。测试白名单登记了它的两个网址。
- 实测：18 秒和 260 秒两种长度都出过垫底，长的拼了 3 到 5 遍，接缝没有空档；和旁白、音效混音后
  -13.4 LUFS，真峰值 -1.5，`continuous_bed` 为真。全套测试仍是原来那 16 个 Windows 老失败。

## Follow-up 2026-11-02: 方案网页预览

- 用户看不惯 Markdown 排版，要求复刻方案出 HTML 预览。先试了在 Codex 内置浏览器里自动打开：内置浏览器只认
  http/https（file:// 被 URL 策略拒绝），用 Start-Process 起的后台预览服务被安全策略拦下；官方 sites 技能的做法
  是常驻会话里起服务、再 open_in_codex。用户随后定：不必强制打开，给链接让他自己点即可，所以没有做服务。
- `scripts/plan_preview.py`（标准库）：把 `复刻方案.md` 和 `拆解报告.md` 排成一页 `复刻方案预览.html`，放在参考片
  目录：左侧目录、顶部关键数字、要点列表做成卡片、「对照」表格做成时间条和段落卡片并配 `frames/<镜号>a.jpg`、
  代码块折叠、大字和打印按钮。Markdown 只覆盖本技能文档用到的写法，不为此引第三方解析库。
- 交付成片时写文件夹路径并用 `Start-Process explorer.exe -ArgumentList ('/select,"' + $video + '"')` 打开；
  PowerShell 直接写 `explorer.exe /select,"$video"` 会转义坏引号、打开「文档」。

## Follow-up 2026-11-02: 更多音色和「我的音色」

- 用户反馈试听每次都是精英青年、真诚青年、温暖闺蜜、御姐。原因是路线文档写死了 8 个推荐，又写了「男女各两个」，
  模型每次都取表头。账号 get_voice（all）有 58 个中文系统音色，没有克隆或设计音色。
- `references/voice-catalog.json`：58 个音色，带男女、年纪、劲头、适用范围（讲解 36、角色 14、播音 2、童声卡通 6）
  和口音；没有官方描述的旧音色按名字补写，最终以试听为准。默认只从「讲解」类挑，四个之间拉开劲头，项目内
  已试听的记在 `制作/试听/已试听.json`，「换一批」不重复。
- `scripts/my_voices.py`（标准库，不联网、不花钱）：list / save / used / remove，存在 `~/.drama-skills/我的音色/`，
  连同试听录音一起存；同名再存就更新。试听前先列出「我的音色」，选了存好的就跳过试听。
- 音色设计（按描述生成专属音色）要花钱、要另外报价，用户同意先不做。

## Follow-up 2026-11-03: 抖音改用系统 Edge 后台取

- 另一位朋友装好技能后，抖音链接 13 秒就放弃、让他上传视频：短链能解析出作品号，卡在「用你自己的浏览器」那一步，
  他的 Codex 没有能用的内置浏览器，或者打不开抖音。同一条链接在本机用页面内 XHR 能取到（主升狼王「一夜持股法
  八大步骤」，170 秒，1080p）。
- `scripts/douyin_edge.mjs`（Node 22 内置 WebSocket，无依赖）：找系统里的 Edge 或 Chrome，用无头模式、临时配置
  启动，走 CDP 打开作品页，把 UA 里的 HeadlessChrome 换掉，等页面签名组件就绪后执行和第一条路相同的 XHR，存
  `douyin_detail.json`；结束时等浏览器退出再删临时配置。本机实测 5.8 秒，随后 `douyin-detail` 下到 1080p 原片。
- 现成方案没选：JoeanAmier 等下载器要配 cookie；Playwright 要另下浏览器（上百 MB）。系统 Edge 每台 Windows 都有。
- SKILL.md 第 1 步改成先跑它，返回 ok:false 再用自带浏览器，最后才请创作者保存；明确不许没试就让创作者上传。
