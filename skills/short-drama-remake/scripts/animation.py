#!/usr/bin/env python3
"""Set up, time, check and render a motion-graphics film, without opening anything on the creator's screen.

    animation.py setup    <动画目录> [--portrait]
    animation.py build    <动画目录> [--timetable <制作/时间表.json>] [--mix <制作/混音.wav>]
    animation.py cues     <动画目录>
    animation.py check    <动画目录>
    animation.py snapshot <动画目录> [--at 1.5,4,9]
    animation.py render   <动画目录> --output <成片.mp4> [--final]
    animation.py tool     <动画目录> -- <其他 hyperframes 命令>

``setup`` installs HyperFrames once for the whole computer, under ``~/.drama-skills/hyperframes``
(about 200 MB, versions pinned in ``assets/motion-kit/package.json``), and copies the motion kit
into the film's folder: ``index.html``, ``lib/`` (the kit, the animation library and five font
files, about 6 MB) and a sample ``film.json``. A film folder never gets its own ``node_modules``.

``build`` turns ``film.json`` (the scenes, written by the agent) and ``时间表.json`` (when each
sentence is spoken, from ``voice_track.py``) into ``film.js``: every scene and cue with its time
in seconds, and the caption lines. A cue's ``at`` can quote a few words of the narration and lands
when the voice says them. It also writes ``音效.json`` (which effect at which second, for
``mix_audio.py``) and sets the film's length in ``index.html``. Timing lives in one place, here.
A film may name a ``style`` from the style library (``assets/styles``): its scenes are then all
custom, drawn in that style, and a light backdrop is allowed. ``build`` wires ``custom.js`` into the page.

``cues`` prints the timed film as a list: what appears when, beside what the voice says at that
moment. ``check``, ``snapshot`` and ``render`` run HyperFrames against the folder. Usage statistics, the
update check and its own skill installer are switched off for every call, and the commands that
open a browser window or a desktop app, upload the project, or install skills are refused.
The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import zh_text

MINIMUM_PYTHON = (3, 9)

KIT = Path(__file__).resolve().parents[1] / "assets" / "motion-kit"
MIRROR = "https://registry.npmmirror.com"
QUIET = {"HYPERFRAMES_NO_TELEMETRY": "1", "HYPERFRAMES_NO_UPDATE_CHECK": "1", "HYPERFRAMES_SKIP_SKILLS": "1", "NO_COLOR": "1"}
# Read-only or local-only commands. Anything else (preview, play, present, open, publish, skills,
# auth, cloud, tts, ...) opens a window, leaves this computer, or changes the agent's own setup.
ALLOWED_TOOLS = {"check", "lint", "validate", "snapshot", "render", "docs", "doctor", "browser", "info",
                 "compositions", "timeline", "inspect", "keyframes", "audio", "beats", "compare", "clean"}
FONTS = (
    # The two lighter weights are for styled films: glass, line and grid looks are set in light type.
    ("@fontsource/noto-sans-sc/files", "noto-sans-sc-chinese-simplified-400-normal.woff2"),
    ("@fontsource/noto-sans-sc/files", "noto-sans-sc-chinese-simplified-500-normal.woff2"),
    ("@fontsource/noto-sans-sc/files", "noto-sans-sc-chinese-simplified-700-normal.woff2"),
    ("@fontsource/noto-sans-sc/files", "noto-sans-sc-chinese-simplified-900-normal.woff2"),
    ("@fontsource/ma-shan-zheng/files", "ma-shan-zheng-chinese-simplified-400-normal.woff2"),
)
SCENE_NEEDS = {"title": ("text",), "kline": (), "points": ("items",), "number": ("value",), "bars": ("items",),
               "compare": ("left", "right"), "statement": ("lines",), "end": ("text",), "custom": ("fn",)}
CUE_LISTS = ("focus", "lines", "items", "notes")
# The style library: fifteen looks a film can take instead of the kit's dark chart-and-gold one
# (from mg-styles-15; choosing and building one is in references/styles.md).
STYLES = Path(__file__).resolve().parents[1] / "assets" / "styles"
# Effects a custom scene may ask for by name, from the library HyperFrames ships, and how loud
# beside the narration (1 would be as loud as the voice; the kit's own sit at 0.45 to 0.6).
CUSTOM_SOUNDS = ("pop.mp3", "click.mp3", "click-soft.mp3", "chime.mp3", "ping.mp3", "sparkle.mp3", "notification.mp3",
                 "whoosh.mp3", "whoosh-short.mp3", "whoosh-cinematic.mp3", "impact-bass-1.mp3", "impact-bass-2.mp3",
                 "riser.mp3", "typing.mp3", "key-press.mp3", "glitch-1.mp3", "glitch-2.mp3", "glitch-3.mp3")
CUSTOM_VOLUME = 0.5
SOUND_RANGE = (0.2, 0.7)
TRANSITIONS = ("slide", "fade", "cut")
# What the kit's charts understand; anything else it would quietly draw as something else.
SHAPES = ("breakout", "fakeout", "up", "down", "range", "top", "bottom")
PRICE_NAMES = ("support", "resistance")
FOCUS_POINTS = ("high", "low", "volume")
BAR_NAME = re.compile(r"(key|last|first)\s*([+-]\s*\d+)?")
TAIL = 0.8
# A focus note is readable at its `at`; the camera sets off this much earlier (the kit agrees).
FOCUS_LEAD = 0.7
# Sound effects: about one every EFFECT_EVERY seconds within each stretch of narration, the kinds
# that carry meaning first; without a timetable the film is cut into EFFECT_WINDOW-second stretches.
EFFECT_EVERY = 4.0
EFFECT_WINDOW = 15.0
EFFECT_GAP = 0.35
# riser.mp3 is silent for a second and a half and crests this long after it starts, measured on the
# file HyperFrames 0.8.181 ships (its own note says the peak is at the end of the ten seconds; it is not).
RISER_CREST = 3.3
# The page is drawn at this many frames a second, and a film's length is kept to whole frames. The
# renderer cuts the sound to the last frame; a length that ends between two frames makes it encode
# the sound a second time, and that second encoding is where its level check has gone wrong.
FPS = 30
# How much quieter than its mix a rendered film may be before the mix is put back, and the true
# peak the renderer delivers under, which a mix put back here has to pass as well.
SOUND_SLACK = 1.0
DELIVERY_PEAK_DB = -1.0
# Eight contact sheets of nine: what one pass of looking can take in.
SNAPSHOTS = 72

Mark = Tuple[str, float]


class Problem(Exception):
    pass


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def shared_home() -> Path:
    return Path(os.environ.get("DRAMA_SKILLS_HOME") or Path.home() / ".drama-skills") / "hyperframes"


def pinned() -> Dict[str, str]:
    return json.loads((KIT / "package.json").read_text(encoding="utf-8"))["dependencies"]


def installed_version(home: Path) -> str:
    manifest = home / "node_modules" / "hyperframes" / "package.json"
    return json.loads(manifest.read_text(encoding="utf-8")).get("version", "") if manifest.is_file() else ""


def node_program() -> str:
    node = shutil.which("node")
    if not node:
        raise Problem("没装 Node.js。图文动效要 Node.js 22 或更新的版本，先帮创作者装上。")
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    major = re.match(r"v(\d+)", version)
    if not major or int(major.group(1)) < 22:
        raise Problem(f"Node.js 是 {version or '未知版本'}，图文动效要 22 或更新。")
    return node


def ensure_install() -> Dict[str, Any]:
    home = shared_home()
    wanted = pinned()["hyperframes"]
    if installed_version(home) == wanted:
        return {"home": str(home), "version": wanted, "installed_now": False}
    npm = shutil.which("npm")
    if not npm:
        raise Problem("找不到 npm（它随 Node.js 一起装）。")
    home.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(KIT / "package.json", home / "package.json")
    errors: List[str] = []
    for registry in (MIRROR, ""):
        command = [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"] + (["--registry", registry] if registry else [])
        try:
            done = subprocess.run(command, cwd=home, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
        except (OSError, subprocess.TimeoutExpired) as error:
            errors.append(type(error).__name__)
            continue
        if done.returncode == 0 and installed_version(home) == wanted:
            return {"home": str(home), "version": wanted, "installed_now": True, "registry": registry or "npm 默认源"}
        errors.append(((done.stderr or done.stdout or "").strip().splitlines() or ["npm 失败"])[-1])
    raise Problem("HyperFrames 没装上（国内镜像和默认源都试过）：" + "；".join(errors))


def system_browser() -> str:
    """The computer's own Edge or Chrome, for when the renderer's browser cannot be downloaded."""
    candidates = []
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable, "")
        if base:
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                   "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                   "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/microsoft-edge"]
    return next((path for path in candidates if os.path.exists(path)), "")


def run_tool(folder: Path, arguments: Sequence[str], capture: bool = True, timeout: int = 3600) -> subprocess.CompletedProcess:
    home = shared_home()
    entry = home / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
    if not entry.is_file():
        raise Problem("这台电脑还没装动画工具：先运行 animation.py setup <动画目录>。")
    env = {**os.environ, **QUIET}
    chosen = home / "browser-path.txt"
    if chosen.is_file() and "HYPERFRAMES_BROWSER_PATH" not in os.environ:
        path = chosen.read_text(encoding="utf-8").strip()
        if path and os.path.exists(path):
            env["HYPERFRAMES_BROWSER_PATH"] = path
    return subprocess.run([node_program(), str(entry), *arguments], cwd=folder, env=env,
                          capture_output=capture, text=True, encoding="utf-8", errors="replace", timeout=timeout)


def plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


# ------------------------------------------------------------------- setup
def page_for(portrait: bool) -> str:
    """The kit's page at 1920x1080, or turned on its side for a vertical film."""
    html = (KIT / "index.html").read_text(encoding="utf-8")
    if portrait:
        # The page names its size in four places, always as 1920 wide by 1080 high.
        html = html.replace("1920", "@W@").replace("1080", "@H@").replace("@W@", "1080").replace("@H@", "1920")
    return html


def page_is_upright(html: str) -> bool:
    size = re.search(r'<div id="root"[^>]*data-width="(\d+)"[^>]*data-height="(\d+)"', html)
    return size is not None and int(size.group(2)) > int(size.group(1))


def cmd_setup(args: argparse.Namespace) -> None:
    install = ensure_install()
    folder: Path = args.folder
    (folder / "lib" / "fonts").mkdir(parents=True, exist_ok=True)
    modules = Path(install["home"]) / "node_modules"
    if args.force or not (folder / "lib" / "kit.js").is_file():
        # A film may have changed its own copy of the kit; only --force replaces it.
        shutil.copyfile(KIT / "lib" / "kit.js", folder / "lib" / "kit.js")
    shutil.copyfile(modules / "gsap" / "dist" / "gsap.min.js", folder / "lib" / "gsap.min.js")
    for package, name in FONTS:
        shutil.copyfile(modules / package / name, folder / "lib" / "fonts" / name)
    page = folder / "index.html"
    if args.force or not page.is_file():
        page.write_text(page_for(args.portrait), encoding="utf-8")
    upright = page_is_upright(page.read_text(encoding="utf-8"))
    if args.portrait and not upright:
        raise Problem("这个动画目录里已经放了横屏的页面，--portrait 没有生效。要改成竖屏，加 --force 重新放模板"
                      "（index.html 和 lib/kit.js 会换回模板的，film.json 不动）。")
    sample = folder / "film.json"
    if not sample.is_file():
        shutil.copyfile(KIT / "film.example.json", sample)
    chosen = Path(install["home"]) / "browser-path.txt"
    ready = run_tool(folder, ["browser", "ensure"], timeout=1800).returncode == 0
    browser_note = ""
    if not ready:
        # The renderer downloads its own Chrome; where that download is blocked, the computer's own browser will do.
        own = system_browser()
        if own:
            chosen.write_text(own, encoding="utf-8")
            ready = run_tool(folder, ["browser", "path"], timeout=120).returncode == 0
            browser_note = f"渲染用的浏览器没下载下来，改用电脑自带的 {Path(own).name}。"
        else:
            browser_note = "渲染用的浏览器没下载下来，电脑上也没找到 Edge 或 Chrome：先装一个再运行 setup。"
    print(f"动画工具{'就绪' if ready else '还差一步'}：HyperFrames {install['version']}（{'刚装好' if install['installed_now'] else '已装过'}，全电脑共用）。"
          f"{browser_note}动画目录 {folder} 里放好了 index.html、lib/ 和示例 film.json。")
    emit({"ok": ready, **install, "folder": str(folder), "portrait": upright,
          "render_browser_ready": ready,
          "sfx_root": str(modules / "hyperframes" / "dist" / "skills" / "media-use" / "audio" / "assets" / "sfx")})


# ------------------------------------------------------------------- build
def spoken_marks(captions: Sequence[Dict[str, Any]], begin: float, end: float) -> List[Mark]:
    """Every character the voice says between two moments, each with the second it is said.

    Caption lines are the finest timing the timetable has (a clause each); inside one line the
    characters are taken to be evenly spread, which is within a few tenths of a second."""
    marks: List[Mark] = []
    for line in captions:
        start, stop = float(line["start"]), float(line["end"])
        if stop <= begin or start >= end:
            continue
        letters = zh_text.spoken(str(line["text"]))
        marks += [(letter, start + (stop - start) * position / len(letters)) for position, letter in enumerate(letters)]
    # A scene may begin or end in the middle of a line: it hears exactly the characters said while it is on.
    return [(letter, moment) for letter, moment in marks if begin - 0.01 <= moment < end]


def said_at(words: str, marks: Sequence[Mark]) -> Optional[float]:
    """The second at which these words start being said, or None when they are not said here."""
    wanted = zh_text.spoken(words)
    position = "".join(letter for letter, _ in marks).find(wanted) if wanted else -1
    return None if position < 0 else marks[position][1]


def heard(marks: Sequence[Mark], limit: int = 60) -> str:
    text = "".join(letter for letter, _ in marks)
    return text[:limit] + ("……" if len(text) > limit else "")


def scene_span(scene: Dict[str, Any], number: int, segments: List[Dict[str, Any]], cursor: float,
               captions: Sequence[Dict[str, Any]] = ()) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Where a scene starts and ends, and the sentences spoken during it."""
    if "segment" in scene:
        if not segments:
            raise Problem(f"第 {number} 个画面写了 segment，但没有给时间表（--timetable）。")
        wanted = scene["segment"]
        first, last = (wanted, wanted) if isinstance(wanted, int) else (wanted[0], wanted[-1])
        if not 1 <= first <= last <= len(segments):
            raise Problem(f"第 {number} 个画面的 segment 是 {wanted}，时间表里只有第 1 到第 {len(segments)} 段。")
        covered = segments[first - 1:last]
        sentences = [sentence for segment in covered for sentence in segment["sentences"]]
        begin = float(covered[0]["start"])
        if "from" in scene:
            mark = scene["from"]
            if isinstance(mark, str) and re.fullmatch(r"s\d+", mark):
                if not 1 <= int(mark[1:]) <= len(sentences):
                    raise Problem(f"第 {number} 个画面的 from 是 {mark!r}，可这一段只有 {len(sentences)} 句。")
                sentences = sentences[int(mark[1:]) - 1:]
                begin = float(sentences[0]["start"])
            elif isinstance(mark, str) and zh_text.spoken(mark):
                # The scene starts when the voice gets to these words, wherever in a sentence that is.
                marks = spoken_marks(captions, begin, float(covered[-1]["end"]))
                moment = said_at(mark, marks)
                if moment is None:
                    raise Problem(f"第 {number} 个画面的 from 是 {mark!r}，可这一段旁白里没有这几个字。这一段说的是：{heard(marks, 120)}")
                begin = moment
                sentences = [sentence for sentence in sentences if float(sentence["start"]) >= begin - 0.01]
            else:
                raise Problem(f"第 {number} 个画面的 from 是 {mark!r}：写 \"s3\"（从这一段的第 3 句开始），或者直接写旁白里的几个字（说到这几个字时换画面）。")
        return begin, float(covered[-1]["end"]), sentences
    if "start" in scene and "end" in scene:
        return float(scene["start"]), float(scene["end"]), []
    if "seconds" in scene:
        return cursor, cursor + float(scene["seconds"]), []
    raise Problem(f"第 {number} 个画面没说它在哪一段：写 segment（时间表里的第几段），或者 seconds（演示用）。")


def cue_time(value: Any, start: float, end: float, sentences: List[Dict[str, Any]], number: int,
             marks: Sequence[Mark] = ()) -> float:
    if isinstance(value, bool):
        raise Problem(f"第 {number} 个画面的 at 写法不对：{value!r}")
    if isinstance(value, (int, float)):
        moment = start + float(value)
    elif isinstance(value, str) and re.fullmatch(r"s\d+", value):
        index = int(value[1:])
        if not 1 <= index <= len(sentences):
            raise Problem(f"第 {number} 个画面写了 at: {value}，可这个画面里只有 {len(sentences)} 句话开口。")
        moment = float(sentences[index - 1]["start"])
    elif isinstance(value, str) and re.fullmatch(r"\d+(\.\d+)?%", value):
        moment = start + (end - start) * float(value[:-1]) / 100.0
    elif isinstance(value, str) and zh_text.spoken(value):
        # Words from the narration: the cue lands when the voice gets to them.
        found = said_at(value, marks)
        if found is None:
            raise Problem(f"第 {number} 个画面写了 at: {value!r}，可这个画面在场时旁白没有说到这几个字。"
                          + (f"这个画面里说的是：{heard(marks, 120)}" if marks
                             else "这个画面在场时旁白一个字都没说；没有给时间表（--timetable）时，也不能按旁白的字定时间。"))
        moment = found
    else:
        raise Problem(f"第 {number} 个画面的 at 写法不对：{value!r}。可以写旁白里的几个字（说到时出现）、\"s2\"（第 2 句开口时）、"
                      "秒数（从这个画面开头算）或 \"40%\"。")
    return round(min(max(moment, start + 0.05), max(start + 0.05, end - 0.3)), 3)


def spread(count: int, start: float, end: float, sentences: List[Dict[str, Any]]) -> List[float]:
    """Times for cues the film did not time itself: on sentence starts when there are enough."""
    if count <= 0:
        return []
    if sentences and count <= len(sentences):
        return [round(max(float(sentences[int(k * len(sentences) / count)]["start"]), start + 0.05), 3) for k in range(count)]
    usable = max(0.5, end - start - 1.2)
    return [round(start + 0.4 + usable * k / count, 3) for k in range(count)]


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_bar(value: Any) -> bool:
    """A candle named by its number from 0, or relative to one that has a name: "key", "key+3", "last-2"."""
    if isinstance(value, bool):
        return False
    return (isinstance(value, int) and value >= 0) or (isinstance(value, str) and bool(BAR_NAME.fullmatch(value.strip())))


def check_chart(chart: Dict[str, Any], where: str) -> None:
    """Refuse a chart the kit would draw wrongly without saying so: a mistyped shape, a candle whose
    high is below its close, a focus on a bar named in a way the kit does not read."""
    if "shape" in chart and chart["shape"] not in SHAPES:
        raise Problem(f"{where}的 shape 是 {chart['shape']!r}。能用的有：{'、'.join(SHAPES)}；别的走势自己给 data。")
    rows = chart.get("data")
    if rows is not None:
        if not isinstance(rows, list) or len(rows) < 5:
            raise Problem(f"{where}的 data 至少要 5 根 K 线，每根写成 [开, 高, 低, 收, 量]。")
        for index, row in enumerate(rows):
            if not (isinstance(row, list) and len(row) == 5 and all(is_number(value) for value in row)):
                raise Problem(f"{where}的 data 第 {index} 根不对：每根写成 [开, 高, 低, 收, 量] 五个数。")
            opened, high, low, closed, volume = row
            if high < max(opened, closed) or low > min(opened, closed) or volume < 0:
                raise Problem(f"{where}的 data 第 {index} 根不对：最高价不能低于开盘和收盘，最低价不能高于它们，量不能是负数。")
    if "key" in chart and not (isinstance(chart["key"], int) and not isinstance(chart["key"], bool) and chart["key"] >= 0):
        raise Problem(f"{where}的 key 写法不对：{chart['key']!r}。写最要紧的那一根是第几根（从 0 数的数字）。")
    if "show" in chart and not is_bar(chart["show"]):
        raise Problem(f"{where}的 show 写法不对：{chart['show']!r}。写第几根（从 0 数），或者 \"key\"、\"key+2\"、\"last\"。")
    for field in ("line",):
        if field in chart and not (is_number(chart[field]) or chart[field] in PRICE_NAMES):
            raise Problem(f"{where}的 {field} 写法不对：{chart[field]!r}。写价格，或者 \"resistance\"（前面的高点）、\"support\"（前面的低点）。")
    for line in chart.get("lines", []) if isinstance(chart.get("lines"), list) else []:
        if not isinstance(line, dict):
            raise Problem(f"{where}的每条横线要写成一个对象：price、at、label。")
        for field in ("price", "to"):
            if field in line and not (is_number(line[field]) or line[field] in PRICE_NAMES):
                raise Problem(f"{where}的横线 {field} 写法不对：{line[field]!r}。写价格，或者 \"resistance\"、\"support\"。")
        if "price" not in line:
            raise Problem(f"{where}的每条横线都要写 price。")
    for focus in chart.get("focus", []) if isinstance(chart.get("focus"), list) else []:
        if not isinstance(focus, dict):
            raise Problem(f"{where}的每个聚焦要写成一个对象：bar（或 bars）、at、note。")
        if "bar" in focus and not is_bar(focus["bar"]):
            raise Problem(f"{where}的聚焦 bar 写法不对：{focus['bar']!r}。写第几根（从 0 数），或者 \"key\"、\"key+3\"、\"last\"。")
        if "bars" in focus and not (isinstance(focus["bars"], list) and len(focus["bars"]) == 2 and all(is_bar(value) for value in focus["bars"])):
            raise Problem(f"{where}的聚焦 bars 要写成两头，例如 [\"key+1\", \"key+8\"]。")
        if focus.get("point", "high") not in FOCUS_POINTS:
            raise Problem(f"{where}的聚焦 point 是 {focus['point']!r}。能用的有：{'、'.join(FOCUS_POINTS)}。")


def resolve_scene(raw: Dict[str, Any], number: int, start: float, end: float, sentences: List[Dict[str, Any]],
                  captions: Sequence[Dict[str, Any]] = ()) -> Dict[str, Any]:
    kind = raw.get("type")
    if kind not in SCENE_NEEDS:
        raise Problem(f"第 {number} 个画面的 type 是 {kind!r}。能用的有：{'、'.join(sorted(SCENE_NEEDS))}。")
    for field in SCENE_NEEDS[kind]:
        if field not in raw:
            raise Problem(f"第 {number} 个画面（{kind}）少了 {field}。")
    scene = {k: v for k, v in raw.items() if k not in ("segment", "seconds", "from")}
    if "transition" in raw and raw["transition"] not in TRANSITIONS:
        raise Problem(f"第 {number} 个画面的 transition 是 {raw['transition']!r}：能用的有 {'、'.join(TRANSITIONS)}。")
    if kind == "kline":
        check_chart(raw, f"第 {number} 个画面（kline）")
    if kind == "compare":
        for side in ("left", "right"):
            if not isinstance(raw[side], dict):
                raise Problem(f"第 {number} 个画面（compare）的 {side} 要写成一个对象：title、shape、verdict 这些。")
            check_chart(raw[side], f"第 {number} 个画面（compare）{side} 这一边")
    # Only what is spoken while this scene is on screen can be cued in it.
    sentences = [sentence for sentence in sentences if start - 0.01 <= float(sentence["start"]) < end]
    marks = spoken_marks(captions, start, end)
    scene.update({"start": round(start, 3), "end": round(end, 3)})
    # A custom scene may keep cues in lists of its own naming: any list of objects in which at
    # least one carries a time. The built-in scenes use these four.
    cue_lists = [name for name in scene if isinstance(scene[name], list) and scene[name]
                 and all(isinstance(entry, dict) for entry in scene[name])
                 and any("at" in entry or "until" in entry for entry in scene[name])] if kind == "custom" else list(CUE_LISTS)
    for name in CUE_LISTS:
        if name in raw and not isinstance(raw[name], list):
            raise Problem(f"第 {number} 个画面的 {name} 要写成列表（方括号），哪怕只有一项。")
    if kind == "points":
        scene["items"] = [{"title": item} if isinstance(item, str) else dict(item) for item in raw["items"]]
    if kind == "bars":
        for item in raw["items"]:
            if not isinstance(item, dict) or "label" not in item or not is_number(item.get("value")):
                raise Problem(f"第 {number} 个画面（bars）的每一条要写 label 和 value（数字）。")
            if item["value"] < 0:
                raise Problem(f"第 {number} 个画面（bars）只画零以上的数；「{item['label']}」是 {item['value']}。有正有负的对比换一种说法，或者写自定义画面。")
        chosen = raw.get("emphasize")
        if chosen is not None and chosen != "max" and not (isinstance(chosen, int) and not isinstance(chosen, bool)
                                                         and 1 <= chosen <= len(raw["items"])):
            raise Problem(f"第 {number} 个画面（bars）的 emphasize 是 {chosen!r}：写第几根（从 1 数，这里有 {len(raw['items'])} 根），或者 \"max\"。")
    if kind == "statement":
        scene["lines"] = [{"text": line} if isinstance(line, str) else dict(line) for line in raw["lines"]]
    if kind == "number" and "notes" in raw:
        scene["notes"] = [{"text": note} if isinstance(note, str) else dict(note) for note in raw["notes"]]
    for name in cue_lists:
        entries = scene.get(name)
        if not isinstance(entries, list):
            continue
        if not all(isinstance(entry, dict) for entry in entries):
            raise Problem(f"第 {number} 个画面的 {name} 里，每一项要写成一个对象（花括号）。")
        entries = [dict(entry) for entry in entries]
        untimed = [entry for entry in entries if "at" not in entry]
        for entry, moment in zip(untimed, spread(len(untimed), start, end, sentences)):
            entry["at"] = moment
        for entry in entries:
            if not any(entry is other for other in untimed):
                entry["at"] = cue_time(entry["at"], start, end, sentences, number, marks)
            if "until" in entry:
                entry["until"] = cue_time(entry["until"], start, end, sentences, number, marks)
                if entry["until"] <= entry["at"]:
                    raise Problem(f"第 {number} 个画面的 {name} 里有一项的 until 不比 at 晚：until 是它留到什么时候。")
        if kind == "custom":
            for entry in entries:
                if "sound" in entry:
                    check_sound(entry, number, name)
        # Only the chart's focuses are taken in the order they happen: the camera goes from one to
        # the next. Cards, bars and lines stay in the order they were written; that is their place on screen.
        scene[name] = sorted(entries, key=lambda entry: entry["at"]) if name == "focus" else entries
    if "at" in scene:
        scene["at"] = cue_time(scene["at"], start, end, sentences, number, marks)
    for side in ("left", "right"):
        if isinstance(scene.get(side), dict) and "at" in scene[side]:
            scene[side] = dict(scene[side], at=cue_time(scene[side]["at"], start, end, sentences, number, marks))
    return scene


def check_sound(entry: Dict[str, Any], number: int, where: str) -> None:
    """A custom scene's cue may carry one of the library's effects, at a volume under the voice."""
    if entry["sound"] not in CUSTOM_SOUNDS:
        raise Problem(f"第 {number} 个画面 {where} 里的 sound 是 {entry['sound']!r}。能用的音效：{'、'.join(CUSTOM_SOUNDS)}。")
    volume = entry.get("volume", CUSTOM_VOLUME)
    if not is_number(volume) or not SOUND_RANGE[0] <= float(volume) <= SOUND_RANGE[1]:
        raise Problem(f"第 {number} 个画面 {where} 里的 volume 是 {volume!r}：写 {SOUND_RANGE[0]} 到 {SOUND_RANGE[1]} 之间的数"
                      "（1 是和人声一样响，模板自己的音效都在 0.45 到 0.6）。")


def effects_for(scenes: List[Dict[str, Any]], stretches: Sequence[Tuple[float, float]] = ()) -> List[Dict[str, Any]]:
    """Which effect at which second. Every cue could have a sound; a film where they all do is noise.
    Each stretch of narration keeps about one every EFFECT_EVERY seconds: first the opening slam,
    then what carries meaning (a note landing, a verdict, a number), then scene changes, then the rest.

    A volume is the effect's loudness beside the narration (1 is as loud as the voice): mix_audio.py
    measures both. Everything here sits under the voice. The two bass impacts are the lowest by the
    meter's reckoning of what they carry: a loudness meter discounts bass, so at equal loudness an
    impact has several dB more level than speech, and it lands on the words it marks."""
    cues: List[Tuple[int, float, str, float]] = []
    for index, scene in enumerate(scenes):
        start = scene["start"]
        if scene["type"] == "title":
            cues.append((0, start + 0.44, "impact-bass-1.mp3", 0.6))
            # The rise is timed to crest as the title gives way to the next scene. It does not use up a
            # place, and a title too short to hold it goes without.
            after = scenes[index + 1]["start"] if index + 1 < len(scenes) else None
            if after is not None and after - RISER_CREST >= start:
                cues.append((-1, after - RISER_CREST, "riser.mp3", 0.45))
        elif index:
            cues.append((2, max(0.0, start - 0.15), "whoosh.mp3", 0.55))
        for focus in scene.get("focus", []) if scene["type"] == "kline" else []:
            cues.append((4, max(start, focus["at"] - FOCUS_LEAD), "whoosh-short.mp3", 0.45))
            if focus.get("note"):
                cues.append((1, max(start, focus["at"] - 0.1), "pop.mp3", 0.5))
        if scene["type"] in ("points", "bars"):
            cues += [(3, item["at"], "pop.mp3", 0.5) for item in scene["items"]]
        if scene["type"] == "statement":
            cues += [(3, line["at"], "click.mp3", 0.5) for line in scene["lines"]]
        if scene["type"] == "number":
            cues.append((1, scene.get("at", start + 0.3) + 1.45, "chime.mp3", 0.45))
        if scene["type"] == "compare":
            cues += [(1, scene[side]["at"], "impact-bass-2.mp3", 0.5) for side in ("left", "right")
                     if isinstance(scene.get(side), dict) and scene[side].get("verdict") and "at" in scene[side]]
        if scene["type"] == "custom":
            # A custom scene names the sounds its own cues make; they count like the kit's pops.
            for value in scene.values():
                if isinstance(value, list):
                    cues += [(3, float(entry["at"]), entry["sound"], float(entry.get("volume", CUSTOM_VOLUME)))
                             for entry in value if isinstance(entry, dict) and entry.get("sound") and "at" in entry]
    if not cues:
        return []
    spans = sorted(stretches)
    if not spans:
        last = max(at for _, at, _, _ in cues)
        spans = [(k * EFFECT_WINDOW, (k + 1) * EFFECT_WINDOW) for k in range(int(last // EFFECT_WINDOW) + 1)]

    def stretch_of(at: float) -> int:
        return max([k for k, (begin, _) in enumerate(spans) if begin <= at + 0.2], default=0)

    kept: List[Tuple[float, str, float]] = []
    heard_at: List[float] = []
    used = [0] * len(spans)
    for rank, at, name, volume in sorted(cues):
        if rank < 0:
            # The rise makes no sound where it starts, so it neither crowds a neighbour nor is crowded.
            kept.append((at, name, volume))
            continue
        home = stretch_of(at)
        room = max(2, round((spans[home][1] - spans[home][0]) / EFFECT_EVERY))
        if used[home] >= room or any(abs(at - other) < EFFECT_GAP for other in heard_at):
            continue
        used[home] += 1
        heard_at.append(at)
        kept.append((at, name, volume))
    return [{"file": name, "at": round(at, 2), "volume": volume} for at, name, volume in sorted(kept)]


def brightness(colour: Any) -> Optional[float]:
    """How bright a #rrggbb colour looks, 0 to 1; None for anything written another way."""
    found = re.fullmatch(r"#([0-9a-fA-F]{2})([0-9a-fA-F]{2})([0-9a-fA-F]{2})", str(colour).strip())
    if not found:
        return None
    red, green, blue = (int(part, 16) / 255.0 for part in found.groups())
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def whole_frames(seconds: float) -> float:
    """The shortest run of whole frames that holds `seconds`, written so that nothing reading it back
    counts one frame more."""
    frames = math.ceil(seconds * FPS - 1e-6)
    return (frames * 1_000_000 // FPS) / 1_000_000


def known_styles() -> List[str]:
    return sorted(path.parent.name for path in STYLES.glob("*/prompt.md"))


def resolve(film: Dict[str, Any], table: Optional[Dict[str, Any]], tail: float) -> Dict[str, Any]:
    style = film.get("style")
    raw_scenes = film.get("scenes")
    if style is not None:
        styles = known_styles()
        if not isinstance(style, str) or style not in styles:
            raise Problem(f"style 写的是 {style!r}。风格库里有：{'、'.join(styles)}。怎么选、怎么做见 references/styles.md。")
        for number, raw in enumerate(raw_scenes if isinstance(raw_scenes, list) else [], 1):
            if isinstance(raw, dict) and raw.get("type") != "custom":
                raise Problem(f"选了风格 {style} 的片子，画面要全部写成 custom：第 {number} 个画面是 {raw.get('type')!r}。"
                              "模板自带的几种画面是黑底金字那一套，混进来就不是这个风格了。")
    else:
        backdrop = brightness((film.get("palette") or {}).get("bg", "#050607"))
        if backdrop is not None and backdrop > 0.35:
            raise Problem("这套画面模板只做深色底：标注的底、字幕的描边都是按深色底配的，换成浅色底字会看不清。"
                          "把 palette 的 bg 换成深色；原片是浅色底时，学它的重点色和涨跌色，底色仍用深色。"
                          "原片是扁平插画、线条、纸面这类浅色画风，走风格库（references/styles.md）：写了 style、画面自己写，才能用浅色底。")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise Problem("film.json 里没有 scenes。")
    segments = (table or {}).get("segments", [])
    lines = (table or {}).get("captions", [])
    starts: List[float] = []
    ends: List[float] = []
    spoken: List[List[Dict[str, Any]]] = []
    cursor = 0.0
    for number, raw in enumerate(raw_scenes, 1):
        if not isinstance(raw, dict):
            raise Problem(f"第 {number} 个画面不是一个对象。")
        start, end, sentences = scene_span(raw, number, segments, cursor, lines)
        if end - start < 0.8:
            raise Problem(f"第 {number} 个画面只有 {end - start:.2f} 秒，太短。")
        starts.append(start)
        ends.append(end)
        spoken.append(sentences)
        cursor = end
    for number in range(1, len(starts)):
        if starts[number] < starts[number - 1]:
            raise Problem(f"第 {number + 1} 个画面排在了第 {number} 个画面前面：画面要按时间顺序写。")
        ends[number - 1] = starts[number]  # one scene hands over exactly where the next begins
    starts[0] = 0.0
    for number in range(len(starts) - 1):
        if ends[number] - starts[number] < 0.8:
            raise Problem(f"第 {number + 1} 个画面只剩 {max(0.0, ends[number] - starts[number]):.2f} 秒就被第 {number + 2} 个画面换掉了："
                          f"把第 {number + 2} 个画面的 from 往后挪，或者去掉第 {number + 1} 个画面。")
    total = whole_frames((float(table["total_s"]) if table else ends[-1]) + tail)
    ends[-1] = total
    scenes = [resolve_scene(raw, number + 1, starts[number], ends[number], spoken[number], lines) for number, raw in enumerate(raw_scenes)]
    charted = False
    for number, scene in enumerate(scenes, 1):
        if scene["type"] == "kline" and scene.get("continue") and not charted:
            raise Problem(f"第 {number} 个画面写了 continue，可它前面没有 K 线画面可以接着讲。第一张图不要写 continue。")
        charted = charted or scene["type"] == "kline"
    captions = []
    for index, line in enumerate(lines):
        nxt = lines[index + 1]["start"] if index + 1 < len(lines) else total
        captions.append({"start": line["start"], "end": round(min(float(nxt) - 0.02, float(line["end"]) + 0.35), 3), "text": line["text"]})
    covered = {n for raw in raw_scenes if "segment" in raw
               for n in ([raw["segment"]] if isinstance(raw["segment"], int) else range(raw["segment"][0], raw["segment"][-1] + 1))}
    resolved = {"total": total, "palette": film.get("palette") or {}, "fonts": film.get("fonts") or {},
                "keywords": film.get("keywords") or [], "captions": captions, "scenes": scenes,
                "_uncovered": [n for n in range(1, len(segments) + 1) if n not in covered]}
    if style is not None:
        resolved["style"] = style
    return resolved


def bare_openings(scenes: Sequence[Dict[str, Any]], allowed: float = 1.2) -> List[Tuple[int, float]]:
    """Scenes that open on an empty frame: nothing of theirs is on screen for more than `allowed` seconds.
    Charts, lists, bars, comparisons and titles draw something at once; big words and a bare number wait
    for their cue, so a late cue leaves the viewer looking at the backdrop."""
    found: List[Tuple[int, float]] = []
    for number, scene in enumerate(scenes, 1):
        if scene.get("header"):
            continue
        first: Optional[float] = None
        if scene["type"] == "statement" and scene.get("lines"):
            first = min(float(line["at"]) for line in scene["lines"])
        elif scene["type"] == "number":
            first = float(scene.get("at", scene["start"] + 0.3))
        if first is not None and first - float(scene["start"]) > allowed:
            found.append((number, round(first - float(scene["start"]), 1)))
    return found


def cmd_build(args: argparse.Namespace) -> None:
    folder: Path = args.folder
    source = folder / "film.json"
    if not source.is_file():
        raise Problem(f"找不到 {source}。先运行 setup，再照示例改 film.json。")
    try:
        film = json.loads(source.read_text(encoding="utf-8"))
    except ValueError as error:
        raise Problem(f"film.json 不是合法的 JSON：{error}") from error
    table = json.loads(args.timetable.read_text(encoding="utf-8")) if args.timetable else None
    resolved = resolve(film, table, args.tail)
    custom = [scene for scene in resolved["scenes"] if scene["type"] == "custom"]
    script = folder / "custom.js"
    if custom:
        if not script.is_file():
            raise Problem("film.json 里有 custom 画面，动画目录里却没有 custom.js：每个 custom 画面的 fn 都要在 custom.js 里写好。")
        written = script.read_text(encoding="utf-8")
        missing = sorted({str(scene["fn"]) for scene in custom if not re.search(r"\b" + re.escape(str(scene["fn"])) + r"\b", written)})
        if missing:
            raise Problem("custom.js 里找不到这些画面函数：" + "、".join(missing) + "。名字要和 film.json 里的 fn 一字不差。")
    uncovered = resolved.pop("_uncovered")
    (folder / "film.js").write_text("window.FILM = " + json.dumps(resolved, ensure_ascii=False) + ";\n", encoding="utf-8")
    stretches = [(float(segment["start"]), float(segment["end"])) for segment in (table or {}).get("segments", [])]
    effects = effects_for(resolved["scenes"], stretches)
    (folder / "音效.json").write_text(json.dumps(effects, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    page = folder / "index.html"
    html = page.read_text(encoding="utf-8")
    html, changed = re.subn(r'(<div id="root"[^>]*data-duration=")[^"]*(")', lambda m: m.group(1) + str(resolved["total"]) + m.group(2), html)
    if not changed:
        raise Problem("index.html 里找不到 #root 的 data-duration：这个文件不是 setup 放的那一份。")
    html = re.sub(r"\s*<audio id=\"mix\"[^>]*></audio>", "", html)
    kit_tag = '<script src="lib/kit.js"></script>'
    if custom and 'src="custom.js"' not in html:
        # The scenes have to be defined before the kit builds the film, so custom.js goes in just ahead of it.
        if kit_tag not in html:
            raise Problem("index.html 里找不到 lib/kit.js 那一行：这个文件不是 setup 放的那一份。")
        html = html.replace(kit_tag, '<script src="custom.js"></script>\n    ' + kit_tag, 1)
    # The mix belongs to one timing: the film's length and where its effects fall. A later build
    # that leaves both alone (a word or a colour changed) keeps the sound; one that moves them
    # takes the sound off, because a mix with effects at the old moments is worse than none.
    timing = hashlib.sha256(json.dumps([resolved["total"], effects], sort_keys=True).encode("utf-8")).hexdigest()[:16]
    sound, stamp = folder / "mix.wav", folder / "mix.json"
    earlier = json.loads(stamp.read_text(encoding="utf-8")).get("timing") if stamp.is_file() and sound.is_file() else None
    taken_off = False
    if args.mix:
        if not args.mix.is_file():
            raise Problem(f"找不到混音文件：{args.mix}")
        shutil.copyfile(args.mix, sound)
        stamp.write_text(json.dumps({"timing": timing}) + "\n", encoding="utf-8")
        state = "带混音"
    elif earlier == timing:
        state = "带混音（沿用上次混好的声音）"
    else:
        taken_off = sound.is_file()
        for stale in (sound, stamp):
            if stale.is_file():
                stale.unlink()
        state = "还没放声音"
    has_sound = sound.is_file()
    if has_sound:
        clip = (f'\n      <audio id="mix" class="clip" data-start="0" data-duration="{resolved["total"]}" '
                f'data-track-index="9" src="mix.wav"></audio>')
        html = re.sub(r'(<div id="root"[^>]*>)', lambda m: m.group(1) + clip + "\n    ", html, count=1)
    page.write_text(html, encoding="utf-8")
    print(f"画面排好了：{len(resolved['scenes'])} 个画面，全长 {resolved['total']} 秒，{len(resolved['captions'])} 条字幕，"
          f"{len(effects)} 个音效点，{state}。")
    if taken_off:
        print("注意：上次放进来的声音和现在的画面对不上（时间或音效点变了，或者认不出它是按哪一版混的），已经取下。"
              "重新运行 mix_audio.py，再 build --mix。")
    if uncovered:
        print("注意：时间表里的第 " + "、".join(str(n) for n in uncovered) + " 段没有画面管，会一直停在前一个画面上。")
    bare = bare_openings(resolved["scenes"])
    for number, seconds in bare:
        print(f"注意：第 {number} 个画面开头有 {seconds:.1f} 秒是空的（第一样东西出现得晚）。把这个画面的 from 往后挪到说那句话的地方，"
              "或者把第一样东西的 at 提前。")
    emit({"ok": True, "film": str(folder / "film.js"), "effects": str(folder / "音效.json"), "total_s": resolved["total"],
          "scenes": [{"type": s["type"], "start": s["start"], "end": s["end"]} for s in resolved["scenes"]],
          "uncovered_segments": uncovered, "bare_openings": [{"scene": number, "seconds": seconds} for number, seconds in bare],
          "has_sound": has_sound, "sound_taken_off": taken_off})


# ------------------------------------------------- proofread, check and render
def read_film(folder: Path) -> Dict[str, Any]:
    source = folder / "film.js"
    if not source.is_file():
        raise Problem("还没有 film.js：先运行 build。")
    text = source.read_text(encoding="utf-8")
    return json.loads(text[text.index("{"):text.rindex("}") + 1])


def scene_cues(scene: Dict[str, Any]) -> List[Tuple[float, str, str]]:
    """What turns up in a scene and when: (second, what kind of thing, its words)."""
    kind, start = scene["type"], float(scene["start"])
    found: List[Tuple[float, str, str]] = []
    if kind == "title":
        found.append((start, "标题", str(scene.get("text", ""))))
    if scene.get("header"):
        found.append((start, "小标题", str(scene["header"])))
    for line in scene.get("lines", []):
        found.append((float(line["at"]), "横线", str(line.get("label") or "（没写字）")) if kind == "kline"
                     else (float(line["at"]), "大字", str(line.get("text", ""))))
    for focus in scene.get("focus", []):
        found.append((float(focus["at"]), "标注", str(focus.get("note") or "（只圈，没写字）")))
    for item in scene.get("items", []):
        found.append((float(item["at"]), "要点" if kind == "points" else "柱子", str(item.get("title") or item.get("label") or "")))
    for note in scene.get("notes", []):
        found.append((float(note["at"]), "补充", str(note.get("text", ""))))
    if kind == "number":
        found.append((float(scene.get("at", start + 0.3)), "数字", f"{scene.get('prefix', '')}{scene.get('value')}{scene.get('suffix', '')}"))
    for side in ("left", "right"):
        part = scene.get(side)
        if isinstance(part, dict) and part.get("verdict") and "at" in part:
            found.append((float(part["at"]), "结论章", str(part["verdict"])))
    if kind == "end":
        found.append((start, "结尾", str(scene.get("text", ""))))
    return sorted(found)


def cmd_cues(args: argparse.Namespace) -> None:
    """The film as a list: what appears when, next to what the voice is saying at that moment.
    Reading it catches a note that contradicts the narration before any picture is rendered."""
    film = read_film(args.folder)
    captions = film.get("captions", [])

    def voice(moment: float) -> str:
        said = [line for line in captions if float(line["start"]) - 0.25 <= moment < float(line["end"]) + 0.6]
        return "「" + said[-1]["text"] + "」" if said else "（这时没人说话）"

    rows = []
    for number, scene in enumerate(film["scenes"], 1):
        print(f"画面 {number}（{scene['type']}）{scene['start']:.1f}–{scene['end']:.1f} 秒")
        for moment, kind, words in scene_cues(scene):
            words = words.replace("**", "").replace("\n", " / ")
            print(f"  {moment:6.1f} 秒 {kind}「{words}」｜旁白：{voice(moment)}")
            rows.append({"scene": number, "at": moment, "kind": kind, "words": words, "voice": voice(moment)})
    print("逐行看：画面上出现的字，和这一刻旁白说的是不是同一件事。对不上就改 film.json 里那一项的 at。")
    emit({"ok": True, "cues": rows})


def within(moment: float, start: float, end: float) -> float:
    return round(min(max(moment, start + 0.2), max(start + 0.2, end - 0.15)), 2)


def snapshot_times(scenes: Sequence[Dict[str, Any]], total: float) -> List[float]:
    """When to look: early in every scene, and just after each thing in it has appeared. A film
    too long for that keeps fewer looks per scene, never fewer scenes: its last third is checked
    as closely as its first."""
    full, pair, single = [0.0], [0.0], [0.0]
    for scene in scenes:
        start, end = float(scene["start"]), float(scene["end"])
        early = within(start + 0.9, start, end)
        appeared = [within(at + 0.6, start, end) for at, _, _ in scene_cues(scene) if at > start + 0.3]
        settled = appeared[-1] if appeared else within(start + (end - start) * 0.6, start, end)
        looks: List[float] = []
        for moment in sorted({early, settled, *appeared}):
            if not looks or moment - looks[-1] >= 0.5:
                looks.append(moment)
        full += looks
        pair += [early, settled]
        single.append(settled)
    times: List[float] = []
    for candidate in (full, pair, single):
        times = sorted({moment for moment in candidate if 0 <= moment < total})
        if len(times) <= SNAPSHOTS:
            return times
    step = len(times) / SNAPSHOTS
    return [times[int(index * step)] for index in range(SNAPSHOTS)]


def findings(node: Any) -> List[Dict[str, Any]]:
    """Every finding in the checker's report, wherever it sits."""
    if isinstance(node, list):
        return [found for item in node for found in findings(item)]
    if not isinstance(node, dict):
        return []
    own = [node] if "severity" in node and "message" in node else []
    return own + [found for value in node.values() for found in findings(value)]


def cmd_check(args: argparse.Namespace) -> None:
    done = run_tool(args.folder, ["check", "--json"])
    text = plain(done.stdout)
    try:
        report = json.loads(text[text.index("{"):text.rindex("}") + 1])
    except ValueError:
        report = {}
    out = plain(done.stdout + "\n" + done.stderr)
    found = findings(report)
    groups = {level: [f for f in found if f.get("severity") == level] for level in ("error", "warning", "info")}
    errors = len(groups["error"])
    if not report:
        counted = re.search(r"(\d+) errors?", out)
        errors = int(counted.group(1)) if counted else (0 if done.returncode == 0 else 1)
    passed = done.returncode == 0 and errors == 0
    print("检查通过：0 个错误。" if passed else f"检查没过：{errors} 个错误。")
    if not report and not passed:
        print("\n".join(out.strip().splitlines()[-25:]))
    brief: List[Dict[str, Any]] = []
    # The full report runs to tens of thousands of characters; what matters fits in a few lines.
    for level, title, examples in (("error", "错误", 6), ("warning", "警告", 2), ("info", "提示", 1)):
        kinds: Dict[str, List[Dict[str, Any]]] = {}
        for finding in groups[level]:
            kinds.setdefault(str(finding.get("code", "?")), []).append(finding)
        for code, rows in kinds.items():
            print(f"{title}：{code} × {len(rows)}")
            for row in rows[:examples]:
                where = f"{float(row['time']):.1f} 秒" if isinstance(row.get("time"), (int, float)) else ""
                words = f"「{str(row['text'])[:20]}」" if row.get("text") else ""
                hint = str(row.get("fixHint") or "")[:110]
                print(f"  {where} {words} {str(row['message'])[:140]}" + (f"｜{hint}" if hint else ""))
                brief.append({"level": level, "code": code, "time": row.get("time"), "text": str(row.get("text") or "")[:40],
                              "message": str(row["message"])[:200]})
    saved = (report.get("saved") or {}).get("report") if isinstance(report.get("saved"), dict) else None
    emit({"ok": True, "passed": passed, "errors": errors, "warnings": len(groups["warning"]), "infos": len(groups["info"]),
          "findings": brief[:30], "report": saved})


def cmd_snapshot(args: argparse.Namespace) -> None:
    folder: Path = args.folder
    if args.at:
        times = [float(x) for x in args.at.split(",")]
    else:
        film = read_film(folder)
        times = snapshot_times(film["scenes"], float(film["total"]))
    if not times:
        raise Problem("没有可截的时间点：先运行 build，或者用 --at 给秒数。")
    # Two folders, so that looking closer at a few moments does not wipe the pass over the whole film.
    out = folder / ("snapshots-at" if args.at else "snapshots")
    done = run_tool(folder, ["snapshot", "--at", ",".join(f"{t:g}" for t in times), "-o", str(out), "--no-end"])
    if done.returncode != 0:
        raise Problem("截图失败：" + (plain(done.stderr or done.stdout).strip().splitlines() or ["未知原因"])[-1])
    sheets = sorted(str(p) for p in out.glob("contact-sheet*"))
    frames = sorted(str(p) for p in out.glob("*.png"))
    print(f"截了 {len(frames)} 张，汇总图 {len(sheets)} 张，在 {out}。逐张看：字有没有出画、盖住、挤在一起。")
    emit({"ok": True, "folder": str(out), "sheets": sheets, "frames": frames, "times": times})


def sound_level(path: Path) -> Tuple[Optional[float], Optional[float]]:
    """(integrated loudness, true peak) of a file's sound; (None, None) when it has none to measure."""
    done = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-vn",
                           "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True)
    log = done.stderr.decode("utf-8", "replace")
    if done.returncode != 0 or "Summary:" not in log:
        return None, None
    summary = log[log.rfind("Summary:"):]

    def number(label: str) -> Optional[float]:
        found = re.search(label + r"[:]\s*(-?[\d.]+)", summary)
        return float(found.group(1)) if found else None

    return number(r"\bI"), number("Peak")


def keep_the_mix(video: Path, mix: Path) -> Dict[str, Any]:
    """See that a rendered film is as loud as the mix it was given, and put the mix back when it is not.

    The renderer encodes the sound itself and turns the whole track down when it reads the encoded
    peaks as too high. On some material its encoder leaves a stray spike in one channel and the
    check takes off far more than the film can spare: 8 dB on a mix whose own peaks were at -3.
    The mix put back here is encoded once, and is kept only if it passes the same ceiling."""
    if not shutil.which("ffmpeg"):
        return {}
    wanted, _ = sound_level(mix)
    got, _ = sound_level(video)
    report: Dict[str, Any] = {"mix_lufs": wanted, "film_lufs": got}
    if wanted is None or wanted <= -69.0 or (got is not None and wanted - got <= SOUND_SLACK):
        return report
    lost = None if got is None else round(wanted - got, 1)
    fixed = video.with_name(video.stem + ".sound" + video.suffix)
    done = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-i", str(mix),
                           "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                           "-shortest", "-movflags", "+faststart", str(fixed)], capture_output=True)
    level, peak = sound_level(fixed) if done.returncode == 0 and fixed.is_file() else (None, None)
    if level is None or peak is None or peak > DELIVERY_PEAK_DB:
        if fixed.is_file():
            fixed.unlink()
        report["lowered_db"] = lost
        return report
    fixed.replace(video)
    report.update(film_lufs=level, restored_db=lost)
    return report


def cmd_render(args: argparse.Namespace) -> None:
    output: Path = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    quality = "high" if args.final else "draft"
    done = run_tool(args.folder, ["render", "--quality", quality, "--output", str(output)], timeout=6 * 3600)
    if done.returncode != 0 or not output.is_file():
        raise Problem("渲染失败：" + (plain(done.stderr or done.stdout).strip().splitlines() or ["未知原因"])[-1])
    mix = args.folder / "mix.wav"
    sound = keep_the_mix(output, mix) if mix.is_file() else {}
    print(f"渲好了（{'正式版' if args.final else '草稿版'}）：{output}")
    if "restored_db" in sound:
        lost = sound["restored_db"]
        print("注意：渲染器" + (f"把声音整体调小了 {lost} 分贝" if lost is not None else "没有把声音放进成片")
              + f"，已经换回混好的那条（现在 {sound['film_lufs']} LUFS）。")
    elif "lowered_db" in sound:
        print(f"注意：成片的声音比混好的那条轻了 {sound['lowered_db']} 分贝：混音的峰值太高，被渲染器调小了。"
              "用 mix_audio.py 重新混一遍，再 build --mix，再渲染。")
    emit({"ok": True, "video": str(output), "quality": quality, "bytes": output.stat().st_size, "sound": sound})


def cmd_tool(args: argparse.Namespace) -> None:
    rest = [a for a in args.rest if a != "--"]
    if "--creator-asked-for-editor" in rest:
        # Written after the folder, argparse hands the flag over as part of the command.
        rest = [a for a in rest if a != "--creator-asked-for-editor"]
        args.creator_asked_for_editor = True
    if not rest:
        raise Problem("在 -- 后面写 hyperframes 的命令，例如：animation.py tool <动画目录> -- docs gsap")
    name = rest[0]
    if name == "preview" and "--stop" in rest:
        pass  # closing an editor page that was left running opens nothing
    elif name == "preview" and args.creator_asked_for_editor:
        rest = [a for a in rest if a != "--open"] + (["--no-open"] if "--no-open" not in rest else [])
    elif name not in ALLOWED_TOOLS:
        raise Problem(f"不运行 hyperframes {name}：它会弹出窗口、把项目传到网上，或者改动你自己的技能。"
                      "给创作者看画面用 snapshot 和 render。创作者明确要在网页里自己动手改时，"
                      "才用 --creator-asked-for-editor 跑 preview，它不会自动弹浏览器，把打印出来的链接发给他。")
    done = run_tool(args.folder, rest)
    print(plain(done.stdout).rstrip())
    if done.returncode != 0:
        print(plain(done.stderr).rstrip())
    emit({"ok": done.returncode == 0, "command": name, "exit": done.returncode})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="图文动效：装一次、排时间、检查、渲染，全程不弹窗口。")
    sub = parser.add_subparsers(dest="command", required=True)

    def with_folder(name: str, text: str) -> argparse.ArgumentParser:
        command = sub.add_parser(name, help=text)
        command.add_argument("folder", type=Path, help="这条片子的动画目录，一般是 <项目>/制作/动画")
        return command

    setup = with_folder("setup", "装好动画工具（全电脑一份），把模板放进动画目录")
    setup.add_argument("--portrait", action="store_true", help="竖屏 1080×1920；不给就是横屏 1920×1080")
    setup.add_argument("--force", action="store_true", help="index.html 和 lib/kit.js 已经在也换成新的")
    build = with_folder("build", "把 film.json 和 时间表.json 排成 film.js，并写出音效点")
    build.add_argument("--timetable", type=Path, help="voice_track.py 写的 时间表.json")
    build.add_argument("--mix", type=Path, help="mix_audio.py 混好的声音；给了就放进片子")
    build.add_argument("--tail", type=float, default=TAIL, help="旁白说完后画面再留几秒")
    with_folder("cues", "列出每样东西什么时候出现、那一刻旁白在说什么（先读这个，再截图）")
    with_folder("check", "检查画面有没有错")
    snapshot = with_folder("snapshot", "截图自查")
    snapshot.add_argument("--at", help="秒数，逗号隔开，存到 snapshots-at；不给就每个画面开头一张、每样东西出现后各一张，存到 snapshots")
    render = with_folder("render", "渲成 MP4")
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--final", action="store_true", help="正式版；不给就是草稿版")
    tool = with_folder("tool", "运行其他不弹窗口的 hyperframes 命令")
    tool.add_argument("--creator-asked-for-editor", action="store_true", help=argparse.SUPPRESS)
    tool.add_argument("rest", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    if args.command != "setup" and not args.folder.is_dir():
        emit({"ok": False, "error": f"no such folder: {args.folder}"}, 2)
    args.folder = args.folder.resolve()
    try:
        {"setup": cmd_setup, "build": cmd_build, "cues": cmd_cues, "check": cmd_check, "snapshot": cmd_snapshot,
         "render": cmd_render, "tool": cmd_tool}[args.command](args)
    except Problem as error:
        emit({"ok": False, "error": str(error)}, 1)
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        emit({"ok": False, "error": f"{type(error).__name__}: {error}"}, 1)


if __name__ == "__main__":
    main()
