#!/usr/bin/env python3
"""Mix the narration, the music bed and the sound effects into one track at a steady loudness.

    mix_audio.py --voice <项目>/制作/旁白.wav --bed <项目>/制作/配乐/配乐垫底.wav \
                 --sfx <项目>/制作/音效.json --sfx-root <音效库目录> --out <项目>/制作/混音.wav

``--sfx`` is a JSON list of ``{"file": "whoosh.mp3", "at": 4.3, "volume": 0.5}``: which effect,
when it starts (seconds of film time, read from 时间表.json) and how loud it is beside the
narration: 1 is as loud as the voice, 0.5 is 6 dB under it (0.5 when left out). The narration and
every effect file are measured, so the number means the same whatever level a file was recorded at.
A ``file`` that is not an absolute path is looked for beside the JSON, then under ``--sfx-root``.
The bed is used at the level ``fetch_music.py`` already set; this script does not change it.
A film nobody speaks in is mixed without ``--voice``: the music then runs the film's length and an
effect's volume is its loudness beside the music instead.

The mix runs as long as the narration (``--length`` makes it longer, for an end card) and is
brought to ``--target-lufs`` (-13 by default): one fixed gain for the whole film, so the level does
not pump, and a limiter that holds the peaks under the ceiling, so the loudness asked for is the
loudness delivered. ``--like <参考片目录>/source.profile.json`` aims at the reference's own loudness
instead (kept between -14 and -11): a remake much quieter than the film it sits next to sounds
weak. The result is written as 48 kHz stereo WAV, measured and reported. Everything runs on this
computer. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional

MINIMUM_PYTHON = (3, 9)

LAYOUT = "aformat=sample_rates=48000:channel_layouts=stereo"
# An effect's volume is its loudness beside the narration: 1 is as loud as the voice. Effect files
# are recorded at very different levels (in the library the kit uses, a bass impact is 17 LU hotter
# than a pop) and so are voices. Used as a plain multiplier, 0.5 put that impact 10 dB over a
# quiet voice for two seconds: the first sentence of the film could not be heard.
DEFAULT_SFX_VOLUME = 0.5
# Effects are laid onto tracks this many at a time. One command holding every cue of a long film
# is longer than Windows allows (it gave out near 145 cues, an eleven-minute film).
EFFECTS_PER_TRACK = 80
# An effect track is written this much under its real level and brought back up in the mix, so
# effects that overlap have room to add up without clipping in the file in between.
TRACK_HEADROOM_DB = 12
DEFAULT_TARGET = -13.0
# Where the limiter holds sample peaks. It has to leave room for two things: true peaks land a little
# above sample peaks, and encoding to AAC adds up to another decibel and a half. The renderer measures
# the encoded track and turns all of it down when its true peak passes -1.5 dBFS, which with a ceiling
# of -2 left finished films about 1 LU quieter than the level they were mixed to.
CEILING_DB = -3.5
# A reference is matched only inside this range: quieter than -14 is too quiet for a phone, and
# past -11 the limiter has to squash the voice.
LIKE_RANGE = (-14.0, -11.0)


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def run(args: List[str]) -> str:
    done = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    log = done.stderr.decode("utf-8", "replace")
    if done.returncode != 0:
        tail = log.strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "ffmpeg failed")
    return log


def duration(path: Path) -> float:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return float(done.stdout.decode("utf-8", "replace").strip() or 0)


def read_effects(listing: Optional[Path], root: Optional[Path]) -> List[Dict[str, Any]]:
    if listing is None:
        return []
    effects = []
    for item in json.loads(listing.read_text(encoding="utf-8")):
        name = Path(str(item["file"]))
        choices = [name] if name.is_absolute() else [listing.parent / name] + ([root / name] if root else [])
        found = next((choice for choice in choices if choice.is_file()), None)
        if found is None:
            raise RuntimeError(f"找不到音效文件：{item['file']}")
        at = float(item.get("at", 0))
        volume = float(item.get("volume", DEFAULT_SFX_VOLUME))
        if at < 0 or not 0 < volume <= 2:
            raise RuntimeError(f"音效 {item['file']} 的时间或音量不对")
        effects.append({"file": found, "at": at, "volume": volume})
    return effects


def effects_track(effects: List[Dict[str, Any]], length: float, out: Path) -> None:
    """One track as long as the film with these effects at their moments. Each effect file is read
    once however often it is used."""
    files: List[Path] = []
    for effect in effects:
        if effect["file"] not in files:
            files.append(effect["file"])
    inputs = ["-f", "lavfi", "-t", f"{length:.3f}", "-i", "anullsrc=r=48000:cl=stereo"]
    parts: List[str] = []
    for k, file in enumerate(files):
        inputs += ["-i", str(file)]
        uses = [n for n, effect in enumerate(effects) if effect["file"] == file]
        taps = "".join(f"[s{n}]" for n in uses)
        parts.append(f"[{k + 1}:a]{LAYOUT}" + (f",asplit={len(uses)}" if len(uses) > 1 else "") + taps)
    names = ["[0:a]"]
    for n, effect in enumerate(effects):
        delay = int(round(effect["at"] * 1000))
        parts.append(f"[s{n}]adelay={delay}|{delay},volume={effect['gain_db']:.2f}dB[fx{n}]")
        names.append(f"[fx{n}]")
    parts.append("".join(names) + f"amix=inputs={len(names)}:duration=first:normalize=0,volume=-{TRACK_HEADROOM_DB}dB[out]")
    # Lossless and mostly silence, so a few MB however long the film is.
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", ";".join(parts),
         "-map", "[out]", "-ar", "48000", "-c:a", "flac", str(out)])


def graph(has_voice: bool, has_bed: bool, tracks: int, length: float) -> str:
    """Everything up to the mixed, unlevelled signal, labelled [mix]: the voice and the bed, whichever
    there are, then the effect tracks, in that order of inputs. The first is padded or cut to the
    film's length, and the mix runs as long as it."""
    parts: List[str] = []
    names: List[str] = []
    index = 0
    for present, label in ((has_voice, "voice"), (has_bed, "bed")):
        if not present:
            continue
        fit = f",apad=whole_dur={length:.6f},atrim=0:{length:.6f}" if index == 0 else ""
        parts.append(f"[{index}:a]{LAYOUT}{fit}[{label}]")
        names.append(f"[{label}]")
        index += 1
    for n in range(tracks):
        parts.append(f"[{index + n}:a]{LAYOUT},volume={TRACK_HEADROOM_DB}dB[fx{n}]")
        names.append(f"[fx{n}]")
    parts.append("".join(names) + f"amix=inputs={len(names)}:duration=first:normalize=0[mix]")
    return ";".join(parts)


def measure(path: Path) -> Dict[str, Optional[float]]:
    log = run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"])
    summary = log[log.rfind("Summary:"):]

    def number(label: str) -> Optional[float]:
        hit = re.search(label + r":\s*(-?[\d.]+|-inf)", summary)
        return None if not hit or hit.group(1) == "-inf" else float(hit.group(1))

    return {"integrated_lufs": number("I"), "true_peak_dbfs": number("Peak"), "loudness_range_lu": number("LRA")}


def loudness_in(log: str) -> Optional[float]:
    """The integrated loudness a run of the meter reported; None for silence, which it reports as -70."""
    summary = log[log.rfind("Summary:"):]
    hit = re.search(r"\bI[:]\s*(-?[\d.]+|-inf)", summary)
    if not hit or hit.group(1) == "-inf" or float(hit.group(1)) <= -69.0:
        return None
    return float(hit.group(1))


def loudness_of(path: Path, short: bool = False) -> Optional[float]:
    """How loud a file is by itself. An effect can be shorter than the meter's 0.4-second window,
    so it is measured with a second of silence after it."""
    chain = ("apad=pad_dur=1," if short else "") + "ebur128"
    return loudness_in(run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0", "-af", chain, "-f", "null", "-"]))


def place_effects(effects: List[Dict[str, Any]], spoken: float) -> None:
    """Give each effect the gain that puts it at its volume beside the narration (or the music, in a
    film nobody speaks in)."""
    levels: Dict[Path, float] = {}
    for effect in effects:
        file = effect["file"]
        if file not in levels:
            level = loudness_of(file, short=True)
            if level is None:
                raise RuntimeError(f"音效 {file.name} 量不出音量：文件是静音的？")
            levels[file] = level
        effect["gain_db"] = spoken + 20 * math.log10(effect["volume"]) - levels[file]


def target_like(profile: Path) -> float:
    """The loudness to aim at so the new film sits beside its reference, kept inside LIKE_RANGE."""
    audio = json.loads(profile.read_text(encoding="utf-8")).get("audio") or {}
    heard = audio.get("integrated_lufs")
    if not isinstance(heard, (int, float)):
        raise RuntimeError(f"{profile.name} 里没有原片的音量：先对原片跑 media_profile.py")
    return round(min(max(float(heard), LIKE_RANGE[0]), LIKE_RANGE[1]), 1)


def mix(voice: Optional[Path], bed: Optional[Path], effects: List[Dict[str, Any]], out: Path,
        target: float, length: Optional[float]) -> Dict[str, Any]:
    if voice is None and bed is None:
        raise RuntimeError("旁白和配乐至少要给一样")
    if voice is not None:
        film = max(length or 0.0, duration(voice))
    else:
        film = length or duration(bed)  # type: ignore[arg-type]
    if film <= 0:
        raise RuntimeError("旁白是空的" if voice is not None else "配乐是空的")
    with tempfile.TemporaryDirectory() as scratch:
        return mix_tracks(voice, bed, effects, out, target, film, Path(scratch))


def mix_tracks(voice: Optional[Path], bed: Optional[Path], effects: List[Dict[str, Any]], out: Path,
               target: float, film: float, scratch: Path) -> Dict[str, Any]:
    if voice is not None:
        spoken = loudness_of(voice)
        if spoken is None:
            raise RuntimeError("旁白量不出音量：是静音的，还是不到半秒？")
    else:
        # Nobody speaks: the music is what the effects have to sit beside.
        spoken = loudness_of(bed) if bed is not None else None
        if spoken is None:
            raise RuntimeError("配乐量不出音量：是静音的？")
    place_effects(effects, spoken)
    inputs: List[str] = []
    if voice is not None:
        inputs += ["-i", str(voice)]
    if bed:
        inputs += ["-i", str(bed)]
    batches = [effects[start:start + EFFECTS_PER_TRACK] for start in range(0, len(effects), EFFECTS_PER_TRACK)]
    for number, batch in enumerate(batches):
        track = scratch / f"effects-{number}.flac"
        effects_track(batch, film, track)
        inputs += ["-i", str(track)]
    base = graph(voice is not None, bed is not None, len(batches), film)
    raw = loudness_in(run(["ffmpeg", "-hide_banner", "-nostats", *inputs, "-filter_complex",
                           base + ";[mix]ebur128=peak=true[out]", "-map", "[out]", "-f", "null", "-"]))
    if raw is None:
        raise RuntimeError("混出来没有声音：旁白是静音的？")
    out.parent.mkdir(parents=True, exist_ok=True)
    ceiling = 10 ** (CEILING_DB / 20.0)
    gain = target - raw
    measured: Dict[str, Optional[float]] = {}
    # One gain for the whole film, then a limiter over the peaks. Holding peaks down takes a little
    # loudness with it, so the gain is topped up once or twice until the target is met.
    for _ in range(4):
        level = f"[mix]volume={gain:.2f}dB,alimiter=limit={ceiling:.4f}:attack=5:release=60:level=disabled[out]"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *inputs, "-filter_complex", base + ";" + level,
             "-map", "[out]", "-map_metadata", "-1", "-ar", "48000", "-c:a", "pcm_s16le", str(out)])
        measured = measure(out)
        got = measured["integrated_lufs"]
        if got is None or abs(target - got) <= 0.3:
            break
        gain += target - got
    result: Dict[str, Any] = {"mix": str(out), "length_s": round(duration(out), 2), "bed": bool(bed),
                              "effects": len(effects), "target_lufs": target,
                              "narration_lufs": spoken if voice is not None else None,
                              "effects_beside": "narration" if voice is not None else "music", **measured}
    loud, peak = result["integrated_lufs"], result["true_peak_dbfs"]
    result["loud_enough"] = loud is not None and loud >= -15.0
    result["no_clipping"] = peak is not None and peak <= -0.5
    return result


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="把旁白、配乐垫底和音效混成一条，整片统一音量。")
    parser.add_argument("--voice", type=Path, help="voice_track.py 拼好的旁白.wav；原片没人说话的片子不给，音效按配乐的响度算")
    parser.add_argument("--bed", type=Path, help="fetch_music.py 做好的配乐垫底.wav；没有配乐就不给")
    parser.add_argument("--sfx", type=Path, help='音效清单 JSON：[{"file","at","volume"}]；volume 是相对旁白的响度，1 和旁白一样响')
    parser.add_argument("--sfx-root", type=Path, help="音效库目录，清单里的文件名到这里找")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--target-lufs", type=float, default=DEFAULT_TARGET)
    parser.add_argument("--like", type=Path, help="参考片的 source.profile.json：音量贴着原片（限在 -14 到 -11 之间）")
    parser.add_argument("--length", type=float, help="全片秒数；比旁白长时才需要给")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            emit({"ok": False, "error": f"{tool} not found; install ffmpeg first"}, 2)
    for name, path in (("旁白", args.voice), ("配乐垫底", args.bed), ("音效清单", args.sfx), ("参考片的量测结果", args.like)):
        if path is not None and not path.is_file():
            emit({"ok": False, "error": f"找不到{name}：{path}"}, 2)
    if args.voice is None and args.bed is None:
        emit({"ok": False, "error": "旁白和配乐至少要给一样：--voice 或 --bed"}, 2)
    if not -24.0 <= args.target_lufs <= -9.0:
        emit({"ok": False, "error": "--target-lufs 要在 -24 到 -9 之间"}, 2)
    try:
        target = target_like(args.like) if args.like else args.target_lufs
        effects = read_effects(args.sfx, args.sfx_root)
        result = mix(args.voice, args.bed, effects, args.out, target, args.length)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        emit({"ok": False, "error": str(error)}, 1)
    print(f"混好了：{result['length_s']} 秒，{result['integrated_lufs']} LUFS（目标 {target}），真峰值 {result['true_peak_dbfs']} dBFS，"
          f"{'垫了配乐' if result['bed'] else '没有配乐'}，音效 {result['effects']} 个。")
    emit({"ok": True, **result})


if __name__ == "__main__":
    main()
