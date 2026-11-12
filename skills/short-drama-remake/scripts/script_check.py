#!/usr/bin/env python3
"""Measure a new narration script against the reference's own lines, before any voice is paid for.

    script_check.py profile <参考片目录>
    script_check.py budget  <参考片目录> --cuts 0,29.6,65.1 [--voice-id <id> --speed 1.1]
    script_check.py check   <稿子> --reference <参考片目录> [--voice-id <id> --speed 1.1
                            --audition 试听.mp3 --audition-text "试听念的那一句"]

``profile`` reads the reference's recognised lines (``transcript.json`` from ``transcribe.py``) and
writes ``source.tone.json``: how long its lines are, how often it hedges, asserts and says "你".
``budget`` turns the reference's sections into a character budget per section for the chosen voice.
``check`` compares a script (《剧本.md》 or a plain text file, one paragraph per section) with the
reference and prints which craft checks pass:

- 时长对得上: how long the chosen voice will take, estimated by ``voice_pace.py``, against the
  reference's length. Without a voice it falls back to the reference's own speech rate and says so.
- 没有照搬: no run of ten or more characters shared with the reference's lines.
- 口气: hedging words no denser than the reference's, asserting words and "你" not much sparser.
- 句子、开头: advice only.

Everything is counted relative to this reference; nothing here is a fixed quota, and the checks are
craft checks for the creator's review, not a release gate. Counting words cannot tell a good line
from a bad one: it only catches a script that has drifted away from how the reference talks.
The last stdout line is one JSON object.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, NoReturn, Optional, Sequence, Tuple

import voice_pace
import zh_text

MINIMUM_PYTHON = (3, 9)

# Words that take a claim back as it is made. Counted against the reference, never on their own.
HEDGES = (
    "有可能", "可能", "也许", "或许", "大概", "恐怕", "未必", "不一定", "不见得", "不好说", "说不准",
    "某种程度", "一定程度", "相对而言", "相对来说", "一般来说", "一般而言", "通常", "往往", "倾向于",
    "似乎", "看起来", "或多或少", "有待", "尚未", "暂时", "至少", "不代表", "不等于", "并不意味",
    "不能说明", "不能确定", "难以确定", "无法确定", "没有证据", "仅供参考", "不构成", "需要注意的是",
    "值得注意的是", "谨慎", "不妨", "不是保证", "不保证", "还要再看", "还需要", "下结论", "不一定是",
    # Withholding a judgement is hedging too: "不能直接……" "别急着……" "单凭这一点……"
    "不能直接", "不能把", "不能只", "不能因为", "不能就", "不能说", "还不能", "别急着", "先别急",
    "只凭", "单凭", "不足以", "有待观察", "再观察", "不好判断", "没法确定", "说不定",
)
# Words that commit to a claim, or point at the one thing that matters.
ASSERTS = (
    "大错特错", "说白了", "一句话", "告诉你", "就是", "一定", "必须", "肯定", "绝对", "根本", "才是",
    "真相", "记住", "千万", "错了", "本质", "关键", "核心", "只有", "永远", "从来", "毫无疑问", "显然",
    "当然", "其实", "全都", "所有", "任何", "最重要", "唯一", "注定", "必然", "别再", "不要再",
    "答案是", "很简单", "真正", "到底", "偏偏", "恰恰",
)
# Stock lines any video may carry; sharing them with the reference is not copying.
BOILERPLATE = (
    "投资有风险入市需谨慎", "不构成投资建议", "不构成任何投资建议", "仅供参考", "点赞关注", "点赞收藏",
    "我们下期再见", "欢迎在评论区",
)
COPY_FAIL = 10
COPY_NOTE = 7
LONG_SENTENCE = 30
TOLERANCE = 0.10
# Estimated from an audition line alone, narration has tended to run longer than the estimate.
TOLERANCE_OVER_UNHEARD = 0.05
# Past this the film will be visibly longer than the reference: still allowed with a voice that has
# been measured before, but the script is told how much to cut to land on it.
LEAN_OVER = 0.05
# A screenplay names its scenes like this (the write skill's format): "## EP001-SC003 内 · 地点 · 时间".
SCENE_HEADING = re.compile(r"^## \S+ (?:内外|内|外) · ")
VO_LINE = re.compile(r"^\[(?:VO|OS)\]\s*[^：:]{0,16}[：:]\s*(.+)$")
SPOKEN_LINE = re.compile(r"^([^\s\[\]#>|：:（(\-*]{1,12})(?:（[^）]*）|\([^)]*\))?[：:]\s*(.+)$")
ASIDE = re.compile(r"（[^）]*）|\([^)]*\)")
PAUSE_MARK = re.compile(r"<#(\d+(?:\.\d+)?)#>")


def emit(payload: Dict[str, Any], code: int = 0) -> NoReturn:
    print(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


# ------------------------------------------------------------------ reading
def read_reference(folder: Path) -> Dict[str, Any]:
    """The reference's recognised lines and measured numbers; either may be missing."""
    lines: List[Dict[str, Any]] = []
    transcript = folder / "transcript.json"
    transcribed = transcript.is_file()
    if transcribed:
        data = json.loads(transcript.read_text(encoding="utf-8"))
        for segment in data.get("segments", []):
            text = zh_text.to_simplified(str(segment.get("text", ""))).strip()
            if segment.get("suspect") or not zh_text.spoken_len(text):
                continue
            lines.append({"start": float(segment.get("start", 0)), "end": float(segment.get("end", 0)), "text": text})
    measured: Dict[str, Any] = {}
    profile = folder / "source.profile.json"
    if profile.is_file():
        measured = json.loads(profile.read_text(encoding="utf-8"))
    return {"folder": folder, "lines": lines, "measured": measured, "transcribed": transcribed}


def spoken_text(raw: str) -> str:
    """A line as the voice says it: stage asides and pause marks taken out."""
    return PAUSE_MARK.sub("", ASIDE.sub("", raw)).strip()


def is_screenplay(rows: Sequence[str]) -> bool:
    """A screenplay keeps only its spoken lines, so one with scenes and no speech has nothing to
    read aloud; a plain narration draft is read whole. A scene heading in the write skill's form
    settles it, and so does a line tagged [VO] or [OS]. A heading above a line shaped like
    "几个字：……" does not: narration drafts are full of such lines ("记住一句话：……"), and reading
    them as dialogue would throw away everything before each colon."""
    return any(SCENE_HEADING.match(row) for row in rows) or any(VO_LINE.match(row.strip()) for row in rows)


def read_script(path: Path, only_voice_over: bool = False) -> List[Dict[str, str]]:
    """Sections of spoken text. A screenplay is cut at its `## ` scene headings and only its
    voice-over and dialogue lines are kept; a plain text file is cut at blank lines."""
    raw = path.read_text(encoding="utf-8")
    rows = raw.splitlines()
    screenplay = is_screenplay(rows)
    sections: List[Dict[str, str]] = []
    if screenplay:
        current: Optional[Dict[str, str]] = None
        for row in rows:
            line = row.strip()
            if line.startswith("## "):
                current = {"id": line[3:].split()[0], "text": ""}
                sections.append(current)
                continue
            if current is None or not line:
                continue
            voiced = VO_LINE.match(line)
            said = voiced.group(1) if voiced else None
            if said is None and not only_voice_over and not line.startswith("["):
                spoken = SPOKEN_LINE.match(line)
                said = spoken.group(2) if spoken else None
            if said:
                current["text"] += spoken_text(said) + "\n"
        return [s for s in sections if zh_text.spoken_len(s["text"])]
    blocks = re.split(r"\n\s*\n", raw)
    for block in blocks:
        kept = "\n".join(spoken_text(row) for row in block.splitlines() if not row.lstrip().startswith("#"))
        if zh_text.spoken_len(kept):
            sections.append({"id": str(len(sections) + 1), "text": kept.strip() + "\n"})
    return sections


def explicit_pauses(path: Path) -> float:
    return sum(float(x) for x in PAUSE_MARK.findall(path.read_text(encoding="utf-8")))


# -------------------------------------------------------------------- tone
def count_words(text: str, words: Sequence[str]) -> Tuple[int, Dict[str, int]]:
    pattern = re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)))
    found: Dict[str, int] = {}
    for hit in pattern.findall(text):
        found[hit] = found.get(hit, 0) + 1
    return sum(found.values()), found


def top(found: Dict[str, int], limit: int = 6) -> str:
    ranked = sorted(found.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return "、".join(f"{word}×{count}" if count > 1 else word for word, count in ranked)


def percentile(values: Sequence[int], share: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(math.ceil(share * len(ordered))) - 1)]


def without_boilerplate(text: str) -> str:
    """The text with stock lines taken out: a required closing notice is not the script hedging."""
    for phrase in BOILERPLATE:
        text = re.sub(r"[\s，,、。；;]*".join(re.escape(letter) for letter in phrase), "", text)
    return text


def tone(text: str) -> Dict[str, Any]:
    simple = zh_text.to_simplified(text)
    chars = zh_text.spoken_len(simple)
    own = without_boilerplate(simple)
    hedges, hedge_words = count_words(own, HEDGES)
    # 不一定 holds 一定 and 不能确定 holds nothing of the kind: a hedge is taken out before the
    # words that commit to a claim are counted, or it would be counted as both.
    hedging = re.compile("|".join(re.escape(word) for word in sorted(HEDGES, key=len, reverse=True)))
    asserts, assert_words = count_words(hedging.sub("＿", own), ASSERTS)
    you = simple.count("你")
    per = (lambda n: round(100.0 * n / chars, 2)) if chars else (lambda n: 0.0)
    return {
        "chars": chars,
        "hedges": hedges, "hedges_per_100": per(hedges), "hedge_words": hedge_words,
        "asserts": asserts, "asserts_per_100": per(asserts), "assert_words": assert_words,
        "you": you, "you_per_100": per(you),
    }


def reference_tone(reference: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    lines = reference["lines"]
    if not lines:
        return None
    result = tone("\n".join(line["text"] for line in lines))
    lengths = [zh_text.spoken_len(line["text"]) for line in lines]
    talking = sum(max(0.0, line["end"] - line["start"]) for line in lines)
    result.update({
        "lines": len(lines),
        "line_chars_median": percentile(lengths, 0.5),
        "line_chars_p90": percentile(lengths, 0.9),
        "speech_seconds": round(talking, 2),
        "chars_per_second": round(result["chars"] / talking, 2) if talking else None,
        "opening": [{"end": line["end"], "text": line["text"]} for line in lines[:3]],
    })
    return result


# -------------------------------------------------------------------- copy
def copied_runs(new_text: str, reference_text: str, shortest: int = COPY_NOTE) -> List[str]:
    """Stretches of the new script, `shortest` characters or longer, that also occur in the reference."""
    new, ref = zh_text.spoken(new_text), zh_text.spoken(reference_text)
    if len(new) < shortest or len(ref) < shortest:
        return []
    grams = {ref[i:i + shortest] for i in range(len(ref) - shortest + 1)}
    runs: List[str] = []
    i = 0
    while i <= len(new) - shortest:
        if new[i:i + shortest] not in grams:
            i += 1
            continue
        end = i + shortest
        while end < len(new) and new[i:end + 1] in ref:
            end += 1
        runs.append(new[i:end])
        i = end
    skip = [zh_text.spoken(phrase) for phrase in BOILERPLATE]
    return [run for run in runs if not any(run in phrase or phrase in run and len(run) - len(phrase) < 4 for phrase in skip)]


# ------------------------------------------------------------------ checks
def clock(seconds: float) -> str:
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes} 分 {rest:02d} 秒" if minutes else f"{rest} 秒"


def pace_for(args: argparse.Namespace, sections: Sequence[str]) -> Optional[Dict[str, Any]]:
    if not args.voice_id:
        return None
    extra: List[voice_pace.Observation] = []
    if args.audition:
        if not args.audition_text:
            emit({"ok": False, "error": "给了 --audition 就要同时给 --audition-text（试听念的那一句）"}, 2)
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            emit({"ok": False, "error": "ffmpeg not found; install ffmpeg first"}, 2)
        clip = voice_pace.measure_clip(args.audition)
        seen = voice_pace.observe(args.audition_text, clip,
                                  args.speed if args.audition_speed is None else args.audition_speed)
        if seen:
            extra.append(seen)
    return voice_pace.estimate(list(sections), args.voice_id, args.speed, extra, args.gap, args.store)


def run_checks(sections: List[Dict[str, str]], reference: Dict[str, Any], pace: Optional[Dict[str, Any]],
               target: Optional[float], pauses: float = 0.0) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name: str, level: str, detail: str) -> None:
        checks.append({"check": name, "level": level, "detail": detail})

    text = "".join(section["text"] for section in sections)
    new = tone(text)
    ref = reference_tone(reference)
    measured = reference["measured"]
    if target is None and measured.get("duration_s"):
        target = float(measured["duration_s"])

    # 1. length
    estimate: Optional[float] = None
    if pace:
        estimate = pace["total_s"] + pauses
        basis = {"general": "通用参数（这个音色还没量过，试听后再跑一次）", "audition": "试听", "history": "这个音色以前配过的片子"}[pace["basis"]]
    elif ref and ref.get("chars_per_second"):
        estimate = new["chars"] / ref["chars_per_second"] + pauses
        basis = "原片的语速（还没选音色；选定后带 --voice-id 再跑一次，音色之间能差两三成）"
    if estimate and target:
        off = estimate / target - 1.0
        detail = f"预计 {clock(estimate)}，目标 {clock(target)}（{off:+.0%}），{new['chars']} 字。依据：{basis}。"
        over = TOLERANCE if (pace and pace["basis"] == "history") else TOLERANCE_OVER_UNHEARD
        change = int(round(new["chars"] * (1 - target / estimate)))
        if -TOLERANCE <= off <= min(over, LEAN_OVER):
            add("时长对得上", "pass" if pace else "note", detail)
        elif pace and LEAN_OVER < off <= over:
            add("时长对得上", "note", detail + f"偏长：成片会比原片长约 {estimate - target:.0f} 秒，删掉约 {change} 字就贴上原片。"
                "创作者不介意长这一点可以不删，但要告诉他。")
        else:
            advice = f"删掉约 {change} 字" if change > 0 else f"补上约 {-change} 字的内容"
            if pace:
                needed = round(pace["speed"] * estimate / target, 2)
                if 1.0 <= needed <= 1.25 and abs(needed - pace["speed"]) <= 0.15:
                    advice += f"；或者把语速从 {pace['speed']} 调到 {needed}，要让创作者重新听一句再定"
            if pace and pace["basis"] != "history" and off > 0:
                advice += "。这个音色还没配过长稿，成片常比估的再长一些，宁可多删一点"
            add("时长对得上", "fail" if pace else "note", detail + advice + "。")
    elif not target:
        add("时长对得上", "note", "没有原片时长（先跑 media_profile.py），也没有给 --target-seconds，这一项没法比。")
    else:
        add("时长对得上", "note", "还没选音色，原片也没有识别出来的台词可以参照语速，时长估不了。选定音色后带 --voice-id 再跑一次。")

    # 2. copied lines
    if ref:
        reference_text = "\n".join(line["text"] for line in reference["lines"])
        runs = copied_runs(text, reference_text)
        long_runs = [run for run in runs if len(run) >= COPY_FAIL]
        if long_runs:
            add("没有照搬", "fail", "和原片台词连着相同 10 个字以上：" + "；".join(f"「{run}」" for run in long_runs[:5]) + "。换个说法重写。")
        elif runs:
            add("没有照搬", "note", "和原片台词有 7 到 9 个字连着相同：" + "；".join(f"「{run}」" for run in runs[:5]) + "。看一眼是不是巧合。")
        else:
            add("没有照搬", "pass", "没有和原片台词连着相同 7 个字以上的地方。")

    # 3. tone, against the reference
    if ref and new["chars"]:
        allowed = max(int(math.ceil((ref["hedges_per_100"] * 1.5 + 0.2) * new["chars"] / 100.0)), ref["hedges"] + 1, 2)
        detail = (f"打折扣的词：新稿 {new['hedges']} 处（每百字 {new['hedges_per_100']}），"
                  f"原片 {ref['hedges']} 处（每百字 {ref['hedges_per_100']}）。")
        if new["hedges"] <= allowed:
            add("不句句打折扣", "pass", detail)
        else:
            add("不句句打折扣", "fail", detail + f"新稿里最多的是：{top(new['hedge_words'])}。原片敢下的判断，新稿也下；提醒放到片尾说一次。")
        if ref["asserts_per_100"] >= 0.5:
            needed = int(math.floor(ref["asserts_per_100"] * 0.5 * new["chars"] / 100.0))
            detail = (f"下判断的词：新稿 {new['asserts']} 处（每百字 {new['asserts_per_100']}），"
                      f"原片 {ref['asserts']} 处（每百字 {ref['asserts_per_100']}），原片常用：{top(ref['assert_words'])}。")
            if new["asserts"] >= needed:
                add("敢下判断", "pass", detail)
            else:
                add("敢下判断", "fail", detail + "新稿把话说软了：把「可以看到」「值得关注」这类说法换成直接的结论。")
        else:
            add("敢下判断", "pass", f"原片很少用下判断的词（每百字 {ref['asserts_per_100']}），这一项不比。")
        if ref["you_per_100"] >= 0.8:
            needed = int(math.floor(ref["you_per_100"] * 0.5 * new["chars"] / 100.0))
            detail = f"「你」：新稿 {new['you']} 次（每百字 {new['you_per_100']}），原片 {ref['you']} 次（每百字 {ref['you_per_100']}）。"
            if new["you"] >= needed:
                add("对着观众说", "pass", detail)
            else:
                add("对着观众说", "fail", detail + "原片一直在对「你」说话，新稿像在念讲义：把说明句改成对观众说的话。")
        else:
            add("对着观众说", "pass", f"原片很少对「你」说话（每百字 {ref['you_per_100']}），这一项不比。")
    elif not ref and reference.get("transcribed"):
        add("口气", "note", "原片没有人说话（识别结果里没有台词），口气和照搬不用和原片比。")
    elif not ref:
        add("口气", "note", "参考片目录里没有 transcript.json，口气和照搬都没法和原片比。口播多的片子先跑 transcribe.py。")

    # 4. advice: sentence length and the opening line
    lengths = [zh_text.spoken_len(sentence) for sentence in zh_text.sentences(text)]
    if lengths:
        long_share = sum(1 for n in lengths if n > LONG_SENTENCE) / len(lengths)
        if long_share > 0.2:
            add("句子不拖", "note", f"{long_share:.0%} 的句子超过 {LONG_SENTENCE} 个字（最长 {max(lengths)} 字）。口播一口气说不完的句子，拆成两句。")
        else:
            add("句子不拖", "pass", f"超过 {LONG_SENTENCE} 个字的句子占 {long_share:.0%}（最长 {max(lengths)} 字）。")
    first = zh_text.sentences(sections[0]["text"])[0] if sections else ""
    if first and ref and len(ref["opening"]) >= 2:
        rate = pace["typical_chars_per_second"] if pace else (ref.get("chars_per_second") or 4.5)
        seconds = zh_text.spoken_len(first) / rate
        limit = max(4.0, ref["opening"][1]["end"] + 1.0)
        if seconds > limit:
            add("开头够快", "note", f"第一句要念约 {seconds:.1f} 秒；原片头两句 {ref['opening'][1]['end']:.1f} 秒就说完了"
                                f"（「{ref['opening'][0]['text']}」「{ref['opening'][1]['text']}」）。")
        else:
            add("开头够快", "pass", f"第一句念完约 {seconds:.1f} 秒；原片头两句用了 {ref['opening'][1]['end']:.1f} 秒。")

    return {
        "checks": checks,
        "failed": [c["check"] for c in checks if c["level"] == "fail"],
        "notes": [c["check"] for c in checks if c["level"] == "note"],
        "script": {**{k: new[k] for k in ("chars", "hedges", "hedges_per_100", "asserts", "asserts_per_100", "you", "you_per_100")},
                   "sections": [{"id": s["id"], "chars": zh_text.spoken_len(s["text"])} for s in sections]},
        "reference": {k: ref[k] for k in ("chars", "hedges_per_100", "asserts_per_100", "you_per_100", "chars_per_second", "line_chars_median")} if ref else None,
        "estimate_s": round(estimate, 1) if estimate else None,
        "target_s": round(target, 1) if target else None,
        "pace": pace,
    }


# ---------------------------------------------------------------- commands
def cmd_profile(args: argparse.Namespace) -> None:
    reference = read_reference(args.reference)
    ref = reference_tone(reference)
    if not ref and reference["transcribed"]:
        print("识别结果里没有台词。画面上也没有字幕、没有人张嘴，就是原片没人说话：口气不用量，新稿也不用比口气和照搬。"
              "拆解报告的「台词来源」写「原片没有人说话」。")
        emit({"ok": True, "no_speech": True})
    if not ref:
        emit({"ok": False, "need_transcript": True,
              "error": "参考片目录里没有 transcript.json。先跑 transcribe.py；没装识别就在报告里写「没有台词识别」。"}, 1)
    out = args.reference / "source.tone.json"
    out.write_text(json.dumps(ref, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"原片台词 {ref['chars']} 字，{ref['lines']} 行，每行中位 {ref['line_chars_median']} 字，每秒 {ref['chars_per_second']} 字。")
    print(f"打折扣的词每百字 {ref['hedges_per_100']}（{top(ref['hedge_words']) or '没有'}）；"
          f"下判断的词每百字 {ref['asserts_per_100']}（{top(ref['assert_words']) or '没有'}）；「你」每百字 {ref['you_per_100']}。")
    emit({"ok": True, "tone": str(out), **{k: v for k, v in ref.items() if not k.endswith("_words")}})


def cmd_budget(args: argparse.Namespace) -> None:
    reference = read_reference(args.reference)
    lines, measured = reference["lines"], reference["measured"]
    try:
        cuts = sorted({float(x) for x in args.cuts.split(",") if x.strip()})
    except ValueError:
        emit({"ok": False, "error": "--cuts 要写成用逗号隔开的秒数，例如 0,29.6,65.1"}, 2)
    duration = float(measured.get("duration_s") or (lines[-1]["end"] if lines else 0))
    if not duration:
        emit({"ok": False, "error": "不知道原片多长：先跑 media_profile.py"}, 1)
    if not cuts or cuts[0] > 0.01:
        cuts = [0.0] + cuts
    if cuts[-1] < duration - 0.5:
        cuts.append(duration)
    target = args.target_seconds or duration
    stretch = target / duration
    pace = pace_for(args, [])
    if pace:
        rate, basis = pace["typical_chars_per_second"], pace["basis"]
    else:
        ref = reference_tone(reference)
        rate, basis = ((ref or {}).get("chars_per_second") or 4.8), "reference"
    rows = []
    spans = list(zip(cuts, cuts[1:]))
    for index, (start, end) in enumerate(spans, 1):
        inside = [line for line in lines if start - 0.01 <= (line["start"] + line["end"]) / 2 < end]
        chars = sum(zh_text.spoken_len(line["text"]) for line in inside)
        seconds = (end - start) * stretch
        # The pause before the next section is part of this one's time, and nothing is said in it.
        budget = max(0.0, seconds - (args.gap if index < len(spans) else 0.0)) * rate
        rows.append({"section": index, "start": round(start, 2), "end": round(end, 2), "reference_chars": chars,
                     "reference_lines": len(inside), "new_seconds": round(seconds, 1),
                     "new_chars_low": int(budget * 0.9), "new_chars_high": int(budget)})
        print(f"第 {index} 段 {start:.1f}–{end:.1f} 秒：原片 {chars} 字、{len(inside)} 行；新稿这段写 {rows[-1]['new_chars_low']}–{rows[-1]['new_chars_high']} 字。")
    low, high = sum(row["new_chars_low"] for row in rows), sum(row["new_chars_high"] for row in rows)
    print(f"全片合计 {low}–{high} 字。上限是正好念满的字数：句子短、停顿多的稿子念得更久，写在中间，不要顶着上限写。")
    if basis == "reference":
        print("还没选音色，字数先按原片语速给；选定音色后带 --voice-id 再跑一次。")
    emit({"ok": True, "basis": basis, "chars_per_second": round(rate, 2), "target_s": round(target, 1),
          "total_chars_low": low, "total_chars_high": high, "sections": rows})


def cmd_check(args: argparse.Namespace) -> None:
    if not args.script.is_file():
        emit({"ok": False, "error": f"no such file: {args.script}"}, 2)
    sections = read_script(args.script, args.voice_over_only)
    if not sections and is_screenplay(args.script.read_text(encoding="utf-8").splitlines()):
        print("这份剧本里没有台词和旁白：口气、照搬和配音时长都不用查。")
        emit({"ok": True, "no_speech": True, "failed": [], "notes": [], "chars": 0})
    if not sections:
        emit({"ok": False, "error": "稿子里没读到要念的内容。剧本里旁白写成「[VO] 旁白：……」，纯文本一段一段写、空行隔开。"}, 1)
    reference = read_reference(args.reference)
    pace = pace_for(args, [section["text"] for section in sections])
    result = run_checks(sections, reference, pace, args.target_seconds, explicit_pauses(args.script))
    label = {"pass": "通过  ", "fail": "不通过 ", "note": "提示  "}
    for item in result["checks"]:
        print(label[item["level"]] + item["check"] + "：" + item["detail"])
    out = args.out or (args.reference / "script-check.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    emit({"ok": True, "report": str(out), "failed": result["failed"], "notes": result["notes"],
          "estimate_s": result["estimate_s"], "target_s": result["target_s"], "chars": result["script"]["chars"]})


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.version_info < MINIMUM_PYTHON:
        emit({"ok": False, "error": "needs Python %d.%d or newer" % MINIMUM_PYTHON}, 2)
    parser = argparse.ArgumentParser(description="拿新稿和原片的台词比：时长、照搬、口气。")
    parser.add_argument("--store", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    def voice_options(p: argparse.ArgumentParser) -> None:
        p.add_argument("--voice-id", help="创作者选定的音色编号")
        p.add_argument("--speed", type=float, default=1.0)
        p.add_argument("--audition", type=Path, help="他选中的那条试听")
        p.add_argument("--audition-text", help="试听念的那一句")
        p.add_argument("--audition-speed", type=float, help="试听用的语速，和 --speed 不同时才给")
        p.add_argument("--gap", type=float, default=0.3, help="段与段之间留的秒数")
        p.add_argument("--target-seconds", type=float, help="新片要多长；不给就取原片时长")

    profile = sub.add_parser("profile", help="量原片台词的口气")
    profile.add_argument("reference", type=Path)
    budget = sub.add_parser("budget", help="每段该写多少字")
    budget.add_argument("reference", type=Path)
    budget.add_argument("--cuts", required=True, help="各段起点的秒数，逗号隔开，例如 0,29.6,65.1")
    voice_options(budget)
    check = sub.add_parser("check", help="检查新稿")
    check.add_argument("script", type=Path, help="《剧本.md》或纯文本旁白稿")
    check.add_argument("--reference", type=Path, required=True, help="参考片目录")
    check.add_argument("--voice-over-only", action="store_true", help="只看 [VO] / [OS]，不看对白")
    check.add_argument("--out", type=Path)
    voice_options(check)
    args = parser.parse_args()

    if not args.reference.is_dir():
        emit({"ok": False, "error": f"no such folder: {args.reference}"}, 2)
    try:
        {"profile": cmd_profile, "budget": cmd_budget, "check": cmd_check}[args.command](args)
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        emit({"ok": False, "error": str(error)}, 1)


if __name__ == "__main__":
    main()
