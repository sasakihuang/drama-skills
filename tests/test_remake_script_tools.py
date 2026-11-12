"""Behaviour of the remake skill's script-side tools: Chinese text helpers, the per-voice pace
estimate and the script check. Inputs are synthetic; no reference video or speech service is used."""

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

import script_check  # noqa: E402
import voice_pace  # noqa: E402
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


def write_reference(folder: Path, lines: list, duration: float) -> None:
    segments = [{"start": start, "end": end, "text": text} for start, end, text in lines]
    (folder / "transcript.json").write_text(json.dumps({"segments": segments}, ensure_ascii=False), encoding="utf-8")
    (folder / "source.profile.json").write_text(json.dumps({"duration_s": duration}), encoding="utf-8")


def tone_wave(path: Path, pattern: list) -> None:
    """A clip made of (seconds, audible) stretches, so quiet ends and gaps have known lengths."""
    inputs: list = []
    for seconds, audible in pattern:
        source = f"sine=frequency=330:duration={seconds}" if audible else f"anullsrc=r=44100:cl=mono:d={seconds}"
        inputs += ["-f", "lavfi", "-i", source]
    joined = "".join(f"[{n}:a]" for n in range(len(pattern))) + f"concat=n={len(pattern)}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", joined, "-map", "[out]", "-ar", "44100", str(path)],
        check=True,
    )


class ChineseTextTests(unittest.TestCase):
    def test_traditional_characters_fold_to_simplified(self) -> None:
        self.assertEqual(zh_text.to_simplified("買賣雙方都說這裡沒問題"), "买卖双方都说这里没问题")
        self.assertEqual(zh_text.to_simplified("以後怎麼辦"), "以后怎么办")

    def test_words_that_are_already_simplified_are_left_alone(self) -> None:
        self.assertEqual(zh_text.to_simplified("成交量顯著放大，這是一位著名的分析師說的"), "成交量显著放大，这是一位著名的分析师说的")
        self.assertEqual(zh_text.to_simplified("他看著盤面，接著說"), "他看着盘面，接着说")
        self.assertEqual(zh_text.to_simplified("扭轉乾坤，乾脆點；登高瞭望，明瞭了"), "扭转乾坤，干脆点；登高瞭望，明了了")

    def test_spoken_form_ignores_marks_spaces_and_case(self) -> None:
        self.assertEqual(zh_text.spoken("K 線，漲了 3 倍！"), zh_text.spoken("k线涨了3倍"))

    def test_pause_features_count_marks_inside_and_between_sentences(self) -> None:
        chars, minor, major = zh_text.pause_features("先看这里，再看那里。然后呢？记住一点：别急。")
        self.assertEqual((chars, minor, major), (17, 2, 2))
        # The closing mark is outside the voiced span, and a doubled mark is one pause.
        self.assertEqual(zh_text.pause_features("真的吗？！")[2], 0)

    def test_sentences_keep_commas_and_clauses_split_them(self) -> None:
        text = "量大了，价格没动。这才是问题"
        self.assertEqual(zh_text.sentences(text), ["量大了，价格没动", "这才是问题"])
        self.assertEqual(zh_text.clauses(text), ["量大了", "价格没动", "这才是问题"])


class VoicePaceTests(unittest.TestCase):
    def clips(self, per_char: float, short: float, between: float) -> list:
        """Clips of a made-up voice: sounding time and silent time are stored apart, as measured."""
        shapes = [(20, 1, 1), (60, 5, 3), (85, 6, 5), (120, 14, 4), (40, 2, 3), (150, 9, 10), (33, 6, 1), (72, 3, 6)]
        return [(c, m, j, round(per_char * c, 3), round(short * m + between * j, 3)) for c, m, j in shapes]

    def seconds(self, fitted: tuple, chars: int, minor: int, major: int) -> float:
        return fitted[0] * chars + fitted[1] * voice_pace.pause_units(minor, major)

    def test_an_unheard_voice_uses_the_general_values(self) -> None:
        self.assertEqual(voice_pace.fit([]), voice_pace.PRIOR)
        self.assertEqual(voice_pace.evidence([])[0], "general")

    def test_one_audition_line_moves_the_estimate_part_of_the_way(self) -> None:
        general = self.seconds(voice_pace.PRIOR, 300, 30, 14)
        slow_line = (16, 2, 1, 2 * voice_pace.PRIOR[0] * 16, 2 * voice_pace.PRIOR[1] * voice_pace.pause_units(2, 1))
        after_one = self.seconds(voice_pace.fit([slow_line]), 300, 30, 14)
        self.assertGreater(after_one, general * 1.2)
        self.assertLess(after_one, general * 2.0)
        self.assertEqual(voice_pace.evidence([slow_line])[0], "audition")

    def test_a_films_worth_of_clips_recovers_a_voice_with_long_pauses(self) -> None:
        clips = self.clips(0.14, 0.9, 1.4)
        fitted = voice_pace.fit(clips)
        # A script far choppier than anything the voice was heard reading: the estimate must not
        # depend on the new text pausing as often as the old ones did.
        truth = 0.14 * 200 + 0.9 * 40 + 1.4 * 20
        self.assertAlmostEqual(self.seconds(fitted, 200, 40, 20) / truth, 1.0, delta=0.07)
        general = self.seconds(voice_pace.PRIOR, 200, 40, 20)
        self.assertGreater(abs(general / truth - 1.0), 0.10, "the test voice must differ from the general values")
        self.assertEqual(voice_pace.evidence(clips)[0], "history")

    def test_estimate_scales_with_speed_and_adds_gaps_between_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "pace.json"
            segments = ["第一段先说结论，再说原因。", "第二段举一个例子，说完就收。"]
            normal = voice_pace.estimate(segments, "test-voice", 1.0, gap=0.3, store=store)
            fast = voice_pace.estimate(segments, "test-voice", 1.25, gap=0.3, store=store)
            self.assertAlmostEqual(normal["voiced_s"] / fast["voiced_s"], 1.25, delta=0.03)
            self.assertAlmostEqual(normal["total_s"] - normal["voiced_s"], 0.3, delta=0.05)
            self.assertEqual(len(normal["segments_s"]), 2)

    def test_remembered_clips_persist_without_duplicates_and_change_the_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory) / "pace.json"
            text = ["这是一段用来估时的旁白，说到这里停一下。然后接着往下说，直到说完。"]
            before = voice_pace.estimate(text, "my-voice", 1.0, store=store)
            clips = self.clips(0.30, 0.8, 1.2)
            self.assertEqual(voice_pace.remember("my-voice", clips, store), len(clips))
            self.assertEqual(voice_pace.remember("my-voice", clips, store), 0)
            self.assertEqual(len(voice_pace.observations_for("my-voice", store)), len(clips))
            after = voice_pace.estimate(text, "my-voice", 1.0, store=store)
            self.assertGreater(after["total_s"], before["total_s"] * 1.3)
            self.assertEqual(after["basis"], "history")
            saved = json.loads(store.read_text(encoding="utf-8"))
            for row in saved["voices"]["my-voice"]["clips"]:
                self.assertTrue(all(isinstance(value, (int, float)) for value in row), "only numbers are stored")

    def test_shipped_seed_is_usable_for_every_voice_it_lists(self) -> None:
        seed = json.loads(voice_pace.SEED.read_text(encoding="utf-8"))
        self.assertTrue(seed["voices"])
        with tempfile.TemporaryDirectory() as directory:
            for voice_id in seed["voices"]:
                with self.subTest(voice=voice_id):
                    result = voice_pace.estimate(["一句十来个字的旁白，用来试一试。"], voice_id, 1.0,
                                                 store=Path(directory) / "none.json")
                    self.assertNotEqual(result["basis"], "general")
                    self.assertTrue(2.0 < result["typical_chars_per_second"] < 8.0)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_measure_clip_leaves_out_the_quiet_at_both_ends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clip = Path(directory) / "clip.wav"
            tone_wave(clip, [(0.4, False), (1.0, True), (0.5, False), (1.2, True), (0.6, False)])
            measured = voice_pace.measure_clip(clip)
            self.assertAlmostEqual(measured["total"], 3.7, delta=0.05)
            self.assertAlmostEqual(measured["lead"], 0.4, delta=0.06)
            self.assertAlmostEqual(measured["trail"], 0.6, delta=0.06)
            self.assertAlmostEqual(measured["net"], 2.7, delta=0.1)
            self.assertAlmostEqual(measured["inner"], 0.5, delta=0.06)

    @unittest.skipUnless(HAS_FFMPEG, "needs ffmpeg")
    def test_learn_command_records_a_clip_under_its_voice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip = root / "segment.wav"
            tone_wave(clip, [(0.3, False), (2.5, True), (0.5, False), (1.5, True), (0.3, False)])
            store = root / "pace.json"
            result = run_tool("voice_pace.py", "--store", str(store), "learn", "--voice-id", "cli-voice",
                              "--speed", "1.1", "--audio", str(clip), "--text", "这一段旁白一共有十几个字，中间停一次。")
            self.assertTrue(result["ok"])
            self.assertEqual(result["added"], {"cli-voice": 1})
            chars, minor, _major, sounding, silent = voice_pace.observations_for("cli-voice", store)[0]
            self.assertEqual((chars, minor), (17, 1))
            self.assertAlmostEqual(sounding, 4.0 * 1.1, delta=0.2)
            self.assertAlmostEqual(silent, 0.5 * 1.1, delta=0.1)


class ScriptReadingTests(unittest.TestCase):
    def test_screenplay_keeps_spoken_lines_per_scene_and_drops_directions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "剧本.md"
            script.write_text(
                "# EP001 示例\n\n"
                "## EP001-SC001 内 · 画布 · 连续\n\n"
                "深色网格上，一根柱子升起来。\n\n"
                "[画面文字] 标题：先看这里\n\n"
                "[VO] 旁白：先看这根柱子（停半拍）它比旁边的都高。\n\n"
                "## EP001-SC002 内 · 画布 · 连续\n\n"
                "[SFX] 一声闷响\n\n"
                "店员（压低声音）：这单我做不了主。\n\n"
                "[OS] 经理：让他进来。\n",
                encoding="utf-8",
            )
            sections = script_check.read_script(script)
            self.assertEqual([s["id"] for s in sections], ["EP001-SC001", "EP001-SC002"])
            self.assertEqual(zh_text.spoken(sections[0]["text"]), "先看这根柱子它比旁边的都高")
            self.assertEqual(zh_text.spoken(sections[1]["text"]), "这单我做不了主让他进来")
            narration_only = script_check.read_script(script, only_voice_over=True)
            self.assertEqual(zh_text.spoken(narration_only[1]["text"]), "让他进来")

    def test_plain_text_is_cut_at_blank_lines_and_pause_marks_are_not_spoken(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "旁白.txt"
            script.write_text("# 标题不念\n第一段，说结论。<#0.5#>接着说。\n\n第二段。\n", encoding="utf-8")
            sections = script_check.read_script(script)
            self.assertEqual(len(sections), 2)
            self.assertEqual(zh_text.spoken(sections[0]["text"]), "第一段说结论接着说")
            self.assertAlmostEqual(script_check.explicit_pauses(script), 0.5)


class NarrationDraftTests(unittest.TestCase):
    DRAFT = ("## 第一段\n\n很多人看见放量就追。问题来了：量变大了，价格站住了吗？\n\n"
             "## 第二段\n\n记住一句话：热闹不等于确认。先比较，再定位。\n")

    def test_a_draft_with_headings_and_colons_is_read_whole(self) -> None:
        # "问题来了：……" is narration, not a character called 问题来了 speaking.
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "旁白稿.md"
            draft.write_text(self.DRAFT, encoding="utf-8")
            for only_voice_over in (False, True):
                sections = script_check.read_script(draft, only_voice_over)
                said = zh_text.spoken("".join(section["text"] for section in sections))
                self.assertEqual(said, zh_text.spoken("很多人看见放量就追问题来了量变大了价格站住了吗记住一句话热闹不等于确认先比较再定位"))

    def test_a_hedge_is_not_also_counted_as_a_commitment(self) -> None:
        self.assertEqual(script_check.tone("这不一定是突破。")["asserts"], 0)
        self.assertEqual(script_check.tone("这一定是突破。")["asserts"], 1)

    def test_with_nothing_to_estimate_from_the_length_check_says_so(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "source.profile.json").write_text(json.dumps({"duration_s": 20.0}), encoding="utf-8")
            result = script_check.run_checks([{"id": "1", "text": "随便写一句。"}], script_check.read_reference(folder), None, None)
            length = [c for c in result["checks"] if c["check"] == "时长对得上"]
            self.assertEqual([c["level"] for c in length], ["note"])
            self.assertIn("--voice-id", length[0]["detail"])


class SilentScriptTests(unittest.TestCase):
    SCREENPLAY = ("# EP001 树洞里的球\n\n## EP001-SC001 外 · 文家后院 · 午后晴\n\n"
                  "[画面文字] 监控机位名：后院\n\n六个孩子在老槐树下追着一只藤球踢。\n\n[SFX] 藤球落进树洞，一声闷响。\n")

    def test_a_screenplay_nobody_speaks_in_has_nothing_to_read_aloud(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "剧本.md"
            script.write_text(self.SCREENPLAY, encoding="utf-8")
            self.assertEqual(script_check.read_script(script), [])
            # A narration draft that merely uses headings is still read whole.
            draft = Path(directory) / "旁白稿.md"
            draft.write_text("## 第一段\n\n先说结论。\n\n## 第二段\n\n再讲原因。\n", encoding="utf-8")
            self.assertEqual([zh_text.spoken(part["text"]) for part in script_check.read_script(draft)], ["先说结论", "再讲原因"])

    def test_checking_it_says_there_is_nothing_to_check_instead_of_counting_the_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            script = root / "剧本.md"
            script.write_text(self.SCREENPLAY, encoding="utf-8")
            (root / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
            checked = run_tool("script_check.py", "check", str(script), "--reference", str(root), "--voice-id", "any-voice")
            self.assertTrue(checked["ok"])
            self.assertTrue(checked["no_speech"])
            self.assertEqual(checked["chars"], 0)

    def test_a_reference_nobody_speaks_in_is_not_a_missing_transcript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "transcript.json").write_text('{"segments": []}', encoding="utf-8")
            profile = run_tool("script_check.py", "profile", str(folder))
            self.assertTrue(profile["ok"])
            self.assertTrue(profile["no_speech"])
            self.assertFalse((folder / "source.tone.json").exists())
            result = script_check.run_checks([{"id": "1", "text": "随便写一句。"}], script_check.read_reference(folder), None, None)
            note = next(c["detail"] for c in result["checks"] if c["check"] == "口气")
            self.assertIn("没有人说话", note)


class ScriptCheckTests(unittest.TestCase):
    REFERENCE = [
        (0.0, 2.0, "你以为便宜的就是划算"),
        (2.0, 3.4, "错 大错特错"),
        (3.4, 6.0, "我告诉你一个真相"),
        (6.0, 9.0, "真正决定价格的只有一样东西"),
        (9.0, 12.0, "記住 就是成本"),
        (12.0, 15.0, "你买的每一件打折货"),
        (15.0, 18.0, "商家其实一定不亏"),
        (18.0, 21.0, "所以别再盯着折扣看"),
        (21.0, 24.0, "你必须先问一句成本是多少"),
        (24.0, 27.0, "这才是关键"),
    ]

    def check(self, text: str, target: float = 27.0, pace: object = None) -> dict:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_reference(folder, self.REFERENCE, 27.0)
            sections = [{"id": "1", "text": text}]
            return script_check.run_checks(sections, script_check.read_reference(folder), pace, target)  # type: ignore[arg-type]

    def level(self, result: dict, name: str) -> str:
        found = [c["level"] for c in result["checks"] if c["check"] == name]
        self.assertEqual(len(found), 1, f"{name} reported {len(found)} times")
        return found[0]

    def test_reference_tone_is_read_through_traditional_characters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            write_reference(folder, self.REFERENCE, 27.0)
            tone = script_check.reference_tone(script_check.read_reference(folder))
            self.assertIn("记住", tone["assert_words"])
            self.assertEqual(tone["you"], 4)
            self.assertEqual(tone["lines"], 10)

    def test_a_script_in_the_references_voice_passes_the_tone_checks(self) -> None:
        text = ("你以为大牌的就是好用？错了。我告诉你，真正决定好不好用的只有成分。"
                "记住，你花的每一分钱，一定有一半付给了广告。所以别再看包装，你必须先看成分表。这才是关键。")
        result = self.check(text)
        for name in ("不句句打折扣", "敢下判断", "对着观众说", "没有照搬"):
            self.assertEqual(self.level(result, name), "pass", name)

    def test_a_script_that_hedges_every_claim_fails_against_a_blunt_reference(self) -> None:
        text = ("大牌的东西可能好用，也可能不一定。通常来说，成分或许更重要，但也不能直接下结论。"
                "一般来说，广告费往往占了一部分，至少暂时没有证据说明它占一半。单凭包装还不足以判断，不妨再观察。")
        result = self.check(text)
        self.assertEqual(self.level(result, "不句句打折扣"), "fail")
        self.assertEqual(self.level(result, "敢下判断"), "fail")
        self.assertEqual(self.level(result, "对着观众说"), "fail")
        self.assertIn("不句句打折扣", result["failed"])

    def test_a_copied_line_is_reported_even_through_marks_and_traditional_characters(self) -> None:
        text = "先说结论。真正决定价格的，只有一样东西：成本。你听明白了吗？"
        result = self.check(text)
        self.assertEqual(self.level(result, "没有照搬"), "fail")
        runs = script_check.copied_runs(text, "真正決定價格的只有一樣東西")
        self.assertEqual(runs, ["真正决定价格的只有一样东西"])

    def test_stock_disclaimers_shared_with_the_reference_are_not_copying(self) -> None:
        self.assertEqual(script_check.copied_runs("最后提醒：投资有风险，入市需谨慎。", "投资有风险入市需谨慎"), [])

    def test_length_fails_with_a_known_voice_and_only_advises_without_one(self) -> None:
        text = "你以为便宜就是划算？错了。" * 12
        without_voice = self.check(text)
        self.assertEqual(self.level(without_voice, "时长对得上"), "note")
        self.assertNotIn("时长对得上", without_voice["failed"])
        with tempfile.TemporaryDirectory() as directory:
            pace = voice_pace.estimate([text], "any-voice", 1.0, store=Path(directory) / "none.json")
        with_voice = self.check(text, pace=pace)
        self.assertEqual(self.level(with_voice, "时长对得上"), "fail")
        self.assertGreater(with_voice["estimate_s"], with_voice["target_s"] * 1.1)
        fitting = self.check(text, target=pace["total_s"], pace=pace)
        self.assertEqual(self.level(fitting, "时长对得上"), "pass")

    def test_a_script_a_little_too_long_is_told_how_much_to_cut(self) -> None:
        text = "你以为便宜就是划算？错了。" * 12
        heard_before = {"total_s": 100.0, "basis": "history", "speed": 1.0, "typical_chars_per_second": 4.5}
        self.assertEqual(self.level(self.check(text, target=97.0, pace=heard_before), "时长对得上"), "pass")       # +3%
        leaning = self.check(text, target=92.6, pace=heard_before)                                              # +8%
        self.assertEqual(self.level(leaning, "时长对得上"), "note")
        detail = next(c["detail"] for c in leaning["checks"] if c["check"] == "时长对得上")
        self.assertRegex(detail, r"删掉约 \d+ 字")
        self.assertNotIn("时长对得上", leaning["failed"])
        self.assertEqual(self.level(self.check(text, target=88.0, pace=heard_before), "时长对得上"), "fail")       # +14%
        self.assertEqual(self.level(self.check(text, target=115.0, pace=heard_before), "时长对得上"), "fail")      # -13%
        # Going by one audition line, narration tends to come out longer than estimated: no leeway there.
        one_line = dict(heard_before, basis="audition")
        self.assertEqual(self.level(self.check(text, target=92.6, pace=one_line), "时长对得上"), "fail")

    def test_a_required_closing_notice_is_not_the_script_hedging(self) -> None:
        closing = "本片图表仅作教学示意，不构成投资建议。投资有风险，入市需谨慎。"
        self.assertEqual(script_check.tone("先说结论。" + closing)["hedges"], 0)
        self.assertEqual(script_check.tone("这可能不构成理由。")["hedges"], 2)

    def test_every_check_reports_once_whatever_it_found(self) -> None:
        text = "你以为大牌的就是好用？错了。我告诉你，真正决定好不好用的只有成分。"
        names = [c["check"] for c in self.check(text)["checks"]]
        self.assertEqual(sorted(names), sorted(["时长对得上", "没有照搬", "不句句打折扣", "敢下判断", "对着观众说", "句子不拖", "开头够快"]))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            # A reference that neither asserts nor addresses the viewer: those two checks say so and pass.
            write_reference(folder, [(0.0, 3.0, "今天天气不错"), (3.0, 6.0, "我们出去走走"), (6.0, 9.0, "路上人不多")], 9.0)
            result = script_check.run_checks([{"id": "1", "text": "外面风很大。先回家吧。"}], script_check.read_reference(folder), None, 9.0)
            levels = {c["check"]: c["level"] for c in result["checks"]}
            self.assertEqual((levels["敢下判断"], levels["对着观众说"]), ("pass", "pass"))

    def test_without_a_transcript_tone_is_reported_as_not_comparable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "source.profile.json").write_text(json.dumps({"duration_s": 20.0}), encoding="utf-8")
            result = script_check.run_checks([{"id": "1", "text": "随便写一句。"}], script_check.read_reference(folder), None, None)
            self.assertEqual(result["failed"], [])
            self.assertIn("口气", result["notes"])
            self.assertIsNone(result["reference"])


class ScriptCheckCommandTests(unittest.TestCase):
    def test_profile_budget_and_check_run_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "R01"
            reference.mkdir()
            write_reference(reference, ScriptCheckTests.REFERENCE, 27.0)
            store = root / "pace.json"

            profile = run_tool("script_check.py", "profile", str(reference))
            self.assertTrue(profile["ok"])
            self.assertTrue((reference / "source.tone.json").is_file())
            self.assertEqual(profile["lines"], 10)

            budget = run_tool("script_check.py", "--store", str(store), "budget", str(reference), "--cuts", "0,9,21")
            self.assertTrue(budget["ok"])
            self.assertEqual([row["section"] for row in budget["sections"]], [1, 2, 3])
            self.assertEqual(budget["sections"][-1]["end"], 27.0)
            for row in budget["sections"]:
                self.assertLess(row["new_chars_low"], row["new_chars_high"])
            self.assertEqual(sum(row["reference_lines"] for row in budget["sections"]), 10)
            # The top of a range is what exactly fills the section, less the pause before the next one.
            rate, rows = budget["chars_per_second"], budget["sections"]
            self.assertEqual(rows[-1]["new_chars_high"], int(rows[-1]["new_seconds"] * rate))
            self.assertEqual(rows[0]["new_chars_high"], int((rows[0]["new_seconds"] - 0.3) * rate))
            self.assertEqual(budget["total_chars_high"], sum(row["new_chars_high"] for row in rows))

            script = root / "旁白.txt"
            script.write_text("你以为大牌的就是好用？错了。\n\n我告诉你，真正决定好不好用的只有成分。记住这一点。\n", encoding="utf-8")
            checked = run_tool("script_check.py", "--store", str(store), "check", str(script), "--reference", str(reference))
            self.assertTrue(checked["ok"])
            report = json.loads(Path(checked["report"]).read_text(encoding="utf-8"))
            self.assertEqual([s["id"] for s in report["script"]["sections"]], ["1", "2"])
            self.assertEqual(checked["failed"], report["failed"])

    def test_profile_says_so_when_no_lines_were_recognised(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_tool("script_check.py", "profile", directory)
            self.assertFalse(result["ok"])
            self.assertTrue(result["need_transcript"])


if __name__ == "__main__":
    unittest.main()
