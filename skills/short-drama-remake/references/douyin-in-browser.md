# 抖音：用你自己的浏览器取作品详情

先试 `scripts/douyin_edge.mjs`：它用电脑自带的 Edge 或 Chrome 在后台打开作品页，做的就是下面第一条路，
不需要你自己的浏览器，也不弹窗口（用法见 SKILL.md 第 1 步）。它返回 `ok: false`，或者电脑上没有 Edge、Chrome，
再按本页用你自己的浏览器。

抖音的分享页不登录就不带视频信息，`fetch_reference.py fetch` 遇到抖音链接会停下，返回
`need_browser` 和作品号。抖音网页自己能给请求签名，所以由你所在环境的浏览器打开作品页，
再在页面里取作品详情。Codex 用它自带的浏览器（应用内浏览器，或 Chrome 插件），Claude Code
用它能用的浏览器工具。没有浏览器时直接走第三条路。

全程不登录、不填密码、不扫码。页面弹出登录框就关掉它，继续往下做；出现验证码或滑块就停下，
不去绕，直接改走第三条路。

## 第一条路：在页面里请求作品详情

1. 打开 `www.douyin.com/video/<作品号>`（前面加 `https://`），等页面加载完。
2. 先静音：页面会自动播放视频和音乐，创作者可能就坐在电脑旁边。

   ```js
   document.querySelectorAll('video,audio').forEach(v => { v.muted = true; v.pause(); });
   ```

3. 在页面里执行下面这段。它只取回下载要用的几项（大约 5KB），不把整份详情塞进对话：

   ```js
   async (id) => {
     const url = `/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web&aweme_id=${id}`
       + `&pc_client_type=1&version_code=190500&version_name=19.5.0&cookie_enabled=true&platform=PC`;
     const text = await new Promise(res => {
       const x = new XMLHttpRequest();
       x.open('GET', url); x.withCredentials = true;
       x.onload = () => res(x.responseText); x.onerror = () => res('');
       x.send();
     });
     let d; try { d = JSON.parse(text).aweme_detail; } catch (e) { return { ok: false, head: text.slice(0, 80) }; }
     if (!d) return { ok: false, head: text.slice(0, 80) };
     const v = d.video || {};
     const rates = (v.bit_rate || []).filter(b => !b.is_h265 && b.play_addr)
       .sort((a, b) => b.bit_rate - a.bit_rate).slice(0, 2)
       .map(b => ({ gear_name: b.gear_name, bit_rate: b.bit_rate, is_h265: 0, play_addr: { url_list: b.play_addr.url_list } }));
     return { ok: true, aweme_detail: {
       aweme_id: d.aweme_id, desc: d.desc, item_title: d.item_title,
       author: { nickname: (d.author || {}).nickname }, statistics: d.statistics,
       video: { duration: v.duration, width: v.width, height: v.height, bit_rate: rates,
                play_addr: { url_list: (v.play_addr || {}).url_list } } } };
   }
   ```

   一定要用 `XMLHttpRequest`：抖音的安全组件只给它签名，用 `fetch` 重放会拿回一整页 HTML。
4. 返回 `ok: true` 时，把返回值原样存成 `<参考片目录>/douyin_detail.json`，再运行：

   ```bash
   python3 <本技能目录>/scripts/fetch_reference.py douyin-detail <参考片目录>/douyin_detail.json --out <参考片目录>
   ```

   视频地址带签名、几分钟就过期，所以存好马上下，别隔一轮再下。

不登录时，详情里的点赞、评论数都是 0，这不是数据错了。要互动数据，就请创作者从 App 里读给你，
或者直接不写。

## 第二条路：读页面自己收到的作品详情

第一条路返回 `ok: false`，比如浏览器的页面脚本是只读作用域、发不出这个请求，但浏览器提供
开发者调试接口（Codex 的 `cdp` 能力）时，就改读页面加载时自己收到的那份详情：

1. 打开 `Network.enable`，然后重新打开同一个作品页。
2. 在 `Network.responseReceived` 事件里，找地址里含 `/aweme/v1/web/aweme/detail/` 的那一条。
3. 用它的 `requestId` 调 `Network.getResponseBody`，拿到正文（一份 JSON）。
4. 把正文存成 `douyin_detail.json`，照第一条路的第 4 步下载。脚本认整份详情，也认上面那份精简版。

## 第三条路：请创作者在 App 里保存

两条路都不通，或者出现了验证码，就请创作者在抖音 App 里点「分享 → 保存本地」（有的作品作者
关了下载，那就录屏），把文件放进项目，再运行：

```bash
python3 <本技能目录>/scripts/fetch_reference.py local <视频文件> --out <参考片目录>
```

有水印不影响拆解。

## 用完

关掉这个标签页。不要留着它继续自动播放。
