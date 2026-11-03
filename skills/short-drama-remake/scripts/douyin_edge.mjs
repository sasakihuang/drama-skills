#!/usr/bin/env node
// Fetch one Douyin work's detail with the computer's own Edge or Chrome, run headless.
//
//   node douyin_edge.mjs <作品号> --out <参考片目录>
//
// The fallback for when the agent has no usable built-in browser. It starts the installed
// Edge (or Chrome) without a window and with a throwaway profile, opens the public work page,
// lets Douyin's own page sign the detail request (the same request as douyin-in-browser.md,
// step one), and saves the small result as <参考片目录>/douyin_detail.json. Then run
// `fetch_reference.py douyin-detail` at once: the video address expires within minutes.
// No login, no cookies kept, nothing typed into the page. A verification page stops it.
// Needs Node 22 or newer (built-in WebSocket). The last stdout line is one JSON object.

import { spawn } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const out = (o, code = 0) => { console.log(JSON.stringify(o)); process.exit(code); };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const args = process.argv.slice(2);
const id = args.find((a) => /^\d{15,22}$/.test(a));
const outAt = args.indexOf("--out");
if (!id || outAt < 0 || !args[outAt + 1]) out({ ok: false, error: "用法：node douyin_edge.mjs <作品号> --out <参考片目录>" }, 2);
if (typeof WebSocket === "undefined") out({ ok: false, error: "需要 Node.js 22 或更新版本" }, 2);
const folder = resolve(args[outAt + 1]);

const candidates = [
  process.env.DOUYIN_BROWSER,
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
  process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, "Google\\Chrome\\Application\\chrome.exe"),
  "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium",
].filter(Boolean);
const browser = candidates.find((p) => existsSync(p));
if (!browser) out({ ok: false, error: "找不到 Edge 或 Chrome；可设环境变量 DOUYIN_BROWSER 指向浏览器程序" }, 2);

// The detail request, as in douyin-in-browser.md; only the fields the download needs come back.
const DETAIL = `(async () => {
  document.querySelectorAll('video,audio').forEach(v => { v.muted = true; v.pause(); });
  const url = '/aweme/v1/web/aweme/detail/?device_platform=webapp&aid=6383&channel=channel_pc_web&aweme_id=${id}'
    + '&pc_client_type=1&version_code=190500&version_name=19.5.0&cookie_enabled=true&platform=PC';
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
})()`;

const profile = mkdtempSync(join(tmpdir(), "douyin-edge-"));
const child = spawn(browser, [
  "--headless=new", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
  "--mute-audio", "--no-first-run", "--no-default-browser-check", "--disable-extensions",
  "--window-size=1366,900", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"], windowsHide: true });

const exited = new Promise((res) => child.on("exit", res));
// Close the browser, wait for it to let go of the profile, remove the profile, then report.
async function finish(result, code = 0) {
  try { if (typeof ws !== "undefined") ws.close(); } catch {}
  try { child.kill(); } catch {}
  await Promise.race([exited, sleep(5000)]);
  for (let i = 0; i < 5; i++) {
    try { rmSync(profile, { recursive: true, force: true }); break; } catch { await sleep(500); }
  }
  out(result, code);
}

const endpoint = await new Promise((res) => {
  let buf = "";
  const timer = setTimeout(() => res(null), 20000);
  child.stderr.on("data", (c) => {
    buf += c.toString();
    const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
    if (m) { clearTimeout(timer); res(m[1]); }
  });
  child.on("exit", () => { clearTimeout(timer); res(null); });
});
if (!endpoint) await finish({ ok: false, error: "浏览器没能以后台方式启动", browser }, 1);

var ws = new WebSocket(endpoint);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
let seq = 0;
const pending = new Map();
const waiters = [];
ws.onmessage = (e) => {
  const msg = JSON.parse(e.data);
  if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); return; }
  for (const w of waiters) if (w.method === msg.method && (!w.session || w.session === msg.sessionId)) w.res(msg);
};
const send = (method, params = {}, sessionId) => new Promise((res) => {
  const msgId = ++seq;
  pending.set(msgId, res);
  ws.send(JSON.stringify({ id: msgId, method, params, ...(sessionId ? { sessionId } : {}) }));
});
const once = (method, session, ms) => new Promise((res) => {
  const w = { method, session, res };
  waiters.push(w);
  setTimeout(() => res(null), ms);
});

try {
  const { result: target } = await send("Target.createTarget", { url: "about:blank" });
  const { result: attached } = await send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
  const s = attached.sessionId;
  const { result: ver } = await send("Browser.getVersion");
  const agent = (ver.userAgent || "").replace("HeadlessChrome", "Chrome");
  await send("Network.setUserAgentOverride", { userAgent: agent, acceptLanguage: "zh-CN,zh" }, s);
  await send("Page.enable", {}, s);
  const loaded = once("Page.loadEventFired", s, 30000);
  await send("Page.navigate", { url: `https://www.douyin.com/video/${id}` }, s);
  await loaded;

  let detail = null;
  for (let attempt = 0; attempt < 4 && !detail; attempt++) {
    await sleep(3000 + attempt * 2000); // the page's signing component needs a moment
    const { result } = await send("Runtime.evaluate", { expression: DETAIL, awaitPromise: true, returnByValue: true }, s);
    const value = result && result.result && result.result.value;
    if (value && value.ok) detail = value;
  }
  if (!detail) {
    const { result } = await send("Runtime.evaluate", { expression: "document.title + ' | ' + location.href", returnByValue: true }, s);
    await finish({ ok: false, error: "页面没有给出作品详情；可能遇到了验证页，或作品已删除、仅自己可见",
          page: result && result.result && result.result.value,
          next: "改走 douyin-in-browser.md 的第三条路：请创作者在 App 里保存视频" }, 1);
  }
  mkdirSync(folder, { recursive: true });
  const file = join(folder, "douyin_detail.json");
  writeFileSync(file, JSON.stringify(detail, null, 1), "utf8");
  const d = detail.aweme_detail;
  await finish({ ok: true, file, title: d.item_title || d.desc, author: d.author.nickname,
        seconds: Math.round((d.video.duration || 0) / 1000), browser,
        next: "马上运行 fetch_reference.py douyin-detail <这个文件> --out <参考片目录>，地址几分钟就过期" });
} catch (error) {
  await finish({ ok: false, error: String(error && error.message || error) }, 1);
}
