#!/usr/bin/env python3
"""Run the bundled shot-breakdown tool and save what it prints as a UTF-8 file.

The tool (vendor/video-shots) prints its results and its guide saves them with the shell's `>`.
Windows PowerShell 5.1 writes such a file as UTF-16, which the tool's next step cannot read, and it
has no `&&` to chain the guide's `recut ... > new && mv new old`. This wrapper does the saving
itself, so one spelling of each command works in every shell:

    shots.py --out shots.json -- seed source.mp4 --track track.json --title "片名"
    shots.py --out shots.json -- recut shots.json --track track.json --split 63.5
    shots.py --out shots.md -- render shots.json --md --track track.json
    shots.py -- validate shots.json --track track.json --frames frames

Everything after `--` goes to the tool unchanged. The file is written only when the tool succeeds,
and only after it has finished, so `recut` can read the file it is about to replace.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence

MINIMUM_PYTHON = (3, 9)
TOOL = Path(__file__).resolve().parents[1] / "vendor" / "video-shots" / "scripts" / "video-shots.mjs"


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(code)


def save_output(command: Sequence[str], out: Path) -> Dict[str, Any]:
    """Run a command and write exactly what it printed to `out`; leave `out` alone when it fails."""
    done = subprocess.run(list(command), stdout=subprocess.PIPE, check=False)
    if done.returncode != 0:
        return {"ok": False, "exit": done.returncode, "error": "拉片工具报错了，原来的文件没有动；错误在上面几行。"}
    if not done.stdout.strip():
        return {"ok": False, "exit": 0, "error": "拉片工具什么都没输出，原来的文件没有动。"}
    out.parent.mkdir(parents=True, exist_ok=True)
    temporary = out.with_name(out.name + ".part")
    temporary.write_bytes(done.stdout)
    os.replace(temporary, out)
    return {"ok": True, "saved": str(out), "bytes": len(done.stdout)}


def main(argv: Optional[List[str]] = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="运行随技能附带的拉片工具，把它的输出存成 UTF-8 文件。")
    parser.add_argument("--out", type=Path, help="把输出存到这个文件；不给就直接显示")
    parser.add_argument("rest", nargs=argparse.REMAINDER, help="-- 后面是拉片工具自己的命令和参数")
    args = parser.parse_args(argv)
    rest = [word for index, word in enumerate(args.rest) if not (index == 0 and word == "--")]
    if not rest:
        emit({"ok": False, "error": "在 -- 后面写拉片工具的命令，例如：shots.py --out shots.json -- seed source.mp4 --track track.json"}, 2)
    node = shutil.which("node")
    if not node:
        emit({"ok": False, "error": "没找到 node：拉片工具要 Node.js 18 或更新的版本。先运行 doctor.py 看怎么装。"}, 2)
    command = [node, str(TOOL), *rest]
    if args.out is None:
        raise SystemExit(subprocess.run(command, check=False).returncode)
    result = save_output(command, args.out)
    emit(result, 0 if result["ok"] else (result["exit"] or 1))


if __name__ == "__main__":
    main()
