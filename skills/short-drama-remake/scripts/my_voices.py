#!/usr/bin/env python3
"""「我的音色」：把创作者听中的配音音色存下来，以后做新片直接复用。

    my_voices.py list
    my_voices.py save --name 财经讲解男声 --voice-id male-qn-jingying --speed 1.1 --sample 试听/精英青年.mp3 --project 放量突破
    my_voices.py used --name 财经讲解男声 --project 下一条片子
    my_voices.py remove --name 财经讲解男声

The list lives in the creator's own folder, next to the provider settings
(``~/.drama-skills/我的音色/voices.json``), so it carries over between projects. ``save`` copies the
audition clip beside it so the creator can listen again later. Saving the same name again updates
that entry. Nothing here calls a provider or spends money. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

MINIMUM_PYTHON = (3, 9)

CATALOG = Path(__file__).resolve().parents[1] / "references" / "voice-catalog.json"


def emit(payload: Dict[str, Any], code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def store_dir(override: Optional[Path] = None) -> Path:
    return override if override else Path.home() / ".drama-skills" / "我的音色"


def load(folder: Path) -> List[Dict[str, Any]]:
    path = folder / "voices.json"
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8")).get("voices", [])


def dump(folder: Path, voices: List[Dict[str, Any]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    data = {"note": "我的音色：创作者听中、想以后复用的配音音色。由 my_voices.py 维护。", "voices": voices}
    partial = folder / "voices.json.part"
    partial.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    partial.replace(folder / "voices.json")


def catalog_entry(voice_id: str) -> Dict[str, Any]:
    if CATALOG.is_file():
        for v in json.loads(CATALOG.read_text(encoding="utf-8")).get("voices", []):
            if v["id"] == voice_id:
                return v
    return {}


def safe_name(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\s]+', "_", name).strip("._") or "音色"


def find(voices: List[Dict[str, Any]], name: str) -> int:
    for n, v in enumerate(voices):
        if v["name"] == name:
            return n
    return -1


def cmd_list(folder: Path) -> None:
    voices = load(folder)
    if not voices:
        print("还没有存过音色。")
    for v in voices:
        extra = f"，{v['speed']} 倍速" if v.get("speed") else ""
        used = f"，用过 {len(v.get('used_in', []))} 次" if v.get("used_in") else ""
        print(f"- {v['name']}：{v.get('voice_name') or v['voice_id']}{extra}{used}")
    emit({"ok": True, "store": str(folder), "voices": voices})


def cmd_save(folder: Path, args: argparse.Namespace) -> None:
    if not 0.5 <= args.speed <= 2.0:
        emit({"ok": False, "error": "speed 要在 0.5 到 2.0 之间"}, 2)
    voices = load(folder)
    known = catalog_entry(args.voice_id)
    entry: Dict[str, Any] = {
        "name": args.name,
        "voice_id": args.voice_id,
        "voice_name": known.get("name", ""),
        "speed": args.speed,
        "saved": date.today().isoformat(),
    }
    if args.emotion:
        entry["emotion"] = args.emotion
    if args.note:
        entry["note"] = args.note
    if args.sample:
        if not args.sample.is_file():
            emit({"ok": False, "error": f"找不到试听文件：{args.sample}"}, 2)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (safe_name(args.name) + args.sample.suffix.lower())
        shutil.copyfile(args.sample, target)
        entry["sample"] = str(target)
    at = find(voices, args.name)
    if at >= 0:
        entry["used_in"] = voices[at].get("used_in", [])
        voices[at] = {**voices[at], **entry}
    else:
        voices.append(entry)
    if args.project:
        mark_used(voices[at if at >= 0 else -1], args.project)
    dump(folder, voices)
    result: Dict[str, Any] = {"ok": True, "saved": voices[at if at >= 0 else -1], "store": str(folder)}
    if not known:
        result["warning"] = "这个编号不在系统音色清单里；如果是账号里的专属音色，可以照常使用。"
    emit(result)


def mark_used(entry: Dict[str, Any], project: str) -> None:
    used = entry.setdefault("used_in", [])
    if not any(u.get("project") == project for u in used):
        used.append({"project": project, "date": date.today().isoformat()})


def cmd_used(folder: Path, args: argparse.Namespace) -> None:
    voices = load(folder)
    at = find(voices, args.name)
    if at < 0:
        emit({"ok": False, "error": f"没有叫「{args.name}」的音色；先运行 list 看看"}, 2)
    mark_used(voices[at], args.project)
    dump(folder, voices)
    emit({"ok": True, "voice": voices[at]})


def cmd_remove(folder: Path, args: argparse.Namespace) -> None:
    voices = load(folder)
    at = find(voices, args.name)
    if at < 0:
        emit({"ok": False, "error": f"没有叫「{args.name}」的音色"}, 2)
    gone = voices.pop(at)
    sample = gone.get("sample")
    if sample and Path(sample).is_file() and Path(sample).parent == folder:
        Path(sample).unlink()
    dump(folder, voices)
    emit({"ok": True, "removed": gone["name"], "left": len(voices)})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="我的音色：存下听中的配音音色，以后复用。")
    parser.add_argument("--store", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="列出存好的音色")
    save = sub.add_parser("save", help="存一个音色（同名就更新）")
    save.add_argument("--name", required=True, help="创作者给它起的名字，例如「财经讲解男声」")
    save.add_argument("--voice-id", required=True)
    save.add_argument("--speed", type=float, default=1.0)
    save.add_argument("--emotion")
    save.add_argument("--sample", type=Path, help="听中的那条试听录音")
    save.add_argument("--note", help="一句话备注，例如适合什么片子")
    save.add_argument("--project", help="这次用在哪个项目")
    used = sub.add_parser("used", help="记一笔：这个音色用在了哪个项目")
    used.add_argument("--name", required=True)
    used.add_argument("--project", required=True)
    remove = sub.add_parser("remove", help="删掉一个音色")
    remove.add_argument("--name", required=True)
    args = parser.parse_args()

    folder = store_dir(args.store)
    try:
        if args.command == "list":
            cmd_list(folder)
        elif args.command == "save":
            cmd_save(folder, args)
        elif args.command == "used":
            cmd_used(folder, args)
        else:
            cmd_remove(folder, args)
    except (OSError, ValueError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
