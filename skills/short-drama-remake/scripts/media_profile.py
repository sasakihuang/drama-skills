#!/usr/bin/env python3
"""Measure how a video sounds and moves, and compare a finished remake with its reference.

Everything here is measured by ffmpeg on this computer; nothing is uploaded. Run it on the
reference before writing the breakdown, and again on the finished film with ``--compare``.

    media_profile.py <video>                          # writes <video stem>.profile.json beside it
    media_profile.py <new film> --compare <reference.profile.json>

What it measures:

- loudness: integrated LUFS, loudness range, true peak;
- pauses: stretches quieter than -40 dBFS lasting 0.25 s or more. A reference with almost
  no pauses has a music bed (or wall-to-wall sound) under the voice;
- stills: stretches of 1 s or more where the picture barely changes (ffmpeg ``freezedetect``).
  A small progress bar or a swapped caption does not count as motion;
- speech rate, when a ``transcript.json`` from ``transcribe.py`` sits beside the video:
  spoken characters per second of speech.

``--compare`` prints which craft checks pass against the reference. They are craft checks
for the creator's review, not a release gate. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Tuple

MINIMUM_PYTHON = (3, 9)

# A remake is meant to run as long as its reference.
LENGTH_TOLERANCE = 0.10
# This many LU under the reference and the difference is plain to hear side by side.
QUIETER_NOTE = 3.0
# The loudest level mix_audio.py will mix to when asked to match a reference.
LOUDEST_MIX = -11.0

PAUSE_DB = -40
PAUSE_MIN = 0.25
LONG_PAUSE = 0.6
STILL_NOISE = 0.003
STILL_MIN = 1.0
SPOKEN = re.compile(r"[0-9A-Za-z㐀-鿿]")


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


def ffmpeg_log(args: List[str]) -> str:
    run = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", *args, "-f", "null", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    text = run.stderr.decode("utf-8", "replace")
    if run.returncode != 0:
        raise RuntimeError(text.strip().splitlines()[-1] if text.strip() else "ffmpeg failed")
    return text


def probe(path: Path) -> Tuple[float, bool, bool]:
    run = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    info = json.loads(run.stdout.decode("utf-8"))
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    return float(info["format"]["duration"]), "video" in kinds, "audio" in kinds


def spans(text: str, start_key: str, end_key: str, duration: float) -> List[Tuple[float, float]]:
    found: List[Tuple[float, float]] = []
    start: Optional[float] = None
    pattern = re.compile(r"(%s|%s): ?(-?[\d.]+)" % (re.escape(start_key), re.escape(end_key)))
    for key, value in pattern.findall(text):
        if key == start_key:
            start = max(0.0, float(value))
        elif start is not None:
            found.append((start, min(duration, float(value))))
            start = None
    if start is not None:
        found.append((start, duration))
    return [(a, b) for a, b in found if b > a]


def audio_profile(path: Path, duration: float) -> Dict[str, Any]:
    loud = ffmpeg_log(["-i", str(path), "-vn", "-af", "ebur128=peak=true"])
    summary = loud[loud.rfind("Summary:"):]

    def number(label: str) -> Optional[float]:
        hit = re.search(label + r":\s*(-?[\d.]+|-inf)", summary)
        return None if not hit or hit.group(1) == "-inf" else float(hit.group(1))

    silence = ffmpeg_log([
        "-i", str(path), "-vn", "-af", f"silencedetect=noise={PAUSE_DB}dB:d={PAUSE_MIN}",
    ])
    pauses = spans(silence, "silence_start", "silence_end", duration)
    # Leading and trailing quiet are not pauses inside the narration.
    inner = [(a, b) for a, b in pauses if a > 0.05 and b < duration - 0.05]
    lengths = [b - a for a, b in inner]
    per_minute = len(inner) / (duration / 60) if duration else 0.0
    return {
        "integrated_lufs": number("I"),
        "loudness_range_lu": number("LRA"),
        "true_peak_dbfs": number("Peak"),
        "pauses": len(inner),
        "long_pauses": sum(1 for x in lengths if x >= LONG_PAUSE),
        "longest_pause_s": round(max(lengths), 2) if lengths else 0.0,
        "pause_share": round(sum(lengths) / duration, 3) if duration else 0.0,
        "pauses_per_minute": round(per_minute, 1),
        # Speech alone leaves many short gaps; almost none means something plays underneath.
        "continuous_bed": per_minute < 1.0,
        "first_long_pauses": [[round(a, 2), round(b, 2)] for a, b in inner if b - a >= LONG_PAUSE][:12],
    }


def motion_profile(path: Path, duration: float) -> Dict[str, Any]:
    log = ffmpeg_log([
        "-i", str(path), "-an",
        "-vf", f"scale=480:-2,freezedetect=n={STILL_NOISE}:d={STILL_MIN}",
    ])
    stills = spans(log, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end", duration)
    lengths = [b - a for a, b in stills]
    opening = sum(min(b, 3.0) - a for a, b in stills if a < 3.0)
    return {
        "stills": len(stills),
        "longest_still_s": round(max(lengths), 2) if lengths else 0.0,
        "still_share": round(sum(lengths) / duration, 3) if duration else 0.0,
        "still_in_first_3s": round(max(0.0, opening), 2),
        "longest_stills": [
            [round(a, 2), round(b, 2)] for a, b in sorted(stills, key=lambda s: s[0] - s[1])[:8]
        ],
    }


def speech_profile(path: Path, transcript: Optional[Path]) -> Optional[Dict[str, Any]]:
    candidates = [transcript] if transcript else [path.with_name("transcript.json")]
    for candidate in candidates:
        if candidate and candidate.is_file():
            data = json.loads(candidate.read_text(encoding="utf-8"))
            segments = [s for s in data.get("segments", []) if not s.get("suspect")]
            spoken = sum(len(SPOKEN.findall(s.get("text", ""))) for s in segments)
            talking = sum(max(0.0, float(s["end"]) - float(s["start"])) for s in segments)
            if spoken and talking:
                return {
                    "transcript": candidate.name,
                    "spoken_chars": spoken,
                    "speech_seconds": round(talking, 2),
                    "chars_per_second": round(spoken / talking, 2),
                }
    return None


def profile(path: Path, transcript: Optional[Path]) -> Dict[str, Any]:
    duration, has_video, has_audio = probe(path)
    result: Dict[str, Any] = {"file": path.name, "duration_s": round(duration, 2)}
    result["audio"] = audio_profile(path, duration) if has_audio else None
    result["motion"] = motion_profile(path, duration) if has_video else None
    result["speech"] = speech_profile(path, transcript)
    return result


def compare(new: Dict[str, Any], ref: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Each check is a pass or a fail; a "note" is something to know that is neither: a comparison
    that could not be made, or a difference that is allowed but will be noticed."""
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str, note: bool = False) -> None:
        checks.append({"check": name, "ok": bool(ok), "level": "note" if note else ("pass" if ok else "fail"), "detail": detail})

    nd, rd = new["duration_s"], ref["duration_s"]
    check("时长接近", abs(nd - rd) <= rd * LENGTH_TOLERANCE,
          f"新片 {nd} 秒，原片 {rd} 秒（{nd / rd - 1:+.0%}；上下 {LENGTH_TOLERANCE:.0%} 以内算接近）")
    nm, rm = new.get("motion"), ref.get("motion")
    if nm and rm:
        limit = max(2.5, rm["longest_still_s"] + 1.0)
        check("画面不僵", nm["longest_still_s"] <= limit,
              f"最长不动 {nm['longest_still_s']} 秒，上限 {round(limit, 2)} 秒（原片 {rm['longest_still_s']} 秒）")
        check("不动的总时长", nm["still_share"] <= rm["still_share"] + 0.10,
              f"新片 {nm['still_share']:.0%}，原片 {rm['still_share']:.0%}")
        check("开头三秒在动", nm["still_in_first_3s"] <= max(1.0, rm["still_in_first_3s"]),
              f"前 3 秒不动 {nm['still_in_first_3s']} 秒，原片 {rm['still_in_first_3s']} 秒")
    na, ra = new.get("audio"), ref.get("audio")
    if na and ra:
        check("没有冷场", na["long_pauses"] <= ra["long_pauses"] + 2,
              f"0.6 秒以上的空白：新片 {na['long_pauses']} 处，原片 {ra['long_pauses']} 处")
        if ra["continuous_bed"]:
            check("垫了配乐", na["continuous_bed"],
                  f"原片声音几乎不断（每分钟 {ra['pauses_per_minute']} 处停顿），新片每分钟 {na['pauses_per_minute']} 处")
        loud = na["integrated_lufs"]
        check("音量够", loud is not None and loud >= -15.0,
              f"新片 {loud} LUFS，原片 {ra['integrated_lufs']} LUFS，至少 -15")
        heard = ra.get("integrated_lufs")
        if loud is not None and heard is not None and heard - loud >= QUIETER_NOTE:
            if loud >= LOUDEST_MIX - 0.7:
                check("和原片一样响", True, f"原片特别响（{heard} LUFS），新片 {loud} LUFS 已经混到上限，再响人声会被压扁。"
                      "放在一起新片会轻一些，这是有意的。", note=True)
            else:
                check("和原片一样响", True, f"新片比原片轻 {heard - loud:.1f} LU，和原片放在一起会明显轻一截。"
                      "混音时给 mix_audio.py 加 --like <参考片的 source.profile.json>，音量就贴着原片。", note=True)
        peak = na["true_peak_dbfs"]
        check("不爆音", peak is not None and peak <= -0.5, f"真峰值 {peak} dBFS，不超过 -0.5")
    ns, rs = new.get("speech"), ref.get("speech")
    if ns and rs:
        check("语速不拖", ns["chars_per_second"] >= rs["chars_per_second"] * 0.85,
              f"新片每秒 {ns['chars_per_second']} 字，原片 {rs['chars_per_second']} 字")
    elif rs:
        check("语速不拖", True, "没法比：成片旁边没有 transcript.json。先对成片跑一遍 transcribe.py，再跑一次这条对照。", note=True)
    return checks


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="Measure sound and motion; compare with a reference.")
    parser.add_argument("video", type=Path)
    parser.add_argument("--transcript", type=Path, help="transcript.json for this video")
    parser.add_argument("--compare", type=Path, help="the reference's .profile.json")
    parser.add_argument("--out", type=Path, help="where to write this video's profile")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            emit({"ok": False, "error": f"{tool} not found; install ffmpeg first"}, 2)
    if not args.video.is_file():
        emit({"ok": False, "error": f"no such file: {args.video}"}, 2)
    try:
        result = profile(args.video, args.transcript)
    except (RuntimeError, subprocess.CalledProcessError, KeyError, ValueError) as error:
        emit({"ok": False, "error": str(error)}, 1)

    out = args.out or args.video.with_name(args.video.stem + ".profile.json")
    payload: Dict[str, Any] = {"ok": True, "profile": str(out), **result}
    if args.compare:
        reference = json.loads(args.compare.read_text(encoding="utf-8"))
        checks = compare(result, reference)
        result["compared_with"] = args.compare.name
        result["checks"] = checks
        payload["checks"] = checks
        payload["failed"] = [c["check"] for c in checks if c["level"] == "fail"]
        payload["notes"] = [c["check"] for c in checks if c["level"] == "note"]
        label = {"pass": "通过  ", "fail": "不通过 ", "note": "提示  "}
        for item in checks:
            print(label[item["level"]] + item["check"] + "：" + item["detail"])
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit(payload)


if __name__ == "__main__":
    main()
