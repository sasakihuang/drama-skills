"""The remake skill's animation plumbing: turning a film description and a timetable into timed
scenes, captions and effect cues, and refusing the renderer commands that open windows."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SCRIPTS = SUITE / "skills/short-drama-remake/scripts"
sys.path.insert(0, str(SCRIPTS))

import animation  # noqa: E402

TABLE = {
    "total_s": 20.0,
    "segments": [
        {"id": "A", "start": 0.0, "end": 8.0, "sentences": [
            {"start": 0.05, "end": 2.0, "text": "第一句。"},
            {"start": 2.5, "end": 5.0, "text": "第二句。"},
            {"start": 5.5, "end": 7.9, "text": "第三句。"}]},
        {"id": "B", "start": 8.3, "end": 20.0, "sentences": [
            {"start": 8.35, "end": 12.0, "text": "第四句。"},
            {"start": 12.6, "end": 19.9, "text": "第五句。"}]},
    ],
    "captions": [
        {"start": 0.05, "end": 2.0, "text": "第一句"},
        {"start": 2.5, "end": 5.0, "text": "第二句"},
        {"start": 12.6, "end": 19.9, "text": "第五句"},
    ],
}


def run_tool(*arguments: str, home: Path) -> dict:
    env = dict(os.environ, DRAMA_SKILLS_HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    done = subprocess.run([sys.executable, "-B", str(SCRIPTS / "animation.py"), *arguments], env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"animation.py printed nothing; stderr: {done.stderr}")
    return json.loads(lines[-1])


class ResolveTests(unittest.TestCase):
    def test_scenes_hand_over_without_gaps_and_cover_the_whole_film(self) -> None:
        film = {"scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "statement", "segment": 1, "from": "s2", "lines": ["一行", "两行"]},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}
        resolved = animation.resolve(film, TABLE, 0.8)
        spans = [(scene["start"], scene["end"]) for scene in resolved["scenes"]]
        self.assertEqual(spans, [(0.0, 2.5), (2.5, 8.3), (8.3, 20.8)])
        self.assertEqual(resolved["total"], 20.8)
        self.assertEqual(resolved["_uncovered"], [])

    def test_a_films_length_is_a_whole_number_of_frames(self) -> None:
        # A length that ends between two frames makes the renderer encode the sound a second time.
        self.assertEqual(animation.whole_frames(40.217), 40.233333)
        self.assertEqual(animation.whole_frames(41.0), 41.0)
        self.assertEqual(animation.whole_frames(20.8), 20.8)
        film = {"scenes": [{"type": "title", "segment": [1, 2], "text": "标题"}]}
        for tail in (0.0, 0.217, 0.8, 1.0 / 3):
            total = animation.resolve(film, TABLE, tail)["total"]
            frames = total * animation.FPS
            self.assertGreaterEqual(total, 20.0 + tail - 1e-6)
            self.assertLess(total, 20.0 + tail + 1.0 / animation.FPS)
            # Written a hair under the frame count when it does not divide evenly, never over it:
            # a reader that rounds up must not count one frame more.
            self.assertLessEqual(frames, round(frames) + 1e-9)
            self.assertLess(round(frames) - frames, 1e-4)

    def test_cues_resolve_to_sentences_seconds_and_shares_of_the_scene(self) -> None:
        film = {"scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "kline", "segment": 2, "focus": [{"bar": "key", "at": "s2", "note": "看这里"}],
             "lines": [{"price": "support", "at": 1.5}]},
        ]}
        scene = animation.resolve(film, TABLE, 0.0)["scenes"][1]
        self.assertEqual(scene["focus"][0]["at"], 12.6)
        self.assertEqual(scene["lines"][0]["at"], 9.8)
        number = {"scenes": [{"type": "number", "segment": [1, 2], "value": 3, "at": "50%"}]}
        self.assertEqual(animation.resolve(number, TABLE, 0.0)["scenes"][0]["at"], 10.0)

    def test_sentence_cues_count_from_where_the_scene_itself_starts(self) -> None:
        film = {"scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "points", "segment": 1, "from": "s2", "items": [{"title": "甲", "at": "s1"}, {"title": "乙", "at": "s2"}]},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}
        items = animation.resolve(film, TABLE, 0.0)["scenes"][1]["items"]
        self.assertEqual([item["at"] for item in items], [2.55, 5.5])

    def test_a_cue_can_quote_the_narration_and_lands_when_it_is_said(self) -> None:
        film = {"scenes": [
            {"type": "points", "segment": 1, "items": [{"title": "甲", "at": "第二句"}, {"title": "乙", "at": "二句"}]},
            {"type": "kline", "segment": 2, "focus": [{"at": "第五句", "until": "五句", "note": "看这里"}]},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        first, second = scenes[0]["items"]
        self.assertEqual(first["at"], 2.5)                       # the caption line starts being said at 2.5
        self.assertAlmostEqual(second["at"], 2.5 + 2.5 / 3, places=2)   # one character of three into the line
        self.assertEqual(scenes[1]["focus"][0]["at"], 12.6)
        self.assertGreater(scenes[1]["focus"][0]["until"], 12.6)

    def test_a_scene_can_start_on_words_in_the_middle_of_a_stretch(self) -> None:
        film = {"scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "statement", "segment": 1, "from": "二句", "lines": [{"text": "一行", "at": "s1"}]},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        handover = round(2.5 + 2.5 / 3, 3)
        self.assertEqual((scenes[0]["end"], scenes[1]["start"]), (handover, handover))
        # "s1" counts the sentences that begin while this scene is on screen: here, the third.
        self.assertEqual(scenes[1]["lines"][0]["at"], 5.5)

    def test_words_the_voice_does_not_say_in_that_scene_are_refused_with_what_it_does_say(self) -> None:
        elsewhere = {"scenes": [
            {"type": "points", "segment": 1, "items": [{"title": "甲", "at": "第五句"}]},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}
        with self.assertRaises(animation.Problem) as refused:
            animation.resolve(elsewhere, TABLE, 0.0)
        self.assertIn("第一句第二句", str(refused.exception))
        with self.assertRaises(animation.Problem):
            animation.resolve({"scenes": [{"type": "title", "segment": 1, "from": "没说过的话", "text": "标题"}]}, TABLE, 0.0)
        with self.assertRaises(animation.Problem):      # no timetable, nothing to find the words in
            animation.resolve({"scenes": [{"type": "number", "seconds": 5, "value": 1, "at": "第二句"}]}, None, 0.0)

    def test_a_scene_that_starts_or_ends_inside_a_line_hears_exactly_its_part_of_it(self) -> None:
        table = dict(TABLE, captions=[{"start": 0.0, "end": 8.0, "text": "一二三四五六七八"},
                                      {"start": 8.35, "end": 19.9, "text": "后一段的话"}])
        film = {"scenes": [
            {"type": "points", "segment": 1, "items": [{"title": "前半", "at": "二三"}]},
            {"type": "points", "segment": 1, "from": "五六", "items": [{"title": "后半", "at": "五六"}, {"title": "还在这一行", "at": "七八"}]},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}
        scenes = animation.resolve(film, table, 0.0)["scenes"]
        self.assertEqual(scenes[1]["start"], 4.0)
        self.assertEqual([item["at"] for item in scenes[1]["items"]], [4.05, 6.0])
        # Words the voice only gets to after the scene has left are not this scene's to cue.
        late = {"scenes": [film["scenes"][0] | {"items": [{"title": "说晚了", "at": "七八"}]}] + film["scenes"][1:]}
        with self.assertRaises(animation.Problem) as refused:
            animation.resolve(late, table, 0.0)
        self.assertIn("一二三四", str(refused.exception))

    def test_cards_bars_and_lines_keep_the_order_they_were_written_in(self) -> None:
        film = {"scenes": [
            {"type": "bars", "segment": 1, "emphasize": 3,
             "items": [{"label": "二十天平均", "value": 1, "at": 5.0}, {"label": "昨天", "value": 2, "at": 3.5}, {"label": "今天", "value": 3, "at": 2.0}]},
            {"type": "kline", "segment": 2, "focus": [{"bar": "key+2", "at": 6.0}, {"bar": "key", "at": 2.0}]},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        self.assertEqual([item["label"] for item in scenes[0]["items"]], ["二十天平均", "昨天", "今天"])
        self.assertEqual([focus["bar"] for focus in scenes[1]["focus"]], ["key", "key+2"], "the camera goes in the order of time")

    def test_until_is_timed_whether_or_not_at_was_written(self) -> None:
        film = {"scenes": [{"type": "kline", "segment": 2, "focus": [
            {"bar": "key", "note": "第一处", "until": "第五句"}, {"bar": "key+4", "note": "第二处", "at": 9.0, "until": 11.0}]}]}
        focus = animation.resolve(film, TABLE, 0.0)["scenes"][0]["focus"]
        self.assertEqual([type(entry["until"]) for entry in focus], [float, float])
        self.assertEqual(focus[0]["until"], 12.6)
        backwards = {"scenes": [{"type": "kline", "segment": 2, "focus": [{"bar": "key", "at": 6.0, "until": 3.0}]}]}
        with self.assertRaises(animation.Problem):
            animation.resolve(backwards, TABLE, 0.0)

    def test_untimed_items_fall_on_sentence_starts_in_order(self) -> None:
        film = {"scenes": [{"type": "points", "segment": [1, 2], "items": ["甲", "乙", "丙", "丁", "戊"]}]}
        items = animation.resolve(film, TABLE, 0.0)["scenes"][0]["items"]
        self.assertEqual([item["title"] for item in items], ["甲", "乙", "丙", "丁", "戊"])
        self.assertEqual([item["at"] for item in items], [0.05, 2.5, 5.5, 8.35, 12.6])
        crowded = {"scenes": [{"type": "points", "segment": 2, "items": ["甲", "乙", "丙", "丁"]}]}
        times = [item["at"] for item in animation.resolve(crowded, TABLE, 0.0)["scenes"][0]["items"]]
        self.assertEqual(times, sorted(times))
        self.assertTrue(all(0.0 <= t < 20.0 for t in times))
        self.assertEqual(len(set(times)), 4)

    def test_every_scene_type_the_kit_draws_can_be_timed(self) -> None:
        kit = (animation.KIT / "lib" / "kit.js").read_text(encoding="utf-8")
        drawn = set(re.findall(r"(\w+): \w+Scene", kit[kit.index("var builders = {"):]))
        self.assertEqual(drawn | {"custom"}, set(animation.SCENE_NEEDS), "kit.js and animation.py disagree on the scene types")
        samples = {
            "title": {"text": "标题"}, "kline": {}, "points": {"items": ["甲", "乙"]}, "number": {"value": 3},
            "bars": {"items": [{"label": "甲", "value": 1}, {"label": "乙", "value": 2}]},
            "compare": {"left": {"title": "左"}, "right": {"title": "右"}}, "statement": {"lines": ["一行"]},
            "end": {"text": "结尾"}, "custom": {"fn": "mine"},
        }
        self.assertEqual(set(samples), set(animation.SCENE_NEEDS))
        for kind, fields in samples.items():
            with self.subTest(type=kind):
                scene = animation.resolve({"scenes": [dict(fields, type=kind, segment=[1, 2])]}, TABLE, 0.0)["scenes"][0]
                self.assertEqual((scene["start"], scene["end"]), (0.0, 20.0))
                for entry in scene.get("items", []) + scene.get("lines", []):
                    self.assertIsInstance(entry["at"], float)

    def test_the_example_film_shipped_with_the_kit_builds(self) -> None:
        film = json.loads((animation.KIT / "film.example.json").read_text(encoding="utf-8"))
        resolved = animation.resolve(film, None, 0.8)
        self.assertGreaterEqual(len({scene["type"] for scene in resolved["scenes"]}), 6)
        self.assertEqual(resolved["scenes"][0]["type"], "title")
        self.assertTrue(animation.effects_for(resolved["scenes"]))

    def test_captions_stay_until_the_next_line_or_a_moment_after_the_voice(self) -> None:
        film = {"scenes": [{"type": "end", "segment": [1, 2], "text": "结尾"}]}
        captions = animation.resolve(film, TABLE, 0.8)["captions"]
        self.assertEqual([c["text"] for c in captions], ["第一句", "第二句", "第五句"])
        self.assertAlmostEqual(captions[0]["end"], 2.35)       # a moment after the voice
        self.assertAlmostEqual(captions[1]["end"], 5.35)
        self.assertLessEqual(captions[2]["end"], 20.8)
        tight = dict(TABLE, captions=[{"start": 1.0, "end": 2.0, "text": "前"}, {"start": 2.1, "end": 3.0, "text": "后"}])
        self.assertAlmostEqual(animation.resolve(film, tight, 0.0)["captions"][0]["end"], 2.08)  # gives way to the next

    def test_a_film_without_a_timetable_runs_on_its_own_seconds(self) -> None:
        film = {"scenes": [{"type": "title", "seconds": 2.5, "text": "标题"}, {"type": "end", "seconds": 3, "text": "结尾"}]}
        resolved = animation.resolve(film, None, 0.5)
        self.assertEqual([(s["start"], s["end"]) for s in resolved["scenes"]], [(0.0, 2.5), (2.5, 6.0)])
        self.assertEqual(resolved["captions"], [])

    def test_segments_no_scene_covers_are_reported(self) -> None:
        film = {"scenes": [{"type": "title", "segment": 2, "text": "标题"}]}
        self.assertEqual(animation.resolve(film, TABLE, 0.0)["_uncovered"], [1])

    def test_a_film_that_cannot_be_timed_is_refused(self) -> None:
        broken = {
            "unknown type": [{"type": "hologram", "segment": 1}],
            "bar without a number": [{"type": "bars", "segment": 1, "items": [{"label": "甲", "value": "很多"}]}],
            "bar without a label": [{"type": "bars", "segment": 1, "items": [{"value": 3}]}],
            "missing field": [{"type": "title", "segment": 1}],
            "segment out of range": [{"type": "title", "segment": 5, "text": "标题"}],
            "sentence that does not exist": [{"type": "kline", "segment": 2, "focus": [{"at": "s9"}]}],
            "unreadable cue": [{"type": "number", "segment": 1, "value": 1, "at": "soon"}],
            "no place in time": [{"type": "title", "text": "标题"}],
            "scenes out of order": [{"type": "end", "segment": 2, "text": "尾"}, {"type": "title", "segment": 1, "text": "头"}],
            "segment without a timetable": None,
        }
        for name, scenes in broken.items():
            with self.subTest(case=name), self.assertRaises(animation.Problem):
                if scenes is None:
                    animation.resolve({"scenes": [{"type": "title", "segment": 1, "text": "标题"}]}, None, 0.0)
                else:
                    animation.resolve({"scenes": scenes}, TABLE, 0.0)
        with self.assertRaises(animation.Problem):
            animation.resolve({"scenes": []}, TABLE, 0.0)

    def test_more_films_the_kit_could_not_draw_are_refused(self) -> None:
        bar = {"label": "甲", "value": 1}
        broken = {
            "a scene the next one replaces at once": [{"type": "title", "segment": 1, "text": "标题"},
                                                      {"type": "end", "segment": 1, "from": "第一句", "text": "尾"}],
            "one focus written without its list": [{"type": "kline", "segment": 1, "focus": {"bar": "key", "at": 1.0}}],
            "a list item that is not an object": [{"type": "kline", "segment": 1, "focus": ["key"]}],
            "a bar below zero": [{"type": "bars", "segment": 1, "items": [bar, {"label": "乙", "value": -5}]}],
            "a fourth bar lit out of three": [{"type": "bars", "segment": 1, "emphasize": 4, "items": [bar, bar, bar]}],
            "a light backdrop": None,
        }
        for name, scenes in broken.items():
            with self.subTest(case=name), self.assertRaises(animation.Problem):
                if scenes is None:
                    animation.resolve({"palette": {"bg": "#f7f5ef"}, "scenes": [{"type": "title", "segment": 1, "text": "标题"}]}, TABLE, 0.0)
                else:
                    animation.resolve({"scenes": scenes}, TABLE, 0.0)
        dark = animation.resolve({"palette": {"bg": "#0b1020"}, "scenes": [{"type": "title", "segment": 1, "text": "标题"}]}, TABLE, 0.0)
        self.assertEqual(dark["palette"]["bg"], "#0b1020")

    def test_a_custom_scene_gets_its_own_cue_lists_timed(self) -> None:
        film = {"scenes": [{"type": "custom", "fn": "mine", "segment": 1, "at": "第二句",
                            "steps": [{"name": "甲", "at": "第二句"}, {"name": "乙", "at": 6.0}],
                            "table": [{"名": "不带时间的数据"}], "plain": [1, 2, 3]}]}
        scene = animation.resolve(film, TABLE, 0.0)["scenes"][0]
        self.assertEqual(scene["at"], 2.5)
        self.assertEqual([step["at"] for step in scene["steps"]], [2.5, 6.0])
        self.assertEqual(scene["table"], [{"名": "不带时间的数据"}], "a list with no cue in it is left as it was")
        self.assertEqual(scene["plain"], [1, 2, 3])

    def test_a_chart_the_kit_would_draw_wrongly_is_refused_by_name(self) -> None:
        good_rows = [[10, 10.5, 9.8, 10.2, 30]] * 6
        broken = {
            "a shape the kit does not know": {"shape": "rocket"},
            "too few candles": {"data": good_rows[:3]},
            "a candle whose high is under its close": {"data": good_rows[:5] + [[10, 10.1, 9.8, 10.4, 30]]},
            "a candle with four numbers": {"data": good_rows[:5] + [[10, 10.5, 9.8, 10.2]]},
            "a bar named in a way the kit does not read": {"focus": [{"bar": "kye+3", "at": 1.0}]},
            "a run of bars with one end": {"focus": [{"bars": ["key+1"], "at": 1.0}]},
            "a point that is not on a candle": {"focus": [{"bar": "key", "point": "middle", "at": 1.0}]},
            "a line at no price": {"lines": [{"at": 1.0, "label": "上沿"}]},
            "a line at a price that is not one": {"lines": [{"price": "ceiling", "at": 1.0}]},
            "a show the kit does not read": {"show": "half"},
        }
        for name, fields in broken.items():
            with self.subTest(case=name), self.assertRaises(animation.Problem):
                animation.resolve({"scenes": [dict(fields, type="kline", segment=1)]}, TABLE, 0.0)
        with self.assertRaises(animation.Problem):
            animation.resolve({"scenes": [{"type": "compare", "segment": 1, "left": {"shape": "sideways"}, "right": {}}]}, TABLE, 0.0)
        with self.assertRaises(animation.Problem):      # nothing before it to carry on from
            animation.resolve({"scenes": [{"type": "kline", "segment": 1, "continue": True}]}, TABLE, 0.0)
        carried = animation.resolve({"scenes": [{"type": "kline", "segment": 1, "show": "key"},
                                                {"type": "kline", "segment": 2, "continue": True}]}, TABLE, 0.0)
        self.assertTrue(carried["scenes"][1]["continue"])
        fine = {"type": "kline", "segment": 1, "data": good_rows, "key": 4, "show": "key+1",
                "lines": [{"price": "support", "to": 10.4, "at": 1.0}],
                "focus": [{"bars": ["key-2", "last"], "at": 2.0}, {"bar": 3, "point": "volume", "at": 4.0}]}
        self.assertEqual(len(animation.resolve({"scenes": [fine]}, TABLE, 0.0)["scenes"][0]["focus"]), 2)


class EffectTests(unittest.TestCase):
    def test_effects_mark_scene_changes_and_cues_and_never_crowd(self) -> None:
        film = {"scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "points", "segment": 1, "from": "s2", "items": [{"title": "甲", "at": "s1"}, {"title": "乙", "at": 0.2}]},
            {"type": "kline", "segment": 2, "focus": [{"at": "s2", "note": "看这里"}]},
        ]}
        effects = animation.effects_for(animation.resolve(film, TABLE, 0.0)["scenes"])
        times = [e["at"] for e in effects]
        self.assertEqual(times, sorted(times))
        self.assertEqual((times[0], effects[0]["file"]), (0.44, "impact-bass-1.mp3"), "the slam lands with the title")
        self.assertNotIn("riser.mp3", [e["file"] for e in effects], "a title this short cannot hold the rise")
        self.assertTrue(all(b - a >= 0.35 for a, b in zip(times, times[1:])), times)
        self.assertTrue(any(abs(t - 12.5) < 0.01 for t in times), "the note landing on the focus cue has a sound")
        self.assertTrue(any(abs(t - 8.15) < 0.01 for t in times), "the cut into the last scene has a sound")
        self.assertTrue(all(e["file"].endswith(".mp3") and 0 < e["volume"] <= 1 for e in effects))

    def test_the_rise_crests_as_the_title_gives_way(self) -> None:
        # The rise is silent where it starts and loudest RISER_CREST seconds in. Started with the
        # title, its loudest moment fell in the middle of the opening sentence.
        film = {"scenes": [{"type": "title", "seconds": 6.0, "text": "标题"}, {"type": "end", "seconds": 3, "text": "完"}]}
        effects = animation.effects_for(animation.resolve(film, None, 0.0)["scenes"])
        rise = [e for e in effects if e["file"] == "riser.mp3"]
        self.assertEqual([e["at"] for e in rise], [round(6.0 - animation.RISER_CREST, 2)])
        # Starting beside the slam it neither pushes the slam out nor is pushed out by it.
        close = {"scenes": [{"type": "title", "seconds": 0.44 + animation.RISER_CREST + 0.1, "text": "标题"},
                            {"type": "end", "seconds": 3, "text": "完"}]}
        files = [e["file"] for e in animation.effects_for(animation.resolve(close, None, 0.0)["scenes"])]
        self.assertIn("riser.mp3", files)
        self.assertIn("impact-bass-1.mp3", files)

    def test_no_effect_is_asked_to_be_louder_than_the_voice(self) -> None:
        film = {"scenes": [
            {"type": "title", "seconds": 5, "text": "标题"},
            {"type": "kline", "seconds": 6, "focus": [{"at": 2.0, "note": "看这里"}]},
            {"type": "compare", "seconds": 6, "left": {"title": "甲", "verdict": "走高", "at": 2.0},
             "right": {"title": "乙", "verdict": "收回", "at": 4.0}},
            {"type": "number", "seconds": 5, "value": 3},
            {"type": "statement", "seconds": 5, "lines": ["一行", "两行"]},
            {"type": "bars", "seconds": 5, "items": [{"label": "甲", "value": 1}, {"label": "乙", "value": 2}]},
        ]}
        effects = animation.effects_for(animation.resolve(film, None, 0.0)["scenes"], [(0.0, 400.0)])
        self.assertGreaterEqual(len({e["file"] for e in effects}), 6)
        self.assertTrue(all(0.3 <= e["volume"] <= 0.7 for e in effects), effects)

    def test_a_busy_stretch_keeps_the_sounds_that_carry_meaning(self) -> None:
        # Twelve list items and one note inside twelve seconds of narration: every cue could have a
        # sound, and a film where they all do is noise.
        items = [{"title": f"第{n}条", "at": 0.3 + n * 0.5} for n in range(12)]
        film = {"scenes": [
            {"type": "points", "segment": 1, "items": items},
            {"type": "kline", "segment": 2, "focus": [{"at": 4.0, "note": "看这里"}]},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        stretches = [(segment["start"], segment["end"]) for segment in TABLE["segments"]]
        effects = animation.effects_for(scenes, stretches)
        first = [e for e in effects if e["at"] < 8.0]
        self.assertLessEqual(len(first), max(2, round(8.0 / animation.EFFECT_EVERY)))
        self.assertGreaterEqual(len(first), 2)
        second = [e["file"] for e in effects if e["at"] >= 8.0]
        self.assertIn("pop.mp3", second, "the note landing is kept")
        self.assertIn("whoosh.mp3", second, "so is the change of scene")
        everything = animation.effects_for(scenes, [(0.0, 400.0)])
        self.assertGreater(len(everything), len(effects), "a stretch long enough keeps them all")


def clip(path: Path, *, gain: float, video: bool = False, seconds: int = 3) -> None:
    """A tone at `gain` dB beside the generator's own level, with a blank picture when asked for one."""
    command = ["ffmpeg", "-v", "error", "-y"]
    if video:
        command += ["-f", "lavfi", "-i", f"color=c=black:s=64x64:r=30:d={seconds}"]
    command += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}", "-af", f"volume={gain}dB", "-ar", "48000", "-ac", "2"]
    command += ["-c:v", "mpeg4", "-c:a", "aac", "-b:a", "192k", "-shortest"] if video else []
    subprocess.run(command + [str(path)], check=True)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "needs ffmpeg")
class RenderedSoundTests(unittest.TestCase):
    """The renderer has turned a film down by 8 dB over a stray spike in its own encoding."""

    def test_a_film_that_came_out_quiet_gets_its_mix_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root / "mix.wav", gain=6)
            clip(root / "film.mp4", gain=-2, video=True)
            report = animation.keep_the_mix(root / "film.mp4", root / "mix.wav")
            self.assertAlmostEqual(report["restored_db"], 8.0, delta=0.6)
            wanted, _ = animation.sound_level(root / "mix.wav")
            got, peak = animation.sound_level(root / "film.mp4")
            self.assertAlmostEqual(got, wanted, delta=0.5)
            self.assertAlmostEqual(report["film_lufs"], got, delta=0.1)
            self.assertLessEqual(peak, animation.DELIVERY_PEAK_DB)
            streams = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                                      str(root / "film.mp4")], capture_output=True, text=True, check=True).stdout.split()
            self.assertEqual(sorted(streams), ["audio", "video"])
            self.assertEqual([p.name for p in root.iterdir() if "sound" in p.name], [], "nothing left behind")

    def test_a_film_as_loud_as_its_mix_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root / "mix.wav", gain=6)
            clip(root / "film.mp4", gain=5.5, video=True)
            before = (root / "film.mp4").read_bytes()
            report = animation.keep_the_mix(root / "film.mp4", root / "mix.wav")
            self.assertNotIn("restored_db", report)
            self.assertNotIn("lowered_db", report)
            self.assertEqual((root / "film.mp4").read_bytes(), before)

    def test_a_mix_too_hot_to_deliver_is_reported_not_put_back(self) -> None:
        # The renderer was right to turn this one down: its peaks sit at full scale (the tone is
        # pushed past it and clips).
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root / "mix.wav", gain=26)
            clip(root / "film.mp4", gain=14, video=True)
            self.assertGreater(animation.sound_level(root / "mix.wav")[1], animation.DELIVERY_PEAK_DB)
            before = (root / "film.mp4").read_bytes()
            report = animation.keep_the_mix(root / "film.mp4", root / "mix.wav")
            self.assertNotIn("restored_db", report)
            self.assertGreater(report["lowered_db"], 3.0)
            self.assertEqual((root / "film.mp4").read_bytes(), before)
            self.assertEqual([p.name for p in root.iterdir() if "sound" in p.name], [])

    def test_a_film_with_no_sound_at_all_gets_the_mix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            clip(root / "mix.wav", gain=6)
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=30:d=3",
                            "-c:v", "mpeg4", str(root / "film.mp4")], check=True)
            report = animation.keep_the_mix(root / "film.mp4", root / "mix.wav")
            self.assertIn("restored_db", report)
            self.assertIsNone(report["restored_db"])
            self.assertIsNotNone(animation.sound_level(root / "film.mp4")[0])


class LookingTests(unittest.TestCase):
    def scenes(self, count: int, cues: int = 0) -> list:
        made = []
        for index in range(count):
            scene = {"type": "statement", "start": index * 10.0, "end": (index + 1) * 10.0,
                     "lines": [{"text": f"第{n}行", "at": index * 10.0 + 2.0 + n * 2.0} for n in range(cues)]}
            made.append(scene)
        return made

    def test_a_short_film_is_looked_at_after_each_thing_appears(self) -> None:
        times = animation.snapshot_times(self.scenes(4, cues=3), 40.0)
        for index in range(4):
            inside = [t for t in times if index * 10.0 < t < (index + 1) * 10.0]
            self.assertGreaterEqual(len(inside), 3, (index, inside))
            for cue in (2.0, 4.0, 6.0):
                self.assertTrue(any(0.3 <= t - (index * 10.0 + cue) <= 0.9 for t in inside), (index, cue, inside))

    def test_a_long_film_is_looked_at_in_every_scene_to_the_end(self) -> None:
        for count in (30, 50, 120):
            with self.subTest(scenes=count):
                times = animation.snapshot_times(self.scenes(count, cues=3), count * 10.0)
                self.assertLessEqual(len(times), animation.SNAPSHOTS)
                self.assertGreater(times[-1], (count - 2) * 10.0)
                if count < animation.SNAPSHOTS:
                    for index in range(count):
                        self.assertTrue(any(index * 10.0 <= t < (index + 1) * 10.0 for t in times), index)

    def test_the_cue_list_names_what_appears_and_when(self) -> None:
        film = {"scenes": [
            {"type": "kline", "segment": 1, "header": "小标题", "lines": [{"price": "support", "at": 1.0, "label": "下沿"}],
             "focus": [{"at": "第二句", "note": "看**这里**"}]},
            {"type": "compare", "segment": 2, "left": {"title": "左", "verdict": "站住", "at": "s1"}, "right": {"title": "右"}},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        self.assertEqual(animation.scene_cues(scenes[0]),
                         [(0.0, "小标题", "小标题"), (1.0, "横线", "下沿"), (2.5, "标注", "看**这里**")])
        self.assertEqual(animation.scene_cues(scenes[1]), [(8.35, "结论章", "站住")])

    def test_a_scene_that_opens_on_an_empty_frame_is_pointed_out(self) -> None:
        film = {"scenes": [
            {"type": "statement", "segment": 1, "lines": [{"text": "来得晚", "at": "第二句"}]},        # nothing for 2.5 s
            {"type": "statement", "segment": 2, "lines": [{"text": "来得早", "at": "s1"}]},
        ]}
        scenes = animation.resolve(film, TABLE, 0.0)["scenes"]
        self.assertEqual(animation.bare_openings(scenes), [(1, 2.5)])
        headed = [dict(scenes[0], header="有小标题就不算空")]
        self.assertEqual(animation.bare_openings(headed), [])

    def test_findings_are_collected_wherever_the_report_keeps_them(self) -> None:
        report = {"ok": False, "layout": {"findings": [{"severity": "error", "message": "两块字叠在一起", "code": "content_overlap"}]},
                  "runtime": {"findings": [{"severity": "warning", "message": "慢", "code": "slow"}], "nested": {"deeper": [
                      {"severity": "info", "message": "出画", "code": "canvas_overflow"}]}}}
        self.assertEqual(sorted(f["code"] for f in animation.findings(report)), ["canvas_overflow", "content_overlap", "slow"])
        self.assertEqual(animation.findings({"ok": True}), [])


class PageTests(unittest.TestCase):
    def sizes(self, page: str) -> set:
        found = set(re.findall(r'data-width="(\d+)" data-height="(\d+)"', page))
        found |= set(re.findall(r"width=(\d+), height=(\d+)", page))
        found |= set(re.findall(r"width: (\d+)px; height: (\d+)px", page))
        # Small boxes (a spark, a dot) also state a width and height; only the frame is this big.
        return {(w, h) for w, h in found if int(w) >= 1000 or int(h) >= 1000}

    def test_the_page_states_one_size_everywhere_in_either_orientation(self) -> None:
        self.assertEqual(self.sizes(animation.page_for(False)), {("1920", "1080")})
        self.assertEqual(self.sizes(animation.page_for(True)), {("1080", "1920")})

    def test_the_kit_pins_the_versions_it_installs(self) -> None:
        for name, version in animation.pinned().items():
            with self.subTest(package=name):
                self.assertRegex(version, r"^\d+\.\d+\.\d+$")


class StyleTests(unittest.TestCase):
    """A film drawn in one of the style library's looks instead of the kit's dark one."""

    def test_the_library_ships_fifteen_styles_and_the_guide_names_every_one(self) -> None:
        styles = animation.known_styles()
        self.assertEqual(len(styles), 15)
        drawn = [name for name in styles if any((animation.STYLES / name).rglob("*.js")) or any((animation.STYLES / name).glob("*.html"))]
        self.assertEqual(sorted(set(styles) - set(drawn)), ["04-3d-render", "06-collage", "18-hanazi"],
                         "only the three styles that need Blender or photographs come without their code")
        self.assertTrue((animation.STYLES / "LICENSE").is_file())
        guide = (SUITE / "skills/short-drama-remake/references/styles.md").read_text(encoding="utf-8")
        for name in styles:
            with self.subTest(style=name):
                self.assertIn(f"| {name} |", guide)

    def test_a_styled_film_is_all_custom_and_may_be_light(self) -> None:
        light = {"style": "01-flat-vector", "palette": {"bg": "#f4efe6"}, "scenes": [{"type": "custom", "fn": "intro", "segment": 1}]}
        self.assertEqual(animation.resolve(light, TABLE, 0.0)["style"], "01-flat-vector")
        self.assertNotIn("style", animation.resolve({"scenes": [{"type": "title", "segment": 1, "text": "标题"}]}, TABLE, 0.0))
        broken = {
            "a style the library does not have": dict(light, style="99-nothing"),
            "a kit scene in a styled film": dict(light, scenes=[{"type": "title", "segment": 1, "text": "标题"}]),
            "a light backdrop without a style": {"palette": {"bg": "#f4efe6"}, "scenes": light["scenes"]},
        }
        for name, film in broken.items():
            with self.subTest(case=name), self.assertRaises(animation.Problem):
                animation.resolve(film, TABLE, 0.0)

    def test_a_custom_cue_can_carry_a_sound_and_any_scene_a_transition(self) -> None:
        film = {"scenes": [{"type": "custom", "fn": "mine", "segment": 1, "transition": "fade",
                            "beats": [{"what": "a", "at": "第二句", "sound": "sparkle.mp3"},
                                      {"what": "b", "at": 6.0, "sound": "pop.mp3", "volume": 0.3}]}]}
        resolved = animation.resolve(film, TABLE, 0.0)
        self.assertEqual(resolved["scenes"][0]["transition"], "fade")
        effects = animation.effects_for(resolved["scenes"])
        self.assertIn({"file": "sparkle.mp3", "at": 2.5, "volume": 0.5}, effects)
        self.assertIn({"file": "pop.mp3", "at": 6.0, "volume": 0.3}, effects)
        broken = {
            "a sound the library does not have": {"at": 1.0, "sound": "boom.mp3"},
            "a sound asked to be as loud as the voice": {"at": 1.0, "sound": "pop.mp3", "volume": 1.0},
        }
        for name, entry in broken.items():
            with self.subTest(case=name), self.assertRaises(animation.Problem):
                animation.resolve({"scenes": [{"type": "custom", "fn": "mine", "segment": 1, "beats": [entry]}]}, TABLE, 0.0)
        with self.assertRaises(animation.Problem):
            animation.resolve({"scenes": [{"type": "title", "segment": 1, "text": "标题", "transition": "spin"}]}, TABLE, 0.0)

    def test_the_kit_offers_frame_drawing_and_a_film_wide_backdrop(self) -> None:
        kit = (SUITE / "skills/short-drama-remake/assets/motion-kit/lib/kit.js").read_text(encoding="utf-8")
        self.assertIn("frames: frames", kit)
        self.assertIn("window.CustomBackdrop", kit)
        self.assertIn("FILM.style", kit)


class CommandTests(unittest.TestCase):
    def film_folder(self, root: Path) -> Path:
        folder = root / "动画"
        folder.mkdir()
        (folder / "index.html").write_text(animation.page_for(False), encoding="utf-8")
        (folder / "film.json").write_text(json.dumps({"keywords": ["重点"], "scenes": [
            {"type": "title", "segment": 1, "text": "标题"},
            {"type": "end", "segment": 2, "text": "结尾"},
        ]}, ensure_ascii=False), encoding="utf-8")
        (root / "时间表.json").write_text(json.dumps(TABLE, ensure_ascii=False), encoding="utf-8")
        return folder

    def test_build_writes_the_timed_film_the_effect_cues_and_the_films_length(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            (root / "混音.wav").write_bytes(b"RIFF0000WAVEfmt ")
            result = run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), "--mix", str(root / "混音.wav"), home=root)
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["total_s"], 20.8)
            text = (folder / "film.js").read_text(encoding="utf-8")
            film = json.loads(text[text.index("{"):text.rindex("}") + 1])
            self.assertEqual([scene["type"] for scene in film["scenes"]], ["title", "end"])
            self.assertEqual(film["keywords"], ["重点"])
            self.assertTrue(json.loads((folder / "音效.json").read_text(encoding="utf-8")))
            page = (folder / "index.html").read_text(encoding="utf-8")
            self.assertEqual(re.findall(r'<div id="root"[^>]*data-duration="([^"]+)"', page), ["20.8"])
            self.assertEqual(len(re.findall(r'<audio id="mix"[^>]*data-duration="20.8"', page)), 1)
            self.assertEqual((folder / "mix.wav").read_bytes(), b"RIFF0000WAVEfmt ")
            # Built again with nothing about the timing changed, the film keeps its sound.
            again = run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), home=root)
            self.assertTrue(again["has_sound"])
            self.assertFalse(again["sound_taken_off"])
            self.assertEqual(len(re.findall(r'<audio id="mix"', (folder / "index.html").read_text(encoding="utf-8"))), 1)
            # A change that moves the film's timing takes the sound off and says so, instead of
            # leaving effects at the old moments or a mix.wav no page plays.
            film = json.loads((folder / "film.json").read_text(encoding="utf-8"))
            film["scenes"].insert(1, {"type": "statement", "segment": 1, "from": "s2", "lines": ["多了一个画面"]})
            (folder / "film.json").write_text(json.dumps(film, ensure_ascii=False), encoding="utf-8")
            moved = run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), home=root)
            self.assertFalse(moved["has_sound"])
            self.assertTrue(moved["sound_taken_off"])
            self.assertNotIn("<audio", (folder / "index.html").read_text(encoding="utf-8"))
            self.assertFalse((folder / "mix.wav").exists())
            fresh = run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), home=root)
            self.assertFalse(fresh["sound_taken_off"], "nothing left to take off the second time")

    def test_cues_reads_the_built_film_back_beside_the_narration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            (folder / "film.json").write_text(json.dumps({"scenes": [
                {"type": "statement", "segment": 1, "lines": [{"text": "大字", "at": "第二句"}]},
                {"type": "end", "segment": 2, "text": "结尾"},
            ]}, ensure_ascii=False), encoding="utf-8")
            self.assertFalse(run_tool("cues", str(folder), home=root)["ok"], "nothing to read before build")
            run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), home=root)
            listed = run_tool("cues", str(folder), home=root)
            self.assertTrue(listed["ok"], listed)
            line = next(row for row in listed["cues"] if row["words"] == "大字")
            self.assertEqual((line["scene"], line["at"], line["voice"]), (1, 2.5, "「第二句」"))

    def test_build_explains_a_broken_film_instead_of_writing_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            (folder / "film.json").write_text('{"scenes": [{"type": "title", "segment": 9, "text": "标题"}]}', encoding="utf-8")
            result = run_tool("build", str(folder), "--timetable", str(root / "时间表.json"), home=root)
            self.assertFalse(result["ok"])
            self.assertFalse((folder / "film.js").exists())

    def test_build_wires_custom_js_in_once_and_names_what_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            (folder / "film.json").write_text(json.dumps({"scenes": [
                {"type": "custom", "fn": "opening", "segment": 1},
                {"type": "custom", "fn": "closing", "segment": 2},
            ]}), encoding="utf-8")
            table = str(root / "时间表.json")
            missing = run_tool("build", str(folder), "--timetable", table, home=root)
            self.assertFalse(missing["ok"])
            self.assertIn("custom.js", missing["error"])
            self.assertFalse((folder / "film.js").exists())
            (folder / "custom.js").write_text("window.CustomScenes = { opening: function () {} };", encoding="utf-8")
            half = run_tool("build", str(folder), "--timetable", table, home=root)
            self.assertFalse(half["ok"])
            self.assertIn("closing", half["error"])
            (folder / "custom.js").write_text(
                "window.CustomScenes = { opening: function () {}, closing: function () {} };", encoding="utf-8")
            for _ in range(2):
                done = run_tool("build", str(folder), "--timetable", table, home=root)
                self.assertTrue(done["ok"], done)
            page = (folder / "index.html").read_text(encoding="utf-8")
            self.assertEqual(page.count('src="custom.js"'), 1, "built twice, wired in once")
            self.assertLess(page.index('src="custom.js"'), page.index('src="lib/kit.js"'))

    def test_an_upright_film_is_asked_for_when_the_folder_is_first_set_up(self) -> None:
        self.assertFalse(animation.page_is_upright(animation.page_for(False)))
        self.assertTrue(animation.page_is_upright(animation.page_for(True)))

    def test_the_editor_flag_counts_wherever_it_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            # No renderer in this made-up home, so each call stops at "run setup first"; what differs is
            # whether the command was let through to that point or refused as one that opens a window.
            refused = run_tool("tool", str(folder), "--", "preview", home=root)
            self.assertIn("弹出窗口", refused["error"])
            for arguments in (["tool", str(folder), "--creator-asked-for-editor", "--", "preview"],
                              ["tool", "--creator-asked-for-editor", str(folder), "--", "preview"]):
                with self.subTest(order=arguments[1]):
                    allowed = run_tool(*arguments, home=root)
                    self.assertNotIn("弹出窗口", allowed["error"])
                    self.assertIn("setup", allowed["error"])

    def test_commands_that_open_windows_upload_or_install_skills_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            for command in ("preview", "play", "present", "open", "publish", "skills", "auth", "cloud", "tts"):
                with self.subTest(command=command):
                    result = run_tool("tool", str(folder), "--", command, home=root)
                    self.assertFalse(result["ok"])

    def test_a_computer_without_the_renderer_is_told_to_run_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = self.film_folder(root)
            for command in (["check", str(folder)], ["render", str(folder), "--output", str(root / "out.mp4")],
                            ["tool", str(folder), "--", "docs", "gsap"]):
                with self.subTest(command=command[0]):
                    result = run_tool(*command, home=root)
                    self.assertFalse(result["ok"])
            self.assertFalse((root / "out.mp4").exists())


if __name__ == "__main__":
    unittest.main()
