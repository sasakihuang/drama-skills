"""Behaviour of the remake skill's audio-side tools: joining narration clips and timing every
sentence from the audio, then mixing voice, bed and effects. Clips are synthetic tones and silences."""

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SCRIPTS = SUITE / "skills/short-drama-remake/scripts"
sys.path.insert(0, str(SCRIPTS))

import voice_pace  # noqa: E402
import voice_track  # noqa: E402
import zh_text  # noqa: E402

HAS_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def run_tool(script: str, *arguments: str) -> dict:
    done = subprocess.run(
        [sys.executable, "-B", str(SCRIPTS / script), *arguments],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"{script} printed nothing; stderr: {done.stderr}")
    return json.loads(lines[-1])


def tone_wave(path: Path, pattern: list, frequency: int = 330) -> None:
    """A clip made of (seconds, audible) stretches."""
    inputs: list = []
    for seconds, audible in pattern:
        source = f"sine=frequency={frequency}:duration={seconds}" if audible else f"anullsrc=r=44100:cl=mono:d={seconds}"
        inputs += ["-f", "lavfi", "-i", source]
    joined = "".join(f"[{n}:a]" for n in range(len(pattern))) + f"concat=n={len(pattern)}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", joined, "-map", "[out]", "-ar", "44100", str(path)],
        check=True,
    )


def level_between(path: Path, start: float, end: float) -> float:
    """Mean level in dB of one stretch of a file."""
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-ss", str(start), "-t", str(end - start), "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )
    return float(done.stderr.split("mean_volume:")[1].split("dB")[0])


def length_of(path: Path) -> float:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(done.stdout.strip())


def lay_out(clause_seconds: list, pauses: list, lead: float = 0.3) -> tuple:
    """Clip geometry for clauses spoken back to back with a silence after each but the last."""
    cursor, inner, spans = lead, [], []
    for index, seconds in enumerate(clause_seconds):
        spans.append((round(cursor, 3), round(cursor + seconds, 3)))
        cursor += seconds
        if index < len(pauses):
            inner.append((round(cursor, 3), round(cursor + pauses[index], 3)))
            cursor += pauses[index]
    return lead, round(cursor, 3), inner, spans


class ClauseSplittingTests(unittest.TestCase):
    def test_marks_stay_with_the_clause_they_follow(self) -> None:
        clauses = voice_track.split_clauses("先看这里，再看那里。然后呢？！\n最后一句")
        self.assertEqual([c["text"] for c in clauses], ["先看这里", "再看那里", "然后呢", "最后一句"])
        self.assertEqual([c["major"] for c in clauses], [False, True, True, True])
        self.assertEqual(clauses[2]["mark"], "？！")
        self.assertEqual([c["chars"] for c in clauses], [4, 4, 3, 4])

    def test_sentences_are_rebuilt_from_clauses_with_their_marks(self) -> None:
        clauses = voice_track.split_clauses("量大了，价没动。这才是问题！")
        sentences = voice_track.group_sentences(clauses, [(0.0, 1.0), (1.2, 2.0), (2.5, 4.0)])
        self.assertEqual([(s["start"], s["end"], s["text"]) for s in sentences],
                         [(0.0, 2.0, "量大了，价没动。"), (2.5, 4.0, "这才是问题！")])


class AlignmentTests(unittest.TestCase):
    def test_each_mark_lands_on_its_own_silence(self) -> None:
        clauses = voice_track.split_clauses("第一句话说完，第二句话接上。第三句话收尾。")
        first, last, inner, spans = lay_out([1.2, 1.2, 1.2], [0.3, 0.6])
        self.assertEqual(voice_track.align(clauses, first, last, inner), spans)

    def test_a_breath_inside_a_clause_is_not_taken_for_a_mark(self) -> None:
        clauses = voice_track.split_clauses("这一句很长很长很长很长要换一口气才能念完，后面一句短。结束了。")
        first = 0.2
        # 3.4 s of speech with a breath in the middle, a comma pause, 1.0 s, a full stop, 0.6 s
        inner = [(1.9, 2.15), (3.85, 4.2), (5.2, 5.9)]
        last = 6.5
        times = voice_track.align(clauses, first, last, inner)
        self.assertEqual(times[0], (0.2, 3.85))
        self.assertEqual(times[1], (4.2, 5.2))
        self.assertEqual(times[2], (5.9, 6.5))

    def test_a_mark_without_a_silence_falls_between_its_neighbours(self) -> None:
        clauses = voice_track.split_clauses("开头四字，中间也是四字，最后四字。收尾四字。")
        # No silence after the first comma: the first two clauses run together for 1.6 s.
        first, last, inner = 0.0, 4.5, [(1.6, 1.9), (2.7, 3.3)]
        times = voice_track.align(clauses, first, last, inner)
        self.assertEqual(times[1][1], 1.6)
        self.assertEqual(times[2], (1.9, 2.7))
        self.assertEqual(times[3], (3.3, 4.5))
        self.assertAlmostEqual(times[0][1], 0.8, delta=0.25)
        self.assertEqual(times[0][1], times[1][0])

    def test_a_voice_that_changes_pace_does_not_drift_onto_the_wrong_silences(self) -> None:
        """Ninety seconds that start slow and end fast, with a breath in every third clause:
        measured from the start of the clip, later marks slide a whole silence out of place."""
        text = "".join("这一小句有八个字" + ("。" if n % 3 == 2 else "，") for n in range(36))
        clauses = voice_track.split_clauses(text)
        cursor, inner, expected = 0.25, [], []
        for n in range(36):
            seconds = 8 * (0.28 if n < 18 else 0.15)
            start = cursor
            if n % 3 == 1:  # a breath in the middle of this clause
                inner.append((round(cursor + seconds / 2, 3), round(cursor + seconds / 2 + 0.25, 3)))
                cursor += 0.25
            cursor += seconds
            expected.append((round(start, 3), round(cursor, 3)))
            if n < 35:
                pause = 0.7 if n % 3 == 2 else 0.35
                inner.append((round(cursor, 3), round(cursor + pause, 3)))
                cursor += pause
        times = voice_track.align(clauses, 0.25, round(cursor, 3), inner)
        wrong = [n for n, (got, want) in enumerate(zip(times, expected)) if abs(got[0] - want[0]) > 0.02 or abs(got[1] - want[1]) > 0.02]
        self.assertEqual(wrong, [])


class CaptionTests(unittest.TestCase):
    def test_a_long_clause_is_cut_and_keeps_only_question_and_exclamation_marks(self) -> None:
        clauses = voice_track.split_clauses("你看到的是交易突然热闹起来却把它直接翻译成了价格还要往上走，对吗？")
        lines = voice_track.caption_lines(clauses, [(0.0, 6.0), (6.4, 7.0)], 16)
        self.assertEqual("".join(line["text"] for line in lines[:-1]), clauses[0]["text"])
        self.assertTrue(all(len(line["text"]) <= 16 for line in lines))
        self.assertEqual(lines[-1]["text"], "对吗？")
        self.assertEqual(lines[0]["start"], 0.0)
        self.assertEqual(lines[-2]["end"], 6.0)
        for before, after in zip(lines, lines[1:]):
            self.assertLessEqual(before["end"], after["start"])

    def test_numbers_and_latin_words_are_not_split_across_lines(self) -> None:
        clauses = voice_track.split_clauses("这一年指数从三千点一路涨到了3856点才停下来")
        lines = voice_track.caption_lines(clauses, [(0.0, 5.0)], 12)
        self.assertTrue(any("3856" in line["text"] for line in lines))


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
class BuildTrackTests(unittest.TestCase):
    def make_clips(self, root: Path) -> Path:
        tone_wave(root / "a.wav", [(0.4, False), (1.5, True), (0.4, False), (1.0, True), (0.5, False)])
        tone_wave(root / "b.wav", [(0.2, False), (2.0, True), (0.3, False)])
        manifest = root / "clips.json"
        manifest.write_text(json.dumps([
            {"id": "S1", "audio": "a.wav", "text": "第一段的前半句话，后半句话。", "voice_id": "test-voice", "speed": 1.2},
            {"id": "S2", "audio": "b.wav", "text": "第二段只有一句话。", "voice_id": "test-voice", "speed": 1.2},
        ], ensure_ascii=False), encoding="utf-8")
        return manifest

    def test_build_trims_joins_times_and_learns(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_clips(root)
            out, store = root / "制作", root / "pace.json"
            result = run_tool("voice_track.py", "--store", str(store), "build", "--manifest", str(manifest),
                              "--out", str(out), "--gap", "0.3", "--target-seconds", "6", "--script-js", str(out / "timetable.js"))
            self.assertTrue(result["ok"], result)
            table = json.loads((out / "时间表.json").read_text(encoding="utf-8"))
            first, second = table["segments"]
            # Clip one keeps 2.9 s of voice plus a little quiet at each end; the gap follows it.
            self.assertAlmostEqual(first["end"] - first["start"], 3.0, delta=0.08)
            self.assertAlmostEqual(second["start"] - first["end"], 0.3, delta=0.01)
            self.assertAlmostEqual(table["total_s"], 3.0 + 0.3 + 2.1, delta=0.15)
            self.assertAlmostEqual(length_of(out / "旁白.wav"), table["total_s"], delta=0.05)
            sentences = first["sentences"]
            self.assertEqual(len(sentences), 1)
            starts = [line["start"] for line in table["captions"]]
            self.assertEqual(len(starts), 3)
            self.assertAlmostEqual(starts[1] - starts[0], 1.9, delta=0.08, msg="the second line starts after the pause")
            self.assertAlmostEqual(starts[2], second["start"] + 0.05, delta=0.05)
            self.assertEqual(result["within_tenth"], abs(table["total_s"] / 6 - 1) <= 0.10)
            self.assertTrue((out / "字幕.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,"))
            self.assertTrue((out / "timetable.js").read_text(encoding="utf-8").startswith("window.TIMETABLE = {"))
            self.assertEqual(result["learned"], {"test-voice": 2})
            self.assertEqual(len(voice_pace.observations_for("test-voice", store)), 2)

    def test_no_learn_leaves_the_pace_record_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self.make_clips(root)
            store = root / "pace.json"
            result = run_tool("voice_track.py", "--store", str(store), "build", "--manifest", str(manifest),
                              "--out", str(root / "out"), "--no-learn")
            self.assertTrue(result["ok"], result)
            self.assertFalse(store.exists())

    def test_clips_are_read_from_a_projects_finished_speech_jobs_in_the_order_asked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / ".short-drama/production/jobs"
            jobs.mkdir(parents=True)
            audio = root / "剧集/EP001/制作成果/audio"
            audio.mkdir(parents=True)
            for name, seconds in (("one", 1.0), ("two", 1.4)):
                tone_wave(audio / f"{name}.wav", [(0.2, False), (seconds, True), (0.2, False)])
                (jobs / f"{name}.json").write_text(json.dumps({
                    "job_id": f"EP001-VO-{name}", "modality": "tts", "prompt": f"这是{name}这一段要念的话。",
                    "outputs": [f"剧集/EP001/制作成果/audio/{name}.wav"],
                    "parameters": {"voice_id": "job-voice", "speed": 1.1},
                }, ensure_ascii=False), encoding="utf-8")
            (jobs / "picture.json").write_text(json.dumps({"job_id": "EP001-IMG", "modality": "image", "outputs": ["x.png"]}), encoding="utf-8")
            clips = voice_track.clips_from_jobs(root, ["EP001-VO-two", "EP001-VO-one"])
            self.assertEqual([clip["id"] for clip in clips], ["EP001-VO-two", "EP001-VO-one"])
            self.assertEqual(clips[0]["voice_id"], "job-voice")
            self.assertEqual(clips[0]["speed"], 1.1)
            with self.assertRaises(RuntimeError):
                voice_track.clips_from_jobs(root, ["EP001-IMG"])
            with self.assertRaises(RuntimeError):
                voice_track.clips_from_jobs(root, ["EP001-VO-missing"])

    def test_a_line_reworded_after_it_was_recorded_is_refused(self) -> None:
        # The production tool keeps one file per job and rewrites it when the line is prepared again,
        # so the text in it is the latest wording. A recording belongs to the wording whose
        # fingerprint its run carries.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / ".short-drama/production/jobs"
            jobs.mkdir(parents=True)
            (root / "制作成果").mkdir()
            (root / "制作成果/line.mp3").write_bytes(b"sound")

            def prepare(text: str, fingerprint: str) -> None:
                (jobs / "abc.json").write_text(json.dumps({
                    "job_id": "EP001-VO-001", "modality": "tts", "prompt": text, "fingerprint": fingerprint,
                    "outputs": ["制作成果/line.mp3"], "parameters": {"voice_id": "v", "speed": 1.0},
                }, ensure_ascii=False), encoding="utf-8")

            def ran(name: str, fingerprint: str, status: str) -> None:
                folder = root / ".short-drama/production/runs/abc"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / f"{name}.json").write_text(json.dumps({"status": status, "fingerprint": fingerprint}), encoding="utf-8")

            def text() -> str:
                return voice_track.clips_from_jobs(root, ["EP001-VO-001"])[0]["text"]

            prepare("第一版的说法。", "f1")
            self.assertEqual(text(), "第一版的说法。", "no run on record: nothing to check the wording against")
            ran("r1", "f1", "succeeded")
            self.assertEqual(text(), "第一版的说法。")
            prepare("第二版改了说法。", "f2")
            with self.assertRaises(RuntimeError) as refused:
                text()
            self.assertIn("EP001-VO-001", str(refused.exception))
            ran("r2", "f2", "failed")
            with self.assertRaises(RuntimeError):
                text()
            ran("r3", "f2", "succeeded")
            self.assertEqual(text(), "第二版改了说法。")

    def test_numbers_and_pause_tags_do_not_break_captions(self) -> None:
        clauses = voice_track.split_clauses("成交了1,000股，9:30开盘就冲。<#0.5#>先看这一根<#0.3#>再看下一根。")
        self.assertEqual([clause["text"] for clause in clauses], ["成交了1000股", "9\u223630开盘就冲", "先看这一根", "再看下一根"])
        self.assertEqual([clause["chars"] for clause in clauses], [8, 7, 5, 5])
        self.assertEqual([clause["major"] for clause in clauses], [False, True, False, True])
        self.assertEqual(zh_text.pause_features("今天讲放量。<#0.5#>先看这一根。"), (10, 0, 1))


@unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
class MixTests(unittest.TestCase):
    def test_mix_reaches_the_target_level_and_runs_as_long_as_the_voice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(3.0, True), (0.3, False), (2.7, True)])
            tone_wave(root / "bed.wav", [(9.0, True)], frequency=110)
            effects = root / "library"
            effects.mkdir()
            tone_wave(effects / "hit.wav", [(0.3, True)], frequency=880)
            listing = root / "sfx.json"
            listing.write_text(json.dumps([{"file": "hit.wav", "at": 1.0}, {"file": "hit.wav", "at": 4.0, "volume": 0.2}]), encoding="utf-8")
            result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--bed", str(root / "bed.wav"),
                              "--sfx", str(listing), "--sfx-root", str(effects), "--out", str(root / "mix.wav"))
            self.assertTrue(result["ok"], result)
            self.assertAlmostEqual(result["length_s"], 6.0, delta=0.05)
            self.assertAlmostEqual(result["integrated_lufs"], -13.0, delta=1.0)
            self.assertLessEqual(result["true_peak_dbfs"], -0.5)
            self.assertEqual((result["bed"], result["effects"]), (True, 2))
            self.assertTrue(result["loud_enough"] and result["no_clipping"])

    def test_an_effect_sits_beside_the_narration_whatever_level_its_file_was_recorded_at(self) -> None:
        # The effect library's bass impacts are recorded 17 LU hotter than its pops. As a plain
        # multiplier, 0.5 put an impact 10 dB over a quiet voice; the opening sentence was drowned.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(8.0, True)])
            for name, gain in (("hot.wav", 12), ("faint.wav", -12)):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                                "-af", f"volume={gain}dB", str(root / name)], check=True)

            def rise(file: str, volume: float) -> float:
                listing = root / "sfx.json"
                listing.write_text(json.dumps([{"file": file, "at": 3.0, "volume": volume}]), encoding="utf-8")
                result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--sfx", str(listing), "--out", str(root / "mix.wav"))
                self.assertTrue(result["ok"], result)
                self.assertTrue(result["no_clipping"])
                return level_between(root / "mix.wav", 3.2, 3.8) - level_between(root / "mix.wav", 1.0, 2.0)

            # As loud as the voice: the two together are about 3 dB over the voice alone, from either file.
            self.assertAlmostEqual(rise("hot.wav", 1.0), 3.0, delta=1.0)
            self.assertAlmostEqual(rise("faint.wav", 1.0), 3.0, delta=1.0)
            # Half as loud: about 1 dB.
            self.assertAlmostEqual(rise("hot.wav", 0.5), 1.0, delta=0.6)

    def test_a_film_nobody_speaks_in_mixes_its_effects_beside_the_music(self) -> None:
        # Many motion-graphics originals have no narration at all; a remake of one has music and effects
        # only, and an effect's volume then means its loudness beside the music.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "bed.wav", [(8.0, True)], frequency=330)
            effects = root / "library"
            effects.mkdir()
            tone_wave(effects / "hit.wav", [(0.6, True)], frequency=440)
            listing = root / "sfx.json"
            listing.write_text(json.dumps([{"file": "hit.wav", "at": 3.0, "volume": 0.5}]), encoding="utf-8")
            result = run_tool("mix_audio.py", "--bed", str(root / "bed.wav"), "--sfx", str(listing),
                              "--sfx-root", str(effects), "--out", str(root / "mix.wav"), "--length", "6")
            self.assertTrue(result["ok"], result)
            self.assertAlmostEqual(result["length_s"], 6.0, delta=0.05)
            self.assertEqual((result["effects_beside"], result["narration_lufs"]), ("music", None))
            self.assertAlmostEqual(result["integrated_lufs"], -13.0, delta=1.0)
            self.assertTrue(result["no_clipping"])
            # Half as loud as the music: about 1 dB over the music alone while it plays.
            rise = level_between(root / "mix.wav", 3.1, 3.5) - level_between(root / "mix.wav", 1.0, 2.0)
            self.assertAlmostEqual(rise, 1.0, delta=0.8)
            neither = run_tool("mix_audio.py", "--out", str(root / "none.wav"))
            self.assertFalse(neither["ok"])
            self.assertFalse((root / "none.wav").exists())

    def test_a_silent_narration_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(3.0, False)])
            result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--out", str(root / "mix.wav"))
            self.assertFalse(result["ok"])
            self.assertIn("旁白", result["error"])
            self.assertFalse((root / "mix.wav").exists())

    def test_a_long_film_with_hundreds_of_effects_still_mixes(self) -> None:
        # One command naming every cue is longer than Windows allows well before this many.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(30.0, True)])
            effects = root / "library"
            effects.mkdir()
            tone_wave(effects / "hit.wav", [(0.1, True)], frequency=880)
            tone_wave(effects / "pop.wav", [(0.1, True)], frequency=660)
            listing = root / "sfx.json"
            listing.write_text(json.dumps([{"file": "hit.wav" if n % 2 else "pop.wav", "at": round(0.11 * n, 2), "volume": 0.2}
                                           for n in range(260)]), encoding="utf-8")
            result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--sfx", str(listing),
                              "--sfx-root", str(effects), "--out", str(root / "mix.wav"))
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["effects"], 260)
            self.assertAlmostEqual(result["length_s"], 30.0, delta=0.05)
            self.assertTrue(result["no_clipping"])

    def test_voice_alone_can_be_padded_for_an_end_card(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(2.0, True)])
            result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--out", str(root / "mix.wav"),
                              "--length", "3.5", "--target-lufs", "-16")
            self.assertTrue(result["ok"], result)
            self.assertAlmostEqual(result["length_s"], 3.5, delta=0.05)
            self.assertEqual((result["bed"], result["effects"]), (False, 0))

    def test_a_missing_effect_file_is_reported_not_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tone_wave(root / "voice.wav", [(1.0, True)])
            listing = root / "sfx.json"
            listing.write_text(json.dumps([{"file": "nowhere.mp3", "at": 0.2}]), encoding="utf-8")
            result = run_tool("mix_audio.py", "--voice", str(root / "voice.wav"), "--sfx", str(listing), "--out", str(root / "mix.wav"))
            self.assertFalse(result["ok"])
            self.assertFalse((root / "mix.wav").exists())


class CaptionBreakTests(unittest.TestCase):
    def lines(self, text: str, limit: int = 18) -> list:
        pieces = -(-len(text) // limit)
        edges = [0] + voice_track.break_points(text, pieces, limit) + [len(text)]
        return [text[a:b] for a, b in zip(edges, edges[1:])]

    def test_a_long_clause_is_cut_where_a_phrase_starts(self) -> None:
        self.assertEqual(self.lines("四张图放在一起你会发现量的大小提供了线索"), ["四张图放在一起", "你会发现量的大小提供了线索"])
        self.assertEqual(self.lines("一根高量柱既不能告诉你进来了多少人就走了"), ["一根高量柱既不能告诉", "你进来了多少人就走了"])
        self.assertEqual(self.lines("这笔成交记600股不能把买卖两边加起来算成1200股"),
                         ["这笔成交记600股", "不能把买卖两边加起来算成1200股"])

    def test_no_line_starts_with_a_particle_ends_on_a_lead_in_or_splits_a_number(self) -> None:
        samples = ["你看到的是交易突然热闹起来却把它直接翻译成了价格还要往上走",
                   "所以真正面对盘面的时候右边其实还是一片空白什么都没有",
                   "这个月的成交量是上个月的3.5倍而且还在继续放大没有停下来的意思",
                   "把那根阳线之后的空白留出来看看两边怎么接着走然后再回头看这根线"]
        for text in samples:
            with self.subTest(text=text):
                parts = self.lines(text)
                self.assertEqual("".join(parts), text)
                self.assertTrue(all(len(part) <= 18 for part in parts), parts)
                for before, after in zip(parts, parts[1:]):
                    self.assertNotIn(after[0], voice_track.NEVER_FIRST, parts)
                    self.assertNotIn(before[-1], voice_track.NEVER_LAST, parts)
                    self.assertFalse(before[-1].isdigit() and (after[0].isdigit() or after[0] == "."), parts)


if __name__ == "__main__":
    unittest.main()
