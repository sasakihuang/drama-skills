#!/usr/bin/env python3
"""Transcribe the reference video's speech on this computer (optional step).

Uses faster-whisper on the CPU (INT8, model ``small`` by default) when it is
importable from the Python running this script. When it is not installed the
script says so and how to set it up; the analysis then reads burned-in
subtitles from the frames instead, which most Douyin/Kuaishou/Xiaohongshu
videos carry anyway.

Outputs next to the source:

- ``transcript.json``: ``{"engine", "model", "language", "segments": [{start, end, text, suspect?}]}``
- ``transcript.txt``: one segment per line with ``[mm:ss.ss]`` start times

Lines that speech recognition typically hallucinates over music-only audio
("请点赞订阅", "字幕由…提供" and similar) are kept but marked ``suspect``: a real
call to action can say the same words, so the frames decide. The last stdout
line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

MINIMUM_PYTHON = (3, 9)

HALLUCINATION = re.compile(
    r"(点赞|订阅|转发|打赏|关注.{0,4}频道|感谢(您的)?观看|字幕由|字幕志愿者|明镜与点点|请不吝)"
)


def emit(ok: bool, **fields: Any) -> None:
    print(json.dumps({"ok": ok, **fields}, ensure_ascii=False))
    sys.exit(0 if ok else 1)


def stamp(seconds: float) -> str:
    minutes, rest = divmod(max(0.0, seconds), 60)
    return "%02d:%05.2f" % (minutes, rest)


def extract_wav(video: Path, wav: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", str(wav)],
        check=True,
    )


def main() -> None:
    if sys.version_info < MINIMUM_PYTHON:
        emit(False, error="需要 Python %d.%d 或更新版本" % MINIMUM_PYTHON)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="本机识别参考片台词（可选）")
    parser.add_argument("video", help="参考片，通常是 source.mp4")
    parser.add_argument("--model", default="small")
    parser.add_argument("--language", default="zh")
    parser.add_argument("--force", action="store_true", help="已有结果也重做")
    args = parser.parse_args()

    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        emit(False, error=f"找不到视频：{video}")
    out_json = video.with_name("transcript.json")
    out_txt = video.with_name("transcript.txt")
    if out_json.is_file() and out_json.stat().st_mtime >= video.stat().st_mtime and not args.force:
        emit(True, cached=True, transcript=str(out_json))
    if not shutil.which("ffmpeg"):
        emit(False, error="没装 ffmpeg，识别台词和拉片都要用它")
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError:
        emit(False, need_setup=True,
             error="这台电脑还没装本机识别台词的程序；不装也行，改读画面上的字幕",
             setup="见 SKILL.md「识别台词（可选）」一节")

    started = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "audio.wav"
        extract_wav(video, wav)
        model = WhisperModel(args.model, device="cpu", compute_type="int8", num_workers=1)
        segments, info = model.transcribe(
            str(wav), language=args.language or None, beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        kept: List[Dict[str, Any]] = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            item: Dict[str, Any] = {"start": round(segment.start, 2), "end": round(segment.end, 2), "text": text}
            if HALLUCINATION.search(text):
                item["suspect"] = True
            kept.append(item)

    result = {
        "engine": "faster-whisper cpu int8",
        "model": args.model,
        "language": getattr(info, "language", args.language),
        "seconds": round(time.time() - started, 1),
        "segments": kept,
    }
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out_txt.write_text(
        "".join(f"[{stamp(s['start'])}] {s['text']}{'（存疑）' if s.get('suspect') else ''}\n" for s in kept),
        encoding="utf-8",
    )
    suspect = sum(1 for s in kept if s.get("suspect"))
    emit(True, transcript=str(out_json), segments=len(kept), suspect=suspect, seconds=result["seconds"])


if __name__ == "__main__":
    main()
