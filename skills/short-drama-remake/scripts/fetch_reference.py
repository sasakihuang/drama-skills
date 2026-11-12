#!/usr/bin/env python3
"""Fetch one reference video for remaking, and nothing else.

The creator pastes a share text or link from Douyin, Kuaishou or Xiaohongshu;
this script saves the original video as ``source.mp4`` with a ``source.json``
fact sheet next to it. It never uploads, posts, logs in or stores credentials.

Subcommands
-----------
``fetch <分享文字或链接> --out <dir>``
    Kuaishou and Xiaohongshu: open the share link the way a phone does and read
    the video address that the share page already carries. No login, no cookie.
    Douyin: the share page carries nothing without the site's own signing, so
    this runs scripts/douyin_edge.mjs (the computer's own Edge or Chrome,
    headless, needs Node 22) to read the work detail and downloads at once.
    Only when that cannot be done does it print ``need_browser`` with the work
    id; the agent then reads the detail in its own browser (see
    references/douyin-in-browser.md) and calls ``douyin-detail``.
``douyin-detail <detail.json> --out <dir>``
    Pick the best H.264 rendition from a saved Douyin work-detail response and
    download it.
``local <video file> --out <dir>``
    Take a video the creator saved from the app (the fallback when a platform
    blocks fetching). Watermarks do not matter for analysis.

The last stdout line is always one JSON object: ``{"ok": true, ...}`` or
``{"ok": false, "error": ...}``. Exit code 0 on success, 1 on failure, 3 when
Douyin needs the browser step.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MINIMUM_PYTHON = (3, 9)

UA_PHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)
UA_DESKTOP = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"
)
DOUYIN_HOME = "https://www.douyin.com"
XHS_HOME = "https://www.xiaohongshu.com"
XHS_VIDEO_CDN = "https://sns-video-bd.xhscdn.com"
URL_RE = re.compile(r"https?://[^\s\"'<>，。！？、【】《》]+")
TIMEOUT = 30


class FetchError(Exception):
    pass


def emit(ok: bool, code: int = 0, **fields: Any) -> None:
    print(json.dumps({"ok": ok, **fields}, ensure_ascii=False))
    sys.exit(code if code else (0 if ok else 1))


def first_url(text: str) -> str:
    match = URL_RE.search(text)
    if not match:
        raise FetchError("输入里没有链接。请把 App 里「分享 → 复制链接」得到的整段文字贴过来。")
    return match.group(0).rstrip(".,;)")


def open_url(url: str, user_agent: str, referer: Optional[str] = None):
    headers = {"User-Agent": user_agent, "Accept-Language": "zh-CN,zh;q=0.9"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=TIMEOUT)


def get_page(url: str, user_agent: str = UA_PHONE) -> Tuple[str, str]:
    """GET a page following redirects; return (final_url, text)."""
    with open_url(url, user_agent) as response:
        body = response.read()
        final = response.geturl()
        charset = response.headers.get_content_charset() or "utf-8"
    return final, body.decode(charset, errors="replace")


def platform_of(url: str) -> str:
    host = re.sub(r"^https?://", "", url).split("/")[0].lower()
    if "douyin" in host or "iesdouyin" in host:
        return "douyin"
    if "kuaishou" in host or "chenzhongtech" in host or "gifshow" in host:
        return "kuaishou"
    if "xhslink" in host or "xiaohongshu" in host or "rednote" in host:
        return "xhs"
    return "other"


def download(urls: List[str], dest: Path, referer: str) -> str:
    """Try candidate addresses in order; return the one that worked."""
    last: Optional[BaseException] = None
    tmp = dest.with_name(dest.name + ".part")
    for url in urls[:6]:
        try:
            with open_url(url, UA_DESKTOP, referer) as response, open(tmp, "wb") as out:
                shutil.copyfileobj(response, out, 1 << 20)
            if tmp.stat().st_size < 10_000:
                raise FetchError("下载到的文件太小，不像视频")
            tmp.replace(dest)
            return url
        except (urllib.error.URLError, OSError, FetchError) as error:
            last = error
    if tmp.exists():
        tmp.unlink()
    raise FetchError(f"视频地址都下不动：{last}")


def probe(path: Path) -> Dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {"probe": "没装 ffprobe，未测"}
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        data = json.loads(out.stdout or "{}")
    except ValueError:
        return {"probe": "ffprobe 读不出"}
    facts: Dict[str, Any] = {"duration_s": round(float(data.get("format", {}).get("duration", 0) or 0), 2)}
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            facts["video"] = f'{stream.get("codec_name")} {stream.get("width")}x{stream.get("height")}'
        elif stream.get("codec_type") == "audio":
            facts["audio"] = stream.get("codec_name")
    facts.setdefault("audio", "无")
    return facts


def finish(out: Path, platform: str, meta: Dict[str, Any]) -> None:
    video = out / "source.mp4"
    meta = {"platform": platform, "fetched_at": time.strftime("%Y-%m-%d %H:%M"), **meta, **probe(video)}
    (out / "source.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(True, platform=platform, path=str(video), meta=meta)


# ---------------------------------------------------------------- Kuaishou
def kuaishou(url: str, out: Path) -> None:
    final, html = get_page(url)
    start = html.find('"photo":{')
    if start < 0:
        raise FetchError(
            "快手分享页里没有作品数据。电脑网页版链接（kuaishou.com/short-video/…）不登录拿不到，"
            "请用 App 里「分享 → 复制链接」得到的 v.kuaishou.com 链接，或在 App 里保存视频后用 local。"
        )
    photo, _ = json.JSONDecoder().raw_decode(html[html.index("{", start):])
    candidates = [item.get("url") for item in photo.get("mainMvUrls") or []]
    for adaptation in (photo.get("manifest") or {}).get("adaptationSet") or []:
        reps = sorted(adaptation.get("representation") or [], key=lambda r: r.get("maxBitrate") or 0, reverse=True)
        for rep in reps:
            candidates += [rep.get("url")] + list(rep.get("backupUrl") or [])
    candidates = [c for c in candidates if c]
    if not candidates:
        raise FetchError("这条快手作品不是视频（可能是图集）。")
    used = download(candidates, out / "source.mp4", final)
    finish(out, "kuaishou", {
        "id": photo.get("photoId"),
        "caption": photo.get("caption"),
        "author": photo.get("userName"),
        "likes": photo.get("likeCount"),
        "views": photo.get("viewCount"),
        "page_duration_s": round((photo.get("duration") or 0) / 1000, 2),
        "share_url": url,
        "media_host": re.sub(r"^https?://", "", used).split("/")[0],
    })


# ------------------------------------------------------------- Xiaohongshu
def xhs_note(html: str) -> Optional[Dict[str, Any]]:
    marker = "window.__INITIAL_STATE__="
    start = html.find(marker)
    if start < 0:
        return None
    body = html[start + len(marker):]
    body = body[: body.find("</script>")].strip().rstrip(";")
    body = re.sub(r"(?<=[:\[,])undefined(?=[,\]}])", "null", body)
    try:
        return json.loads(body)["noteData"]["data"]["noteData"]
    except (ValueError, KeyError, TypeError):
        return None


def xhs(url: str, out: Path) -> None:
    _final, html = get_page(url)
    note = xhs_note(html)
    if not note:
        raise FetchError(
            "小红书分享页没给笔记数据。分享链接放久了会失效（「当前笔记暂时无法浏览」），"
            "请重新复制一次分享链接，或在 App 里保存视频后用 local。"
        )
    if note.get("type") != "video":
        raise FetchError(f"这条是图文笔记，不是视频：{note.get('title') or ''}")
    video = note.get("video") or {}
    candidates: List[str] = []
    key = (video.get("consumer") or {}).get("originVideoKey")
    if key:
        candidates.append(XHS_VIDEO_CDN + "/" + key)
    streams = []
    for codec, items in ((video.get("media") or {}).get("stream") or {}).items():
        for item in items or []:
            streams.append((codec == "h264", item.get("height") or 0, item))
    streams.sort(key=lambda s: (s[0], s[1]), reverse=True)
    for _h264, _height, item in streams:
        candidates += list(item.get("backupUrls") or []) + [item.get("masterUrl")]
    candidates = [c for c in candidates if c]
    if not candidates:
        raise FetchError("笔记里没找到视频地址。")
    used = download(candidates, out / "source.mp4", XHS_HOME + "/")
    interact = note.get("interactInfo") or {}
    finish(out, "xhs", {
        "id": note.get("noteId"),
        "title": note.get("title"),
        "desc": (note.get("desc") or "")[:800],
        "author": (note.get("user") or {}).get("nickname") or (note.get("user") or {}).get("nickName"),
        "likes": interact.get("likedCount"),
        "collects": interact.get("collectedCount"),
        "comments": interact.get("commentCount"),
        "share_url": url,
        "media_host": re.sub(r"^https?://", "", used).split("/")[0],
    })


# ------------------------------------------------------------------ Douyin
def douyin_id(url: str) -> str:
    target = url
    if "v.douyin.com" in url or "iesdouyin" in url:
        try:
            target, _ = get_page(url)
        except (urllib.error.URLError, OSError):
            target = url
    match = re.search(r"(?:video/|note/|modal_id=|aweme_id=)(\d{15,20})", target) or re.search(r"(\d{19})", target)
    if not match:
        raise FetchError("没从抖音链接里认出作品号。")
    return match.group(1)


EDGE_SCRIPT = Path(__file__).resolve().with_name("douyin_edge.mjs")
EDGE_TIMEOUT = 120


def edge_detail(work_id: str, out: Path, command: Optional[List[str]] = None) -> Tuple[Optional[Path], str]:
    """Read one work's detail with the computer's own browser, headless.

    Returns (saved detail file, "") or (None, why it could not be done)."""
    if command is None:
        node = shutil.which("node")
        if not node:
            return None, "没装 Node.js"
        version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
        major = re.match(r"v(\d+)", version)
        if not major or int(major.group(1)) < 22:
            return None, f"Node.js 版本是 {version or '未知'}，后台取抖音要 22 或更新"
        command = [node, str(EDGE_SCRIPT)]
    try:
        run = subprocess.run(command + [work_id, "--out", str(out)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=EDGE_TIMEOUT,
                             env={**os.environ, "NO_COLOR": "1"})
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, f"后台浏览器没跑成：{type(error).__name__}"
    lines = [line for line in (run.stdout or "").splitlines() if line.strip()]
    try:
        result = json.loads(lines[-1]) if lines else {}
    except ValueError:
        result = {}
    detail = out / "douyin_detail.json"
    if result.get("ok") and detail.is_file():
        return detail, ""
    return None, str(result.get("error") or "后台浏览器没有返回作品详情")


def douyin_detail(detail_file: Path, out: Path) -> None:
    raw = json.loads(detail_file.read_text(encoding="utf-8"))
    detail = raw.get("aweme_detail") if isinstance(raw, dict) and "aweme_detail" in raw else raw
    if not isinstance(detail, dict) or not detail.get("video"):
        raise FetchError("这份 JSON 里没有 aweme_detail.video：浏览器那一步拿到的不是作品详情。")
    video = detail["video"]
    rates = [b for b in video.get("bit_rate") or [] if not b.get("is_h265") and b.get("play_addr")]
    rates.sort(key=lambda b: b.get("bit_rate") or 0, reverse=True)
    play = (rates[0]["play_addr"] if rates else video.get("play_addr")) or {}
    urls = list(play.get("url_list") or [])
    if not urls:
        raise FetchError("作品详情里没有视频地址（可能是图文作品）。")
    stats = detail.get("statistics") or {}
    download(urls, out / "source.mp4", DOUYIN_HOME + "/")
    finish(out, "douyin", {
        "id": detail.get("aweme_id"),
        "title": detail.get("item_title") or detail.get("desc"),
        "desc": detail.get("desc"),
        "author": (detail.get("author") or {}).get("nickname"),
        "likes": stats.get("digg_count"),
        "comments": stats.get("comment_count"),
        "shares": stats.get("share_count"),
        "collects": stats.get("collect_count"),
        "page_duration_s": round((video.get("duration") or 0) / 1000, 2),
        "rendition": rates[0].get("gear_name") if rates else "play_addr",
    })


# ------------------------------------------------------------------- main
def cmd_fetch(text: str, out: Path, use_browser: bool = True) -> None:
    url = first_url(text)
    platform = platform_of(url)
    if platform == "kuaishou":
        kuaishou(url, out)
    elif platform == "xhs":
        xhs(url, out)
    elif platform == "douyin":
        work_id = douyin_id(url)
        reason = "已按 --no-browser 跳过"
        if use_browser:
            detail, reason = edge_detail(work_id, out)
            if detail is not None:
                douyin_detail(detail, out)
        emit(False, 3, platform="douyin", need_browser=True, id=work_id,
             page=DOUYIN_HOME + "/video/" + work_id, tried=reason,
             next="电脑自带的浏览器没取到。照 references/douyin-in-browser.md 用你自己的浏览器取作品详情，再运行 douyin-detail；还不行就请创作者在 App 里保存视频")
    else:
        raise FetchError("目前只支持抖音、快手、小红书的分享链接；别的平台请先在 App 里保存视频，再用 local。")


def cmd_local(video: Path, out: Path) -> None:
    if not video.is_file():
        raise FetchError(f"找不到这个文件：{video}")
    shutil.copyfile(video, out / "source.mp4")
    finish(out, "local", {"original_name": video.name})


def main() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        emit(False, error="需要 Python %d.%d 或更新版本" % MINIMUM_PYTHON)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="取一条参考视频，存成 source.mp4 + source.json")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_fetch = sub.add_parser("fetch", help="抖音/快手/小红书分享文字或链接")
    p_fetch.add_argument("text")
    p_fetch.add_argument("--no-browser", action="store_true", help="抖音不调用电脑自带的浏览器，只返回作品号")
    p_detail = sub.add_parser("douyin-detail", help="浏览器取到的抖音作品详情 JSON")
    p_detail.add_argument("detail")
    p_local = sub.add_parser("local", help="创作者自己保存的视频文件")
    p_local.add_argument("video")
    for p in (p_fetch, p_detail, p_local):
        p.add_argument("--out", required=True, help="参考片目录，例如 项目开发/remake/R01")
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    try:
        if args.cmd == "fetch":
            cmd_fetch(args.text, out, not args.no_browser)
        elif args.cmd == "douyin-detail":
            douyin_detail(Path(args.detail).expanduser().resolve(), out)
        else:
            cmd_local(Path(args.video).expanduser().resolve(), out)
    except FetchError as error:
        emit(False, error=str(error))
    except (urllib.error.URLError, OSError, ValueError) as error:
        emit(False, error=f"{type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
