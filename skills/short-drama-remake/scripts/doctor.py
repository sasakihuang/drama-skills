#!/usr/bin/env python3
"""Check, in one go, what this computer has and lacks for remaking a video.

    doctor.py [--route motion|drama]

Looks only at this computer: which programs are installed and how new they are, whether a
browser is there for the Douyin step, whether the creator's key file exists (the keys themselves
are never read out or printed), whether speech recognition and the animation tool are set up, and
how much disk space is left. It installs nothing and changes nothing; for each missing piece it
says what it is for and how to get it, so the creator can be asked once instead of one failure at
a time. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional

MINIMUM_PYTHON = (3, 9)

HOME = Path.home() / ".drama-skills"
ANIMATION_PIN = Path(__file__).resolve().parents[1] / "assets" / "motion-kit" / "package.json"
WINDOWS = platform.system() == "Windows"
MAC = platform.system() == "Darwin"


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def version_of(command: List[str], pattern: str) -> Optional[str]:
    program = shutil.which(command[0])
    if not program:
        return None
    try:
        done = subprocess.run([program, *command[1:]], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    found = re.search(pattern, (done.stdout or "") + (done.stderr or ""))
    return found.group(1) if found else ""


def install_hint(windows: str, mac: str, other: str) -> str:
    return windows if WINDOWS else mac if MAC else other


def browser_path() -> Optional[str]:
    candidates = [os.environ.get("DOUYIN_BROWSER", "")]
    # Windows keeps programs under folders it names in the environment.
    for variable in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA"):
        base = os.environ.get(variable, "")
        if base:
            candidates += [os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"),
                           os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")]
    candidates += [
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/microsoft-edge", "/usr/bin/google-chrome", "/usr/bin/chromium",
    ]
    return next((path for path in candidates if path and os.path.exists(path)), None)


KEY_NAMES = ("MINIMAX_API_KEY", "OPENAI_API_KEY")


def key_file() -> Path:
    """Where the production skill reads keys from: the file DRAMA_SKILLS_ENV names, or the usual one."""
    named = os.environ.get("DRAMA_SKILLS_ENV")
    return Path(named).expanduser() if named else HOME / "providers.env"


def key_names(path: Path) -> List[str]:
    """Names in the key file that have a value, read the way the production skill reads it: a
    byte-order mark at the top and an `export` in front of a name are both fine. Values are not kept."""
    names = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if line and not line.startswith("#") and "=" in line:
            name, value = line.split("=", 1)
            if value.strip().strip("'\""):
                names.append(name.strip())
    return names


def check(route: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    def add(name: str, state: str, detail: str, use: str = "", fix: str = "") -> None:
        items.append({"name": name, "state": state, "detail": detail, "use": use, "fix": fix})

    add("Python", "ok", platform.python_version())

    ffmpeg, ffprobe = version_of(["ffmpeg", "-version"], r"ffmpeg version (\S+)"), shutil.which("ffprobe")
    if ffmpeg is None or not ffprobe:
        add("ffmpeg", "missing", "没装（要 ffmpeg 和 ffprobe 两个程序）", "拉片、量声音画面、拼旁白、混音都要用",
            install_hint("winget install Gyan.FFmpeg，装完重开终端", "brew install ffmpeg", "用系统的软件包管理器装 ffmpeg"))
    else:
        add("ffmpeg", "ok", ffmpeg or "已装")

    node = version_of(["node", "--version"], r"v(\d+\.\d+\.\d+)")
    major = int(node.split(".")[0]) if node else 0
    node_fix = install_hint("winget install OpenJS.NodeJS.LTS，装完重开终端", "brew install node", "到 nodejs.org 下载 LTS 版")
    if node is None or not major:
        add("Node.js", "missing", "没装", "拉片工具、抖音后台下载、图文动效都要用", node_fix)
    elif major < 18:
        add("Node.js", "missing", f"{node}，太旧", "拉片工具要 18，抖音后台下载和图文动效要 22", node_fix)
    elif major < 22:
        add("Node.js", "partial", f"{node}：拉片能用，抖音后台下载和图文动效要 22 或更新", "抖音链接、K 线和图表这类图文动效片", node_fix)
    else:
        add("Node.js", "ok", node)

    browser = browser_path()
    if browser:
        add("浏览器", "ok", Path(browser).name)
    else:
        add("浏览器", "partial", "没找到 Edge 或 Chrome", "抖音链接靠它在后台取作品；快手、小红书不需要",
            "装 Edge 或 Chrome；或者让创作者在抖音 App 里保存视频后交给你")

    keys = key_file()
    # A key may also be set in the environment; the production skill takes it from there first.
    present = set(key_names(keys) if keys.is_file() else []) | {name for name in KEY_NAMES if os.environ.get(name)}
    if "MINIMAX_API_KEY" in present:
        add("密钥文件", "ok", "有 MiniMax 密钥" + ("，有画图密钥" if "OPENAI_API_KEY" in present else ""))
    elif not keys.is_file():
        add("密钥文件", "missing", f"没有 {keys}", "配音、出图、出视频都要从这里读密钥",
            "按生产技能的说明建这个文件，让创作者自己把密钥填进去；不要让他把密钥发到对话里")
    else:
        add("密钥文件", "missing", "文件在，但 MINIMAX_API_KEY 是空的", "配音和出视频",
            "请创作者自己打开这个文件填上；不要让他把密钥发到对话里")

    asr = HOME / "asr-venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    ready = False
    if asr.is_file():
        try:
            ready = subprocess.run([str(asr), "-c", "import faster_whisper"], capture_output=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ready = False
    if ready:
        add("识别台词", "ok", "已装")
    else:
        add("识别台词", "partial", "还没装", "原片有人说话时，新稿的口气、有没有照搬、每段字数都靠它；不装这几项没法和原片比",
            "见 SKILL.md「识别台词」一节；第一次要下载几百 MB，先问创作者")

    if route == "motion":
        wanted = ""
        if ANIMATION_PIN.is_file():
            wanted = json.loads(ANIMATION_PIN.read_text(encoding="utf-8")).get("dependencies", {}).get("hyperframes", "")
        installed = HOME / "hyperframes" / "node_modules" / "hyperframes" / "package.json"
        have = json.loads(installed.read_text(encoding="utf-8")).get("version", "") if installed.is_file() else ""
        if have and (not wanted or have == wanted):
            add("动画工具", "ok", f"HyperFrames {have}")
        else:
            add("动画工具", "partial", "还没装" if not have else f"装的是 {have}，技能要 {wanted}", "图文动效片在本机做动画",
                "运行 animation.py setup，它会装好（约 200 MB，全电脑共用一份）")

    free = shutil.disk_usage(Path.home()).free / 1e9
    if free < 3:
        add("磁盘空间", "partial", f"用户目录所在的盘只剩 {free:.1f} GB", "一条片子连素材要几百 MB", "先腾出几个 GB")
    else:
        add("磁盘空间", "ok", f"剩 {free:.0f} GB")
    return items


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="一次查清这台电脑做复刻还缺什么。")
    parser.add_argument("--route", choices=("motion", "drama"), default="motion",
                        help="motion：图文动效类（还要查动画工具）；drama：剧情和实拍类")
    args = parser.parse_args()
    items = check(args.route)
    marks = {"ok": "好  ", "partial": "注意 ", "missing": "缺  "}
    for item in items:
        line = marks[item["state"]] + item["name"] + "：" + item["detail"]
        if item["state"] != "ok":
            line += f"。用处：{item['use']}。怎么办：{item['fix']}"
        print(line)
    emit({"ok": True, "ready": not any(i["state"] == "missing" for i in items),
          "missing": [i["name"] for i in items if i["state"] == "missing"],
          "attention": [i["name"] for i in items if i["state"] == "partial"], "items": items})


if __name__ == "__main__":
    main()
