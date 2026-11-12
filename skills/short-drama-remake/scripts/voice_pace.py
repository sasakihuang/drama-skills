#!/usr/bin/env python3
"""Estimate how long a chosen voice takes to read a script, and remember what each voice really did.

A reference's speech rate says how fast its narrator talked, not how fast the voice the creator
picks will talk: measured on finished narration, the same script ran 17% to 28% longer than the
"reference rate x characters" figure. This module estimates from the voice itself.

    voice_pace.py estimate --voice-id <id> --speed 1.1 --text-file 旁白.txt [--audition 试听.mp3 --audition-text "那一句"]
    voice_pace.py learn --voice-id <id> --speed 1.1 --audio 旁白-001.mp3 --text "这一段的原文"
    voice_pace.py show [--voice-id <id>]

How it works: a clip is split into the time the voice is sounding and the time it is silent
between words, both measured from the audio. A voice then has two numbers at speed 1.0: seconds
per spoken character, and seconds per pause (a sentence break counts as 1.85 short pauses). They
are averages over every clip heard so far, started from general values that fade as clips arrive.
Voices differ little in the first number and up to four times in the second, which is why a
character count alone misleads. Tried on finished narration, the estimate landed within about 9%
with only the audition line to go on, and within about 5% with an earlier film in the same voice.

What is measured is kept in ``~/.drama-skills/voice-pace.json`` (numbers only: character and pause
counts, seconds), next to the creator's other settings, so it carries over between projects.
``references/voice-pace-seed.json`` ships a few voices measured in advance. Nothing here calls a
speech service or spends money. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import zh_text

MINIMUM_PYTHON = (3, 9)

SEED = Path(__file__).resolve().parents[1] / "references" / "voice-pace-seed.json"
# What a voice is assumed to do until it has been heard, at speed 1.0: seconds per spoken
# character and seconds per short pause. The weights say how much hearing it takes to outvote them.
PRIOR = (0.195, 0.40)
PRIOR_CHARS = 10.0
PRIOR_PAUSES = 12.0
PAUSE_RATIO = 1.85  # a sentence break lasts about this many short pauses
MAX_OBSERVATIONS = 120
MIN_CHARS = 6
SILENCE_DB = -40
SILENCE_MIN = 0.12

# characters, short pauses, sentence breaks, sounding seconds, silent seconds (both at speed 1.0)
Observation = Tuple[int, int, int, float, float]


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def store_path(override: Optional[Path] = None) -> Path:
    return override if override else Path.home() / ".drama-skills" / "voice-pace.json"


def measure_clip(path: Path) -> Dict[str, float]:
    """Total length, the quiet at each end, and the silences in between.

    ``net`` is the span from the first sound to the last; ``inner`` is how much of it is silent."""
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    total = float(probe.stdout.decode("utf-8", "replace").strip() or 0)
    if total <= 0:
        raise RuntimeError(f"cannot read the length of {path.name}")
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
         f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    log = run.stderr.decode("utf-8", "replace")
    starts = [float(x) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    lead = ends[0] if starts and ends and starts[0] < 0.05 else 0.0
    trail = 0.0
    if starts and (len(ends) < len(starts) or ends[-1] > total - 0.06):
        trail = max(0.0, total - starts[-1])
    net = max(0.0, total - lead - trail)
    inner = 0.0
    for index, start in enumerate(starts):
        end = ends[index] if index < len(ends) else total
        if start >= 0.05 and end <= total - 0.06:
            inner += end - start
    return {"total": round(total, 3), "lead": round(lead, 3), "trail": round(trail, 3),
            "net": round(net, 3), "inner": round(min(inner, net), 3)}


def observe(text: str, clip: Dict[str, float], speed: float) -> Optional[Observation]:
    """One clip as the model sees it; ``clip`` is what ``measure_clip`` returned."""
    chars, minor, major = zh_text.pause_features(text)
    net, inner = float(clip.get("net", 0)), float(clip.get("inner", 0))
    if chars < MIN_CHARS or net <= 0 or speed <= 0:
        return None
    return (chars, minor, major, round((net - inner) * speed, 3), round(inner * speed, 3))


def _read(path: Path) -> Dict[str, List[Observation]]:
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    voices: Dict[str, List[Observation]] = {}
    for voice_id, entry in (data.get("voices") or {}).items():
        rows = entry.get("clips") if isinstance(entry, dict) else entry
        kept: List[Observation] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 5:
                kept.append((int(row[0]), int(row[1]), int(row[2]), float(row[3]), float(row[4])))
        voices[voice_id] = kept
    return voices


def observations_for(voice_id: str, store: Optional[Path] = None) -> List[Observation]:
    """Everything known about one voice: the creator's own clips first, then the shipped ones."""
    own = _read(store_path(store)).get(voice_id, [])
    shipped = [row for row in _read(SEED).get(voice_id, []) if row not in own]
    return own + shipped


def remember(voice_id: str, new: Sequence[Observation], store: Optional[Path] = None,
             note: str = "") -> int:
    """Add clips to the creator's own record; returns how many were new."""
    path = store_path(store)
    data: Dict[str, Any] = {"note": "各音色的实测语速，由 voice_pace.py 维护；只有数字，没有内容。", "voices": {}}
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("voices", {})
    entry = data["voices"].setdefault(voice_id, {"clips": []})
    clips = [list(row) for row in entry.get("clips", [])]
    added = 0
    for row in new:
        if list(row) not in clips:
            clips.append(list(row))
            added += 1
    entry["clips"] = clips[-MAX_OBSERVATIONS:]
    if added:
        entry["updated"] = date.today().isoformat()
        if note:
            entry["last_source"] = note
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_name(path.name + ".part")
        partial.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        partial.replace(path)
    return added


def pause_units(minor: int, major: int) -> float:
    return minor + PAUSE_RATIO * major


def fit(observations: Sequence[Observation]) -> Tuple[float, float]:
    """Seconds per spoken character and per short pause for one voice at speed 1.0."""
    usable = [o for o in observations if o[0] >= MIN_CHARS]
    if not usable:
        return PRIOR
    per_char = (sum(o[3] for o in usable) + PRIOR_CHARS * PRIOR[0]) / (sum(o[0] for o in usable) + PRIOR_CHARS)
    per_pause = (sum(o[4] for o in usable) + PRIOR_PAUSES * PRIOR[1]) / (
        sum(pause_units(o[1], o[2]) for o in usable) + PRIOR_PAUSES)
    return (per_char, per_pause)


def evidence(observations: Sequence[Observation]) -> Tuple[str, float]:
    """How much the estimate rests on, and the margin to quote with it."""
    usable = [o for o in observations if o[0] >= MIN_CHARS]
    chars = sum(o[0] for o in usable)
    if not usable:
        return "general", 0.12
    if len(usable) < 4 or chars < 150:
        return "audition", 0.10
    return "history", 0.07


def seconds_at(params: Tuple[float, float], text: str, speed: float) -> float:
    chars, minor, major = zh_text.pause_features(text)
    return (params[0] * chars + params[1] * pause_units(minor, major)) / max(0.1, speed)


def typical_rate(params: Tuple[float, float], speed: float) -> float:
    """Characters per second for ordinary narration: a short pause about every nine characters, a
    sentence break about every twenty-two. Used to turn seconds into a character budget."""
    return speed / (params[0] + params[1] * (1.0 / 9.0 + PAUSE_RATIO / 22.0))


def estimate(segments: Sequence[str], voice_id: str, speed: float,
             extra: Sequence[Observation] = (), gap: float = 0.3,
             store: Optional[Path] = None) -> Dict[str, Any]:
    """Estimated narration length for a list of segments read by one voice."""
    known = list(extra) + [o for o in observations_for(voice_id, store) if o not in extra]
    params = fit(known)
    basis, margin = evidence(known)
    per_segment = [round(seconds_at(params, text, speed), 2) for text in segments]
    voiced = sum(per_segment)
    total = voiced + gap * max(0, len(per_segment) - 1)
    return {
        "voice_id": voice_id,
        "speed": speed,
        "basis": basis,
        "clips_known": len([o for o in known if o[0] >= MIN_CHARS]),
        "margin": margin,
        "per_character_s": round(params[0], 3),
        "short_pause_s": round(params[1], 2),
        "sentence_break_s": round(params[1] * PAUSE_RATIO, 2),
        "typical_chars_per_second": round(typical_rate(params, speed), 2),
        "segments_s": per_segment,
        "voiced_s": round(voiced, 1),
        "total_s": round(total, 1),
        "chars": sum(zh_text.spoken_len(text) for text in segments),
    }


def read_segments(args: argparse.Namespace) -> List[str]:
    if args.text:
        return [args.text]
    if not args.text_file:
        emit({"ok": False, "error": "给 --text 或 --text-file"}, 2)
    raw = args.text_file.read_text(encoding="utf-8")
    return [block.strip() for block in re.split(r"\n\s*\n", raw) if zh_text.spoken_len(block)]


BASIS_NAMES = {"general": "通用参数", "audition": "试听", "history": "以前配过的片子"}


def cmd_estimate(args: argparse.Namespace) -> None:
    extra: List[Observation] = []
    if args.audition:
        if not args.audition_text:
            emit({"ok": False, "error": "给了 --audition 就要同时给 --audition-text（试听念的那一句）"}, 2)
        clip = measure_clip(args.audition)
        seen = observe(args.audition_text, clip, args.speed if args.audition_speed is None else args.audition_speed)
        if seen:
            extra.append(seen)
    result = estimate(read_segments(args), args.voice_id, args.speed, extra, args.gap, args.store)
    minutes, seconds = divmod(int(round(result["total_s"])), 60)
    print(f"这个音色念完大约 {minutes} 分 {seconds:02d} 秒（上下 {int(result['margin'] * 100)}% 以内），"
          f"共 {result['chars']} 字。依据：{BASIS_NAMES[result['basis']]}。")
    emit({"ok": True, **result})


def cmd_learn(args: argparse.Namespace) -> None:
    items: List[Dict[str, Any]] = []
    if args.manifest:
        items = json.loads(args.manifest.read_text(encoding="utf-8"))
    elif args.audio and args.text:
        items = [{"audio": str(args.audio), "text": args.text}]
    else:
        emit({"ok": False, "error": "给 --audio 和 --text，或者 --manifest"}, 2)
    by_voice: Dict[str, List[Observation]] = {}
    for item in items:
        voice_id = item.get("voice_id") or args.voice_id
        speed = float(item.get("speed") or args.speed)
        if not voice_id:
            emit({"ok": False, "error": "缺 voice_id"}, 2)
        seen = observe(item["text"], measure_clip(Path(item["audio"])), speed)
        if seen:
            by_voice.setdefault(voice_id, []).append(seen)
    added = {voice: remember(voice, rows, args.store, args.project or "") for voice, rows in by_voice.items()}
    emit({"ok": True, "added": added, "store": str(store_path(args.store))})


def cmd_show(args: argparse.Namespace) -> None:
    own = _read(store_path(args.store))
    shipped = _read(SEED)
    names = [args.voice_id] if args.voice_id else sorted(set(own) | set(shipped))
    rows = []
    for name in names:
        known = observations_for(name, args.store)
        params = fit(known)
        basis, margin = evidence(known)
        rows.append({"voice_id": name, "clips": len(known), "basis": basis, "margin": margin,
                     "per_character_s": round(params[0], 3), "short_pause_s": round(params[1], 2),
                     "typical_chars_per_second_at_1.0": round(typical_rate(params, 1.0), 2)})
        print(f"- {name}：{len(known)} 段，普通旁白每秒约 {rows[-1]['typical_chars_per_second_at_1.0']} 字"
              f"（语速 1.0；每次短停顿约 {rows[-1]['short_pause_s']} 秒）")
    emit({"ok": True, "store": str(store_path(args.store)), "voices": rows})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="估一个音色念完一份稿要多久；记下每个音色的实测语速。")
    parser.add_argument("--store", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    est = sub.add_parser("estimate", help="估时长")
    est.add_argument("--voice-id", required=True)
    est.add_argument("--speed", type=float, default=1.0)
    est.add_argument("--text")
    est.add_argument("--text-file", type=Path, help="旁白稿；空行分段")
    est.add_argument("--audition", type=Path, help="创作者选中的那条试听")
    est.add_argument("--audition-text", help="试听念的那一句")
    est.add_argument("--audition-speed", type=float, help="试听用的语速，和 --speed 不同时才给")
    est.add_argument("--gap", type=float, default=0.3, help="段与段之间留的秒数")
    learn = sub.add_parser("learn", help="把配好的音频记进实测语速")
    learn.add_argument("--voice-id")
    learn.add_argument("--speed", type=float, default=1.0)
    learn.add_argument("--audio", type=Path)
    learn.add_argument("--text")
    learn.add_argument("--manifest", type=Path, help='JSON 列表：[{"audio","text","voice_id","speed"}]')
    learn.add_argument("--project")
    show = sub.add_parser("show", help="看已经量过的音色")
    show.add_argument("--voice-id")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if args.command != "show" and not shutil.which(tool):
            emit({"ok": False, "error": f"{tool} not found; install ffmpeg first"}, 2)
    try:
        {"estimate": cmd_estimate, "learn": cmd_learn, "show": cmd_show}[args.command](args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
