#!/usr/bin/env python3
"""Pick, download and prepare a free background track from the curated Mixkit catalog.

The catalog (``references/music-catalog.json``) lists 32 Mixkit tracks in eight moods. Every one
is under the Mixkit Stock Music Free License (commercial and social-media use, no attribution) and
was downloaded from a mainland China connection without a proxy when the list was made. Tracks
outside the catalog are refused: their licence has not been checked.

    fetch_music.py list [--mood 科技财经]
    fetch_music.py fetch <id> --out <dir> --voice <narration.wav> --length <seconds>

``fetch`` downloads ``<id>.mp3`` into ``--out`` (reused when already there), measures it, and
writes ``配乐垫底.wav``: the music from its first sound to its last (fade-out tails dropped), looped with 1.5 s crossfades when the
film is longer than the track, cut to ``--length``, faded in and out, and set ``--gap`` LU under
the narration's measured loudness (13 by default). It also writes ``配乐来源.md`` for the delivery
notes. The bed is ready to mix as is: no further volume change is needed. The last stdout line is
one JSON object.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

MINIMUM_PYTHON = (3, 9)

CATALOG = Path(__file__).resolve().parents[1] / "references" / "music-catalog.json"
# Mixkit serves each track at <base><id>/<id>.mp3; a browser-like agent avoids a 403.
MIXKIT_MUSIC = "https://assets.mixkit.co/music/"
MIXKIT_LICENSE = "https://mixkit.co/license/#musicFree"
AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
CROSSFADE = 1.5


def emit(payload: Dict[str, Any], code: int = 0) -> None:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def load_catalog() -> Dict[str, Any]:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def loudness(path: Path) -> float:
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "ebur128", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    found = re.findall(r"I:[ \t]+(-?[\d.]+) LUFS", run.stderr.decode("utf-8", "replace"))
    if not found:
        raise RuntimeError(f"could not measure loudness of {path.name}")
    return float(found[-1])


def first_sound(path: Path) -> float:
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-t", "10", "-i", str(path), "-af",
         "silencedetect=noise=-45dB:d=0.2", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    text = run.stderr.decode("utf-8", "replace")
    start = re.search(r"silence_start: (-?[\d.]+)", text)
    end = re.search(r"silence_end: ([\d.]+)", text)
    if start and end and abs(float(start.group(1))) < 0.05:
        return float(end.group(1))
    return 0.0


def last_sound(path: Path, total: float) -> float:
    """Where the music really ends: a fade-out tail would leave a hole at every loop seam."""
    window = min(20.0, total)
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-sseof", f"-{window:.3f}", "-i", str(path), "-af",
         "areverse,silencedetect=noise=-35dB:d=0.2", "-f", "null", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    text = run.stderr.decode("utf-8", "replace")
    start = re.search(r"silence_start: (-?[\d.]+)", text)
    end = re.search(r"silence_end: ([\d.]+)", text)
    if start and end and abs(float(start.group(1))) < 0.05:
        return max(0.0, total - float(end.group(1)))
    return total


def duration(path: Path) -> float:
    run = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return float(run.stdout.decode("utf-8").strip() or 0)


def download(track_id: int, target: Path) -> None:
    url = MIXKIT_MUSIC + f"{track_id}/{track_id}.mp3"
    last: Optional[Exception] = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": AGENT})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            if len(data) < 50_000:
                raise RuntimeError(f"download too small ({len(data)} bytes)")
            partial = target.with_suffix(".part")
            partial.write_bytes(data)
            partial.replace(target)
            return
        except (urllib.error.URLError, OSError, RuntimeError) as error:
            last = error
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"could not download track {track_id}: {last}")


def render_bed(source: Path, out: Path, start: float, usable: float, length: float,
               gain_db: float, fade_in: float, fade_out: float) -> int:
    copies = 1 if usable >= length else math.ceil((length - CROSSFADE) / (usable - CROSSFADE))
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for _ in range(copies):
        args += ["-ss", f"{start:.3f}", "-t", f"{usable:.3f}", "-i", str(source)]
    chain: List[str] = []
    label = "[0:a]"
    for k in range(1, copies):
        chain.append(f"{label}[{k}:a]acrossfade=d={CROSSFADE}:c1=tri:c2=tri[x{k}]")
        label = f"[x{k}]"
    chain.append(
        f"{label}atrim=0:{length:.3f},asetpts=N/SR/TB,volume={gain_db:.2f}dB,"
        f"afade=t=in:d={fade_in},afade=t=out:st={max(0.0, length - fade_out):.3f}:d={fade_out},"
        "aformat=sample_rates=48000:channel_layouts=stereo[bed]"
    )
    args += ["-filter_complex", ";".join(chain), "-map", "[bed]", "-c:a", "pcm_s16le", str(out)]
    run = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode != 0:
        raise RuntimeError(run.stderr.decode("utf-8", "replace").strip().splitlines()[-1])
    return copies


def cmd_list(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    tracks = [t for t in catalog["tracks"] if not args.mood or t["mood"] == args.mood]
    for t in tracks:
        print(f"{t['id']:>5}  {t['mood']}  {t['seconds']:>6.1f}s  {t['name']}（{t['genre']}）")
    emit({"ok": True, "moods": catalog["moods"], "tracks": tracks})


def cmd_fetch(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    track = next((t for t in catalog["tracks"] if t["id"] == args.id), None)
    if track is None:
        emit({"ok": False, "error": f"track {args.id} is not in the checked catalog; pick one from `list`"}, 2)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    source = out / f"mixkit-{args.id}.mp3"
    if not source.is_file() or duration(source) < 10:
        download(args.id, source)
    seconds = duration(source)
    start = first_sound(source)
    end = last_sound(source, seconds)
    music = loudness(source)
    result: Dict[str, Any] = {
        "ok": True, "id": args.id, "name": track["name"], "artist": track["artist"], "mood": track["mood"],
        "file": str(source), "seconds": round(seconds, 2), "first_sound_s": round(start, 2),
        "last_sound_s": round(end, 2),
        "music_lufs": music, "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }
    if args.voice:
        voice = loudness(args.voice)
        gain = (voice - args.gap) - music
        result.update({"voice_lufs": voice, "gap_lu": args.gap, "gain_db": round(gain, 2)})
        if args.length:
            bed = out / "配乐垫底.wav"
            copies = render_bed(source, bed, start, end - start, args.length, gain,
                                args.fade_in, args.fade_out)
            result.update({"bed": str(bed), "length_s": args.length, "copies": copies,
                           "bed_lufs": loudness(bed)})
    notes = out / "配乐来源.md"
    notes.write_text(
        "# 配乐来源\n\n"
        f"- 曲目：{track['name']}（{track['artist']}），Mixkit 编号 {args.id}\n"
        f"- 下载：{date.today().isoformat()}，文件 `{source.name}`，SHA-256 {result['sha256']}\n"
        f"- 许可：Mixkit Stock Music Free License（{MIXKIT_LICENSE}）。可用于商用和社交平台视频，"
        "不用署名；不可用于 CD/DVD、广播电视、游戏，不可混音成纯音乐作品，不可登记到版权识别系统。\n"
        "- 收到平台版权认领时，把详情发给 Mixkit 处理。\n",
        encoding="utf-8",
    )
    result["notes"] = str(notes)
    emit(result)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="Free background music from the checked Mixkit catalog.")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list", help="show the catalog")
    listing.add_argument("--mood")
    fetch = sub.add_parser("fetch", help="download one track and build the music bed")
    fetch.add_argument("id", type=int)
    fetch.add_argument("--out", type=Path, required=True, help="folder for the track and the bed")
    fetch.add_argument("--voice", type=Path, help="the finished narration, to set the music under it")
    fetch.add_argument("--length", type=float, help="film length in seconds; builds 配乐垫底.wav")
    fetch.add_argument("--gap", type=float, default=13.0, help="how far under the narration, in LU")
    fetch.add_argument("--fade-in", type=float, default=0.5)
    fetch.add_argument("--fade-out", type=float, default=1.5)
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            emit({"ok": False, "error": f"{tool} not found; install ffmpeg first"}, 2)
    if getattr(args, "voice", None) and not args.voice.is_file():
        emit({"ok": False, "error": f"no such narration file: {args.voice}"}, 2)
    try:
        cmd_list(args) if args.command == "list" else cmd_fetch(args)
    except (RuntimeError, subprocess.CalledProcessError, ValueError) as error:
        emit({"ok": False, "error": str(error),
              "hint": "检查网络能否打开 assets.mixkit.co；不行就换一首或改用创作者自己的音乐文件"}, 1)


if __name__ == "__main__":
    main()
