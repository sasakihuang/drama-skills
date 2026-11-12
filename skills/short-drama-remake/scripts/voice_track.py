#!/usr/bin/env python3
"""Join the narration clips into one track and work out when every sentence is spoken.

    voice_track.py build --project <项目> --jobs <配音任务号，逗号隔开> --out <项目>/制作 [--reference <参考片目录>]
    voice_track.py build --manifest clips.json --out <项目>/制作

For each clip it trims the quiet at both ends, lines the clips up with a short gap between them,
and writes into ``--out``:

- ``旁白.wav``: the whole narration, 48 kHz stereo, ready to mix;
- ``时间表.json``: where each section starts and how long it runs, every sentence with its start
  and end, and caption lines cut to a readable length. Animation, captions and sound effects are
  all timed from this file;
- ``字幕.srt``: the same caption lines as a subtitle file.

Sentence times come from the audio, not from a guess by character count: a synthetic voice pauses
at punctuation, so each mark in the text is matched to a silence in the clip, and only a mark with
no silence near it falls back to its share of the spoken time.

``--jobs`` reads the clips, their text, voice and speed from the project's finished speech jobs
(``.short-drama/production/jobs``); ``--manifest`` takes a JSON list of
``{"id", "audio", "text", "voice_id", "speed"}`` for clips made any other way. What each voice
really did is added to the creator's own pace record (see ``voice_pace.py``) unless ``--no-learn``.
Nothing here calls a speech service or spends money. The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import voice_pace
import zh_text

MINIMUM_PYTHON = (3, 9)

SILENCE_DB = -40
SILENCE_MIN = 0.10
PAD = 0.05            # quiet kept at each end of a clip
MAX_SKIPPED_MARKS = 12  # marks in a row that may have no silence of their own
# Costs the matching weighs: a mark left without a silence, against a silence that fits badly.
SKIP_COMMA = 0.5
SKIP_SENTENCE_END = 1.0
MATCH_WEIGHT = 0.8
# A long silence is almost always a mark, so leaving one unused costs by how long it is.
BREATH = 0.3
LOOSE_SILENCE = 2.0
CAPTION_CHARS = 18
KEEP_MARKS = "？！?!"
MARK = re.compile("([" + re.escape(zh_text.MINOR_MARKS + zh_text.MAJOR_MARKS) + r"\n]+)")


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


# ------------------------------------------------------------------ audio
def silences(path: Path) -> Tuple[float, List[Tuple[float, float]]]:
    """Clip length and every silent stretch in it."""
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
    starts = [max(0.0, float(x)) for x in re.findall(r"silence_start: (-?[\d.]+)", log)]
    ends = [float(x) for x in re.findall(r"silence_end: ([\d.]+)", log)]
    spans = [(start, ends[i] if i < len(ends) else total) for i, start in enumerate(starts)]
    return total, [(a, min(b, total)) for a, b in spans if b > a]


def voiced_span(total: float, spans: Sequence[Tuple[float, float]]) -> Tuple[float, float, List[Tuple[float, float]]]:
    """First sound, last sound, and the silences strictly between them."""
    first, last = 0.0, total
    inner = list(spans)
    if inner and inner[0][0] < 0.05:
        first = inner.pop(0)[1]
    if inner and inner[-1][1] > total - 0.06:
        last = inner.pop()[0]
    return first, last, [(a, b) for a, b in inner if a >= first and b <= last]


# -------------------------------------------------------------- alignment
def split_clauses(text: str) -> List[Dict[str, Any]]:
    """The stretches said in one breath, each with the mark that follows it."""
    pieces = MARK.split(zh_text.speakable(text).strip())
    clauses: List[Dict[str, Any]] = []
    for index in range(0, len(pieces), 2):
        words = pieces[index].strip()
        mark = pieces[index + 1] if index + 1 < len(pieces) else ""
        if not zh_text.spoken_len(words):
            if clauses and mark:
                clauses[-1]["mark"] += mark
            continue
        clauses.append({"text": words, "mark": mark.replace("\n", ""), "chars": zh_text.spoken_len(words),
                        "major": bool(mark) and any(ch in zh_text.MAJOR_MARKS + "\n" for ch in mark)})
    if clauses:
        clauses[-1]["major"] = True
    return clauses


def _match(marks: Sequence[Tuple[int, bool]], quiet: Sequence[int], spoken_at: Sequence[float],
           lengths: Sequence[float], start: Tuple[int, float], finish: Tuple[int, float],
           slack: float, share: float, skip_costs: Tuple[float, float], gain: float,
           longest_run: int) -> Dict[int, int]:
    """Place marks on silences between two fixed points, in order.

    ``marks`` are (spoken characters up to the mark, is a sentence end); ``quiet`` lists the
    silences that may be used; ``start`` and ``finish`` are (characters, speaking time) at the two
    ends. Returns {position in marks: silence index}. Each mark is judged against the mark placed
    before it, at the pace of this stretch; a mark may go unplaced, a silence may go unused, and a
    long silence left unused costs by its length."""
    span_chars = finish[0] - start[0]
    span_time = finish[1] - start[1]
    if not marks or not quiet or span_chars <= 0 or span_time <= 0:
        return {}
    rate = span_time / span_chars
    skip = [skip_costs[1] if major else skip_costs[0] for _chars, major in marks]
    skipped_to = [0.0]
    for value in skip:
        skipped_to.append(skipped_to[-1] + value)
    loose_to = [0.0]
    for k in quiet:
        loose_to.append(loose_to[-1] + LOOSE_SILENCE * max(0.0, lengths[k] - BREATH))

    def fit(chars_from: int, at_from: float, chars_here: int, at_here: float) -> Optional[float]:
        expected = rate * (chars_here - chars_from)
        allowed = slack + share * expected
        away = abs((at_here - at_from) - expected)
        return MATCH_WEIGHT * away / allowed if away <= allowed else None

    count, room = len(marks), len(quiet)
    cost: List[List[Optional[float]]] = [[None] * room for _ in range(count)]
    came: List[List[Tuple[int, int]]] = [[(-1, -1)] * room for _ in range(count)]
    for i in range(count):
        for q in range(room):
            k = quiet[q]
            bonus = min(lengths[k], 1.0) * gain
            best: Optional[float] = None
            origin = (-1, -1)
            if i <= longest_run:
                away = fit(start[0], start[1], marks[i][0], spoken_at[k])
                if away is not None:
                    best = skipped_to[i] + loose_to[q] + away - bonus
            for i2 in range(max(0, i - longest_run - 1), i):
                for q2 in range(q):
                    before = cost[i2][q2]
                    if before is None:
                        continue
                    away = fit(marks[i2][0], spoken_at[quiet[q2]], marks[i][0], spoken_at[k])
                    if away is None:
                        continue
                    total = before + (skipped_to[i] - skipped_to[i2 + 1]) + (loose_to[q] - loose_to[q2 + 1]) + away - bonus
                    if best is None or total < best:
                        best, origin = total, (i2, q2)
            cost[i][q], came[i][q] = best, origin
    best_end: Optional[float] = skipped_to[count] + loose_to[room] if count <= longest_run + 1 else None
    node = (-1, -1)
    for i in range(count):
        if count - 1 - i > longest_run:
            continue
        for q in range(room):
            before = cost[i][q]
            if before is None:
                continue
            away = fit(marks[i][0], spoken_at[quiet[q]], finish[0], finish[1])
            if away is None:
                continue
            total = before + (skipped_to[count] - skipped_to[i + 1]) + (loose_to[room] - loose_to[q + 1]) + away
            if best_end is None or total < best_end:
                best_end, node = total, (i, q)
    placed: Dict[int, int] = {}
    while node != (-1, -1):
        placed[node[0]] = quiet[node[1]]
        node = came[node[0]][node[1]]
    return placed


def align(clauses: List[Dict[str, Any]], first: float, last: float,
          inner: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    """Start and end of each clause inside the clip.

    Marks and silences are compared on a clock that only runs while the voice is sounding, so a
    silence's place does not depend on which other silences were matched. It is done in two rounds.
    Sentence ends go first, onto the long silences, with a loose idea of pace: that fixes a point
    every few seconds. Commas are then placed inside each stretch at that stretch's own pace. A
    voice does not hold one speed for a minute and a half, and placing every mark by the clip's
    average pace lets the error pile up until marks land one silence late."""
    count = len(clauses)
    if not count:
        return []
    lengths = [b - a for a, b in inner]
    spoken_at = [inner[k][0] - first - sum(lengths[:k]) for k in range(len(inner))]
    speaking = max(0.01, (last - first) - sum(lengths))
    chars_to: List[int] = []  # spoken characters up to the mark after clause i
    running = 0
    for clause in clauses:
        running += clause["chars"]
        chars_to.append(running)
    marks = count - 1
    matched: Dict[int, int] = {}

    enders = [i for i in range(marks) if clauses[i]["major"]]
    placed = _match([(chars_to[i], True) for i in enders], list(range(len(inner))), spoken_at, lengths,
                    (0, 0.0), (running, speaking), 0.6, 0.35, (SKIP_SENTENCE_END, SKIP_SENTENCE_END), 0.5, 4)
    for position, k in placed.items():
        matched[enders[position]] = k

    fixed = sorted(matched)
    edges = [(-1, -1)] + [(i, matched[i]) for i in fixed] + [(marks, len(inner))]
    for (mark_a, quiet_a), (mark_b, quiet_b) in zip(edges, edges[1:]):
        between = [i for i in range(mark_a + 1, mark_b)]
        room = list(range(quiet_a + 1, quiet_b))
        if not between or not room:
            continue
        start = (chars_to[mark_a], spoken_at[quiet_a]) if mark_a >= 0 else (0, 0.0)
        finish = (chars_to[mark_b], spoken_at[quiet_b]) if mark_b < marks else (running, speaking)
        placed = _match([(chars_to[i], clauses[i]["major"]) for i in between], room, spoken_at, lengths,
                        start, finish, 0.35, 0.25, (SKIP_COMMA, SKIP_COMMA + 0.1), 0.3, MAX_SKIPPED_MARKS)
        for position, k in placed.items():
            matched[between[position]] = k

    def clock(spoken: float) -> float:
        """Turn speaking time back into clip time by adding the silences that came before."""
        return first + spoken + sum(length for length, at in zip(lengths, spoken_at) if at < spoken - 1e-6)

    anchors = [(-1, 0, 0.0)] + [(i, chars_to[i], spoken_at[matched[i]]) for i in sorted(matched)] + [(marks, running, speaking)]
    times: List[Tuple[float, float]] = []
    start_at = first
    for index in range(count):
        if index == count - 1:
            end = nxt = last
        elif index in matched:
            end, nxt = inner[matched[index]]
        else:
            before = max((a for a in anchors if a[0] < index), key=lambda a: a[0])
            after = min((a for a in anchors if a[0] > index), key=lambda a: a[0])
            portion = (chars_to[index] - before[1]) / max(1, after[1] - before[1])
            end = nxt = clock(before[2] + portion * (after[2] - before[2]))
        end = max(end, start_at)
        times.append((round(start_at, 3), round(end, 3)))
        start_at = max(nxt, end)
    return times


# Where a long clause may be cut into caption lines. There is no word list to consult, so the cut
# goes by the characters around it. A particle belongs to the word before it and never starts a
# line; a character that leads into what follows (不, 就, 把, 你 ...) never ends one, and is where a
# line would rather start; a number or a Latin word is never cut. That keeps 「不能」 or 「你会」 on
# one line far more often than cutting at the exact middle does. Clauses this long are rare: a
# line holds CAPTION_CHARS characters, and narration written in short sentences seldom runs over.
NEVER_FIRST = frozenset("的地得了着过们吗呢吧啊呀么")
LEADERS = frozenset("把被让给对从向往和与或而但却就才也都还又再不没别因所虽既这那哪你我他她它谁先连很最更太")
NEVER_LAST = LEADERS | frozenset("是有要会能可在为比像")
LATIN = re.compile(r"[0-9A-Za-z.%]")


def break_points(text: str, pieces: int, limit: int) -> List[int]:
    """Indexes at which to cut `text` into `pieces` lines of about equal length."""
    cuts: List[int] = []
    for n in range(1, pieces):
        ideal = len(text) * n / pieces
        last = cuts[-1] if cuts else 0

        def cost(index: int) -> float:
            penalty = abs(index - ideal)
            if text[index] in NEVER_FIRST or text[index - 1] in NEVER_LAST or (LATIN.match(text[index - 1]) and LATIN.match(text[index])):
                penalty += 100.0
            if text[index] in LEADERS:
                penalty -= 4.0
            longest = max(index - last, (len(text) - index) / (pieces - n))
            return penalty + 10.0 * max(0.0, longest - limit)

        choices = range(last + 2, len(text) - 1)
        if not choices:
            break
        cuts.append(min(choices, key=cost))
    return cuts


def caption_lines(clauses: List[Dict[str, Any]], times: Sequence[Tuple[float, float]], limit: int) -> List[Dict[str, Any]]:
    """Readable caption lines: one per clause, a long clause cut near its middle where a phrase starts."""
    lines: List[Dict[str, Any]] = []
    for clause, (start, end) in zip(clauses, times):
        text = clause["text"]
        keep = "".join(ch for ch in clause["mark"] if ch in KEEP_MARKS)[:1]
        parts = [text]
        if clause["chars"] > limit:
            edges = [0] + break_points(text, -(-clause["chars"] // limit), limit) + [len(text)]
            parts = [text[a:b] for a, b in zip(edges, edges[1:]) if text[a:b]]
        weights = [max(1, zh_text.spoken_len(p)) for p in parts]
        cursor = start
        for n, part in enumerate(parts):
            share = (end - start) * weights[n] / sum(weights)
            stop = end if n == len(parts) - 1 else cursor + share
            lines.append({"start": round(cursor, 3), "end": round(stop, 3),
                          "text": part + (keep if n == len(parts) - 1 else "")})
            cursor = stop
    return lines


def group_sentences(clauses: List[Dict[str, Any]], times: Sequence[Tuple[float, float]]) -> List[Dict[str, Any]]:
    sentences: List[Dict[str, Any]] = []
    text, start = "", None
    for clause, (a, b) in zip(clauses, times):
        if start is None:
            start = a
        text += clause["text"] + clause["mark"]
        if clause["major"]:
            sentences.append({"start": start, "end": b, "text": text})
            text, start = "", None
    return sentences


# ------------------------------------------------------------------ inputs
def clips_from_jobs(project: Path, wanted: Sequence[str]) -> List[Dict[str, Any]]:
    folder = project / ".short-drama" / "production" / "jobs"
    if not folder.is_dir():
        raise RuntimeError(f"项目里没有配音任务记录：{folder}")
    known: Dict[str, Dict[str, Any]] = {}
    stale: Dict[str, bool] = {}
    for path in folder.glob("*.json"):
        job = json.loads(path.read_text(encoding="utf-8"))
        if job.get("modality") != "tts" or not job.get("job_id") or not job.get("outputs"):
            continue
        known[job["job_id"]] = job
        # The production tool keeps one file per job and rewrites it when a line is reworded, so the
        # text in it is the latest wording, not necessarily the one on the recording. A recording
        # belongs to the wording whose fingerprint its run carries.
        made_with = set()
        runs = folder.parent / "runs" / path.stem
        for record in runs.glob("*.json") if runs.is_dir() else []:
            try:
                run = json.loads(record.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if run.get("status") == "succeeded" and run.get("fingerprint"):
                made_with.add(run["fingerprint"])
        stale[job["job_id"]] = bool(made_with) and bool(job.get("fingerprint")) and job["fingerprint"] not in made_with
    clips = []
    for job_id in wanted:
        job = known.get(job_id)
        if job is None:
            raise RuntimeError(f"没有这个配音任务：{job_id}。项目里有：{'、'.join(sorted(known)) or '（空）'}")
        audio = project / job["outputs"][0]
        if not audio.is_file():
            raise RuntimeError(f"任务 {job_id} 的配音还没生成：{audio}")
        if stale[job_id]:
            raise RuntimeError(f"任务 {job_id} 的稿子在配音之后又改过，还没重新配：现在的录音念的是旧稿，字幕会对不上。"
                               "重新配这一段，或者把稿子改回去。")
        parameters = job.get("parameters") or {}
        clips.append({"id": job_id, "audio": audio, "text": job.get("prompt") or "",
                      "voice_id": parameters.get("voice_id"), "speed": float(parameters.get("speed") or 1.0)})
    return clips


def clips_from_manifest(path: Path) -> List[Dict[str, Any]]:
    clips = []
    for n, item in enumerate(json.loads(path.read_text(encoding="utf-8")), 1):
        audio = Path(item["audio"])
        if not audio.is_absolute():
            audio = path.parent / audio
        if not audio.is_file():
            raise RuntimeError(f"找不到音频：{audio}")
        clips.append({"id": str(item.get("id") or n), "audio": audio, "text": item["text"],
                      "voice_id": item.get("voice_id"), "speed": float(item.get("speed") or 1.0)})
    return clips


# ------------------------------------------------------------------- build
def stamp(seconds: float) -> str:
    whole = int(seconds)
    return "%02d:%02d:%02d,%03d" % (whole // 3600, whole % 3600 // 60, whole % 60, int(round((seconds - whole) * 1000)) % 1000)


def join(clips: Sequence[Dict[str, Any]], gap: float, out: Path) -> None:
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    for clip in clips:
        args += ["-i", str(clip["audio"])]
    chain: List[str] = []
    order: List[str] = []
    for n, clip in enumerate(clips):
        chain.append(
            f"[{n}:a]atrim=start={clip['cut_in']:.3f}:end={clip['cut_out']:.3f},asetpts=N/SR/TB,"
            f"aresample=48000,aformat=sample_fmts=s16:channel_layouts=stereo[s{n}]"
        )
        order.append(f"[s{n}]")
        if n < len(clips) - 1 and gap > 0:
            chain.append(f"anullsrc=r=48000:cl=stereo:d={gap:.3f}[g{n}]")
            order.append(f"[g{n}]")
    chain.append("".join(order) + f"concat=n={len(order)}:v=0:a=1[out]")
    run = subprocess.run(args + ["-filter_complex", ";".join(chain), "-map", "[out]", "-map_metadata", "-1",
                                 "-c:a", "pcm_s16le", str(out)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if run.returncode != 0:
        tail = run.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(tail[-1] if tail else "ffmpeg failed")


def build(clips: List[Dict[str, Any]], out: Path, gap: float, limit: int) -> Dict[str, Any]:
    segments: List[Dict[str, Any]] = []
    captions: List[Dict[str, Any]] = []
    learned: Dict[str, List[voice_pace.Observation]] = {}
    cursor = 0.0
    for clip in clips:
        total, spans = silences(clip["audio"])
        first, last, inner = voiced_span(total, spans)
        clip["cut_in"], clip["cut_out"] = max(0.0, first - PAD), min(total, last + PAD)
        clauses = split_clauses(clip["text"])
        if not clauses:
            raise RuntimeError(f"{clip['id']} 没有要念的文字")
        times = align(clauses, first, last, inner)
        shift = cursor - clip["cut_in"]
        moved = [(round(a + shift, 3), round(b + shift, 3)) for a, b in times]
        length = clip["cut_out"] - clip["cut_in"]
        segments.append({
            "id": clip["id"], "start": round(cursor, 3), "end": round(cursor + length, 3),
            "voice_s": round(last - first, 3), "chars": sum(c["chars"] for c in clauses),
            "text": clip["text"].strip(), "audio": clip["audio"].name,
            "sentences": group_sentences(clauses, moved),
        })
        captions += caption_lines(clauses, moved, limit)
        if clip.get("voice_id"):
            seen = voice_pace.observe(clip["text"], {"net": last - first, "inner": sum(b - a for a, b in inner)}, clip["speed"])
            if seen:
                learned.setdefault(clip["voice_id"], []).append(seen)
        cursor += length + gap
    total_s = round(cursor - gap, 3)
    out.mkdir(parents=True, exist_ok=True)
    join(clips, gap, out / "旁白.wav")
    voices = sorted({clip["voice_id"] for clip in clips if clip.get("voice_id")})
    table = {
        "version": 1, "total_s": total_s, "gap_s": gap,
        "voice_id": voices[0] if len(voices) == 1 else voices, "speed": clips[0]["speed"],
        "chars": sum(s["chars"] for s in segments), "segments": segments, "captions": captions,
    }
    return {"table": table, "learned": learned}


def cmd_build(args: argparse.Namespace) -> None:
    if args.manifest:
        clips = clips_from_manifest(args.manifest)
    elif args.project and args.jobs:
        clips = clips_from_jobs(args.project, [j.strip() for j in args.jobs.split(",") if j.strip()])
    else:
        emit({"ok": False, "error": "给 --project 和 --jobs，或者 --manifest"}, 2)
    if not clips:
        emit({"ok": False, "error": "一段配音都没有"}, 2)
    result = build(clips, args.out, args.gap, args.caption_chars)
    table = result["table"]
    target: Optional[float] = args.target_seconds
    if target is None and args.reference:
        profile = args.reference / "source.profile.json"
        if profile.is_file():
            target = json.loads(profile.read_text(encoding="utf-8")).get("duration_s")
    if target:
        table["target_s"] = round(float(target), 2)
    (args.out / "时间表.json").write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    srt = "".join(f"{n}\n{stamp(line['start'])} --> {stamp(line['end'])}\n{line['text']}\n\n"
                  for n, line in enumerate(table["captions"], 1))
    (args.out / "字幕.srt").write_text(srt, encoding="utf-8")
    if args.script_js:
        args.script_js.parent.mkdir(parents=True, exist_ok=True)
        args.script_js.write_text("window.TIMETABLE = " + json.dumps(table, ensure_ascii=False) + ";\n", encoding="utf-8")
    added = {}
    if not args.no_learn:
        added = {voice: voice_pace.remember(voice, rows, args.store, args.out.parent.name)
                 for voice, rows in result["learned"].items()}
    minutes, seconds = divmod(int(round(table["total_s"])), 60)
    line = f"旁白拼好了：{len(table['segments'])} 段，全长 {minutes} 分 {seconds:02d} 秒，{len(table['captions'])} 条字幕。"
    payload: Dict[str, Any] = {"ok": True, "narration": str(args.out / "旁白.wav"), "timetable": str(args.out / "时间表.json"),
                               "captions": str(args.out / "字幕.srt"), "total_s": table["total_s"],
                               "segments": len(table["segments"]), "learned": added}
    if target:
        off = table["total_s"] / float(target) - 1.0
        payload.update({"target_s": round(float(target), 2), "off_target": round(off, 3), "within_tenth": abs(off) <= 0.10})
        line += f"原片 {int(float(target)) // 60} 分 {int(float(target)) % 60:02d} 秒，差 {off:+.0%}。"
        if abs(off) > 0.10:
            line += "差出一成以上：把实际时长告诉创作者，由他定是接受，还是改稿重配这几段。"
    print(line)
    emit(payload)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="把各段配音拼成一条旁白，算出每句话的时间。")
    parser.add_argument("--store", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    build_cmd = sub.add_parser("build", help="拼旁白、出时间表和字幕")
    build_cmd.add_argument("--project", type=Path, help="短剧项目目录")
    build_cmd.add_argument("--jobs", help="配音任务号，按片子里的顺序，逗号隔开")
    build_cmd.add_argument("--manifest", type=Path, help='JSON 列表：[{"id","audio","text","voice_id","speed"}]')
    build_cmd.add_argument("--out", type=Path, required=True, help="输出目录，一般是 <项目>/制作")
    build_cmd.add_argument("--gap", type=float, default=0.3, help="段与段之间留的秒数")
    build_cmd.add_argument("--caption-chars", type=int, default=CAPTION_CHARS, help="一条字幕最多几个字")
    build_cmd.add_argument("--reference", type=Path, help="参考片目录：用来和原片时长比")
    build_cmd.add_argument("--target-seconds", type=float)
    build_cmd.add_argument("--script-js", type=Path, help="另存一份给动画页面直接读的 timetable.js")
    build_cmd.add_argument("--no-learn", action="store_true", help="不把这次的实测语速记下来")
    args = parser.parse_args()

    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            emit({"ok": False, "error": f"{tool} not found; install ffmpeg first"}, 2)
    try:
        cmd_build(args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
