"""The remake skill's covers: what a cover may say, which style and picture each of the two takes,
how the page is sized, where a poster finds its subject, and the run from a finished film to two
pictures beside it."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List
from unittest import mock

SUITE = Path(__file__).resolve().parents[1]
SCRIPTS = SUITE / "skills/short-drama-remake/scripts"
sys.path.insert(0, str(SCRIPTS))

import animation  # noqa: E402
import cover  # noqa: E402

FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


def near(colour: str, wanted: str, tolerance: int = 3) -> bool:
    """Two #rrggbb colours within a few levels of each other: a frame that went through video encoding
    keeps a flat colour only to within a level or two."""
    return all(abs(int(colour[i:i + 2], 16) - int(wanted[i:i + 2], 16)) <= tolerance for i in (1, 3, 5))


def spec(**fields: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {"film": "成片.mp4", "title": ["看见**放量**", "先问这三句"], "at": 3.0}
    base.update(fields)
    return base


class SpecTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def check(self, raw: Any, seconds: float = 40.0) -> Dict[str, Any]:
        return cover.check_spec(raw, self.folder, seconds)

    def refused(self, raw: Any, *words: str) -> None:
        with self.assertRaises(cover.Problem) as caught:
            self.check(raw)
        for word in words:
            self.assertIn(word, str(caught.exception))

    def animation_folder(self) -> Path:
        moving = self.folder / "动画"
        moving.mkdir(exist_ok=True)
        (moving / "index.html").write_text("<html></html>", encoding="utf-8")
        (moving / "film.js").write_text("window.FILM = {};", encoding="utf-8")
        return moving

    def test_a_title_reads_at_a_glance_or_is_refused_with_why(self) -> None:
        self.assertEqual(self.check(spec())["lines"], ["看见**放量**", "先问这三句"])
        self.assertEqual(self.check(spec(title="第一行\n第二行"))["lines"], ["第一行", "第二行"])
        self.refused(spec(title=[]), "还没写")
        self.refused(spec(title=None), "还没写")
        self.refused(spec(title=["一", "二", "三", "四"]), "最多 3 行")
        self.refused(spec(title=["放量以后为什么还会跌"]), "第 1 行", "10 个字宽")
        # Digits and Latin letters read half as wide as a Chinese character.
        self.assertEqual(self.check(spec(title=["3 步看懂 K 线走势"]))["lines"], ["3 步看懂 K 线走势"])
        self.refused(spec(title=["九个字的第一行文字", "九个字的第二行文字", "再来三个字"]), "一共 23 个字宽")
        self.refused(spec(title=["看见**放量"]), "没配成对")

    def test_the_lines_around_the_title_are_short_and_plain(self) -> None:
        made = self.check(spec(sub="先问这三句，再决定追不追", en="Three questions before you chase", tag="第 2 讲"))
        self.assertEqual((made["sub"], made["en"], made["tag"]), ("先问这三句，再决定追不追", "Three questions before you chase", "第 2 讲"))
        self.refused(spec(sub="这一句钩子写得实在是太长了已经超过十八个字宽了"), "sub", "18 个字宽")
        self.refused(spec(sub="**重点**"), "sub")
        self.refused(spec(en="英文这里写了中文"), "只写英文")
        self.refused(spec(en="x" * 41), "40 个字母")
        self.refused(spec(tag="**重点**"), "tag")

    def test_a_motion_film_gets_a_poster_and_footage_a_photo_unless_told(self) -> None:
        self.animation_folder()
        footage = self.check(spec())
        self.assertEqual({footage["sides"][s]["style"] for s in cover.SIDES}, {"photo"})
        motion = self.check(spec(animation="动画"))
        self.assertEqual({motion["sides"][s]["style"] for s in cover.SIDES}, {"poster"})
        mixed = self.check(spec(animation="动画", horizontal={"style": "photo"}))
        self.assertEqual((mixed["sides"]["vertical"]["style"], mixed["sides"]["horizontal"]["style"]), ("poster", "photo"))
        self.assertEqual(self.check(spec(style="poster"))["sides"]["vertical"]["style"], "poster")
        self.refused(spec(style="magazine"), "poster", "photo")

    def test_each_cover_takes_its_own_picture_or_the_shared_one(self) -> None:
        picture = self.folder / "图.png"
        picture.write_bytes(b"png")
        shared = self.check(spec())
        self.assertEqual([shared["sides"][s]["at"] for s in cover.SIDES], [3.0, 3.0])
        own = self.check(spec(horizontal={"at": 12.5, "place": "right"}))
        self.assertEqual((own["sides"]["vertical"]["at"], own["sides"]["horizontal"]["at"]), (3.0, 12.5))
        drawn = self.check(spec(vertical={"image": "图.png"}))
        self.assertEqual(drawn["sides"]["vertical"]["image"], picture.resolve())
        self.assertIsNone(drawn["sides"]["vertical"]["at"])
        self.assertEqual(drawn["sides"]["horizontal"]["at"], 3.0)
        self.refused(spec(image="图.png"), "只写一个")
        unset = self.check(spec(at=None))
        self.assertEqual([unset["sides"][s]["at"] for s in cover.SIDES], [None, None], "an AI cover needs no frame")
        self.refused(spec(at=40.0), "40 秒", "40.00 秒")
        self.refused(spec(vertical={"image": "没有这张.png"}), "找不到")

    def test_the_ai_fields_are_read_and_footage_takes_its_frame_as_reference(self) -> None:
        (self.folder / "无字幕.mp4").write_bytes(b"mp4")
        made = self.check(spec(visual="一根放量阳线被金色光圈圈住", look="黑底金色"))
        self.assertEqual((made["visual"], made["look"], made["ai"], made["reference"]), ("一根放量阳线被金色光圈圈住", "黑底金色", True, False))
        self.assertTrue(self.check(spec(clean="无字幕.mp4"))["reference"], "footage keeps its people")
        self.assertFalse(self.check(spec(clean="无字幕.mp4", reference=False))["reference"])
        sides = self.check(spec(ai=False, vertical={"ai": True}))["sides"]
        self.assertEqual((sides["vertical"]["ai"], sides["horizontal"]["ai"]), (True, False))
        self.refused(spec(ai="yes"), "true 或 false")
        self.refused(spec(vertical={"ai": 1}), "true 或 false")
        self.refused(spec(visual="很长" * 120), "visual")

    def test_a_crop_is_a_part_of_the_frame_given_as_shares_of_it(self) -> None:
        made = self.check(spec(crop=[0.05, 0.2, 0.9, 0.5], horizontal={"crop": [0, 0, 1, 1]}))
        self.assertEqual(made["sides"]["vertical"]["crop"], [0.05, 0.2, 0.9, 0.5])
        self.assertEqual(made["sides"]["horizontal"]["crop"], [0.0, 0.0, 1.0, 1.0])
        self.assertIsNone(self.check(spec())["sides"]["vertical"]["crop"], "found from the picture when not given")
        self.refused(spec(crop=[0.5, 0.5, 0.6, 0.4]), "出了画面")
        self.refused(spec(crop=[0, 0, 0.05, 1]), "至少 0.1")
        self.refused(spec(crop=[0, 0, 1]), "[左, 上, 宽, 高]")

    def test_unknown_fields_and_values_out_of_range_are_named(self) -> None:
        self.refused(spec(titel=["错字"]), "titel")
        self.refused(spec(vertical={"postion": "top"}), "postion")
        self.refused(spec(vertical={"place": "left"}), "top / middle / bottom")
        self.refused(spec(horizontal={"place": "middle"}), "left / center / right / top / bottom")
        self.refused(spec(vertical={"zoom": 5}), "1 到 3")
        self.refused(spec(vertical={"focus": [0.5]}), "[横向, 竖向]")
        self.refused(spec(vertical={"focus": [0.5, 2]}), "[横向, 竖向]")
        self.refused(spec(accent="gold"), "#ffd23f")
        self.refused(spec(font="hei"), "bold", "light", "brush")
        self.refused(spec(at="3"), "要写数字")
        (self.folder / "无字幕.mp4").write_bytes(b"mp4")
        self.assertEqual(self.check(spec(animation="动画" if self.animation_folder() else ""))["animation"],
                         (self.folder / "动画").resolve())
        self.refused(spec(animation="动画", clean="无字幕.mp4"), "只写一个")
        self.refused(spec(animation="不存在的目录"), "不是排好的动画目录")

    def test_defaults_fill_in_what_was_left_out(self) -> None:
        made = self.check(spec())
        self.assertEqual((made["font"], made["accent"], made["tag"], made["sub"], made["en"]), ("bold", cover.ACCENT, "", "", ""))
        self.assertIsNone(made["sides"]["vertical"]["place"], "the page picks the place that suits the picture")
        self.assertEqual(made["sides"]["horizontal"]["zoom"], 1.0)
        self.assertEqual(made["sides"]["horizontal"]["focus"], [0.5, 0.5])
        self.assertEqual(made["film"], (self.folder / "成片.mp4").resolve())


class LayoutTests(unittest.TestCase):
    def test_the_covers_are_the_two_shapes_douyin_asks_for(self) -> None:
        self.assertEqual(cover.SIZE, {"vertical": (1080, 1440), "horizontal": (1440, 1080)})

    def test_a_photo_that_does_not_suit_its_cover_goes_on_a_card(self) -> None:
        self.assertEqual(cover.shape_for("vertical", (1080, 1440)), "full")
        self.assertEqual(cover.shape_for("vertical", (1080, 1920)), "full", "an upright film loses a little top and bottom")
        self.assertEqual(cover.shape_for("vertical", (1920, 1080)), "card")
        self.assertEqual(cover.shape_for("horizontal", (1920, 1080)), "full")
        self.assertEqual(cover.shape_for("horizontal", (1080, 1920)), "card")
        self.assertEqual(cover.shape_for("horizontal", (1080, 1080)), "full")

    def test_covers_sit_beside_the_film_named_for_their_shape(self) -> None:
        film = Path("项目/剧集/EP001/制作成果/成片/放量阳线-草稿.mp4")
        paths = cover.cover_paths(film)
        self.assertEqual(paths["vertical"], film.parent / "放量阳线-封面-竖3比4.jpg")
        self.assertEqual(paths["horizontal"], film.parent / "放量阳线-封面-横4比3.jpg")
        self.assertEqual(cover.cover_paths(film.with_name("放量阳线-正式版.mp4")), paths)
        self.assertEqual(cover.cover_paths(Path("成片.mp4"))["vertical"].name, "成片-封面-竖3比4.jpg")

    def test_the_page_states_one_size_everywhere_on_either_cover(self) -> None:
        upright, wide = cover.page_for("vertical"), cover.page_for("horizontal")
        self.assertIn('data-width="1080" data-height="1440"', upright)
        self.assertIn('data-width="1440" data-height="1080"', wide)
        self.assertIn("width: 1440px; height: 1080px", wide)
        # The size is named in exactly four places (viewport, page, root, root's data): the layout
        # itself reads it from the page, so turning the page on its side turns all of it.
        self.assertEqual((upright.count("1080"), upright.count("1440")), (4, 4))
        self.assertEqual((wide.count("1080"), wide.count("1440")), (4, 4))

    def test_the_page_is_told_each_covers_style_picture_and_words(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        made = cover.check_spec(spec(title=["<放量> & **阳线**"], sub="一句钩子", en="A hook", tag="第 2 讲", style="poster",
                                     horizontal={"style": "photo", "place": "right", "zoom": 1.4}), folder, 40.0)
        sides = {"vertical": cover.side_data("vertical", made["sides"]["vertical"], "pic-vertical.png", (1080, 1920),
                                             [0.0, 0.2, 1.0, 0.45], "#060709"),
                 "horizontal": cover.side_data("horizontal", made["sides"]["horizontal"], "pic-horizontal.png", (1080, 1920),
                                               None, "")}
        text = cover.page_data(made, sides)
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        self.assertEqual(data["lines"], ["&lt;放量&gt; &amp; <b>阳线</b>"])
        self.assertEqual((data["sub"], data["en"], data["tag"]), ("一句钩子", "A hook", "第 2 讲"))
        self.assertEqual(data["vertical"], {"style": "poster", "picture": "pic-vertical.png", "size": [1080, 1920],
                                            "place": None, "crop": [0.0, 0.2, 1.0, 0.45], "bg": "#060709", "aspect": 1.25})
        self.assertEqual(data["horizontal"], {"style": "photo", "picture": "pic-horizontal.png", "size": [1080, 1920],
                                              "place": "right", "mode": "card", "aspect": 0.5625, "zoom": 1.4,
                                              "focus": [0.5, 0.5]})
        whole = cover.side_data("vertical", made["sides"]["vertical"], "p.png", (1920, 1080), None, "#000000")
        self.assertEqual((whole["crop"], whole["aspect"]), ([0.0, 0.0, 1.0, 1.0], 1.7778))


class PromptTests(unittest.TestCase):
    """The AI cover's prompt: one fixed template, the film's own words and subject."""

    def made(self, **fields: Any) -> Dict[str, Any]:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        return cover.check_spec(spec(**fields), folder, 40.0)

    def test_every_word_goes_in_exactly_with_its_place_and_colour(self) -> None:
        made = self.made(sub="先问这三句，再决定追不追", tag="K线入门 · 第2讲", en="Three questions",
                         visual="一根放量阳线被金色光圈圈住")
        upright = cover.ai_prompt(made, "vertical", False)
        self.assertTrue(upright.startswith("竖版 3:4 的短视频封面"))
        for words in ("「K线入门 · 第2讲」", "「先问这三句，再决定追不追」", "「THREE QUESTIONS」", "第1行「看见放量」", "第2行「先问这三句」",
                      "其中「放量」用金色", "画面内容：一根放量阳线被金色光圈圈住。", "必须一字不差", "文字集中在画面上半部分",
                      "不要任何多余的文字"):
            self.assertIn(words, upright)
        self.assertNotIn("**", upright)
        self.assertNotIn("参考图", upright, "no reference, no word of one")
        wide = cover.ai_prompt(made, "horizontal", True)
        self.assertTrue(wide.startswith("横版 4:3 的短视频封面"))
        self.assertIn("文字集中在画面上方", wide)
        self.assertIn("参考图是片子里的一帧", wide)

    def test_the_lines_left_out_are_not_asked_for_and_the_look_and_colours_follow_the_spec(self) -> None:
        plain = cover.ai_prompt(self.made(title=["越过这条线"], visual="一条金线", look="扁平插画，米色底"), "vertical", False)
        self.assertIn("超大标题，粗黑体，暖白色，带一点发光：「越过这条线」", plain)
        self.assertIn("扁平插画，米色底", plain)
        for absent in ("标签", "标题下一行", "英文", "其中"):
            self.assertNotIn(absent, plain)
        brush = cover.ai_prompt(self.made(title=["放量了", "为什么**还跌**"], font="brush", accent="#ff3b3b", visual="一根阳线"),
                                "vertical", False)
        self.assertIn("毛笔书法字，色号 #ff3b3b 的颜色", brush)
        self.assertIn("其中「还跌」用白色", brush)


class ProjectTests(unittest.TestCase):
    def test_an_ai_cover_is_written_into_the_films_episode(self) -> None:
        film = Path("D:/项目/剧集/EP001/制作成果/成片/放量阳线-草稿.mp4")
        root, episode = cover.project_of(film)
        self.assertEqual((root, episode), (Path("D:/项目"), "剧集/EP001/制作成果"))
        targets = cover.ai_targets(film)
        self.assertEqual(targets["vertical"], ("剧集/EP001/制作成果/封面/cover-3x4.png", Path("D:/项目/剧集/EP001/制作成果/封面/cover-3x4.png")))
        self.assertEqual(targets["horizontal"][0], "剧集/EP001/制作成果/封面/cover-4x3.png")

    def test_a_film_outside_a_projects_episode_cannot_have_an_ai_cover(self) -> None:
        for misplaced in ("D:/片子/放量阳线.mp4", "D:/项目/剧集/第一集/制作成果/成片/放量阳线.mp4",
                          "D:/项目/剧集/EP001/制作成果/草稿/放量阳线.mp4"):
            with self.assertRaises(cover.Problem) as caught:
                cover.project_of(Path(misplaced))
            self.assertIn("剧集/EP001/制作成果/成片", str(caught.exception))


class SubjectTests(unittest.TestCase):
    """Where a poster finds the one thing a dark frame is about."""

    @staticmethod
    def frame(wide: int, tall: int, lit: List[tuple], ground: int = 6) -> bytes:
        pixels = bytearray([ground] * (wide * tall))
        for left, top, right, bottom, value in lit:
            for row in range(top, bottom):
                for column in range(left, right):
                    pixels[row * wide + column] = value
        return bytes(pixels)

    def test_the_bright_part_of_a_dark_frame_is_found_with_a_margin(self) -> None:
        pixels = self.frame(100, 200, [(20, 60, 80, 120, 200)])
        self.assertEqual(cover.bright_box(100, 200, pixels), (0.16, 0.26, 0.68, 0.38))

    def test_faint_lines_and_lone_specks_are_not_the_subject(self) -> None:
        # Gridlines a little brighter than the ground, and a single bright pixel far from the chart.
        pixels = self.frame(100, 200, [(0, 30, 100, 31, 30), (0, 150, 100, 151, 30), (95, 5, 96, 6, 255),
                                       (30, 80, 70, 110, 180)])
        self.assertEqual(cover.bright_box(100, 200, pixels), (0.26, 0.36, 0.48, 0.23))

    def test_a_light_frame_or_an_empty_one_has_no_subject_to_cut_out(self) -> None:
        self.assertIsNone(cover.bright_box(100, 200, self.frame(100, 200, [], ground=200)), "light ground")
        self.assertIsNone(cover.bright_box(100, 200, self.frame(100, 200, [])), "nothing bright")


class CandidateTests(unittest.TestCase):
    def test_candidates_spread_over_the_film_clear_of_its_first_and_last_moments(self) -> None:
        found = cover.windows(40.0, 12)
        self.assertEqual(len(found), 12)
        starts = [start for start, _ in found]
        self.assertEqual(starts, sorted(starts))
        self.assertGreaterEqual(starts[0], 0.5)
        self.assertLessEqual(found[-1][0] + found[-1][1], 40.0 - 1.5 + 0.01)
        self.assertTrue(all(0 < length <= cover.WINDOW for _, length in found))
        short = cover.windows(3.0, 12)
        self.assertTrue(all(start >= 0 for start, _ in short))

    def test_a_motion_film_offers_every_scene_settled_then_the_moments_its_marks_land(self) -> None:
        film = {"scenes": [
            {"type": "title", "start": 0.0, "end": 3.0, "text": "标题"},
            {"type": "kline", "start": 3.0, "end": 12.0, "focus": [{"at": 5.0, "note": "一"}, {"at": 8.0, "note": "二"}]},
            {"type": "statement", "start": 12.0, "end": 16.0, "lines": [{"at": 12.5, "text": "大字"}]},
            {"type": "end", "start": 16.0, "end": 20.0, "text": "片尾"},
        ]}
        self.assertEqual(cover.scene_moments(film, 12), [1.8, 5.6, 8.6, 13.1])
        self.assertEqual(cover.scene_moments(film, 3), [1.8, 8.6, 13.1], "every scene before any extra mark")
        self.assertEqual(len(cover.scene_moments(film, 2)), 2)
        self.assertEqual(cover.scene_moments({"scenes": [film["scenes"][-1]]}, 12), [], "the end card is never a cover")

    def test_a_clean_copy_draws_no_captions_headings_or_sound(self) -> None:
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        source = root / "动画"
        (source / "lib").mkdir(parents=True)
        (source / "snapshots").mkdir()
        (source / "lib" / "kit.js").write_text("// kit", encoding="utf-8")
        (source / "mix.wav").write_bytes(b"RIFF")
        (source / "index.html").write_text(
            '<html><head><style></style></head><body><div id="root"></div>\n'
            '      <audio id="mix" class="clip" data-start="0" src="mix.wav"></audio>\n</body></html>', encoding="utf-8")
        copy = cover.clean_copy(source, root / "封面")
        page = (copy / "index.html").read_text(encoding="utf-8")
        self.assertIn(".mk-captions, .mk-header { display: none !important; }", page)
        self.assertNotIn("<audio", page)
        self.assertTrue((copy / "lib" / "kit.js").is_file())
        self.assertFalse((copy / "mix.wav").exists())
        self.assertFalse((copy / "snapshots").exists())
        self.assertIn('id="mix"', (source / "index.html").read_text(encoding="utf-8"), "the film's own page is untouched")


class RendererTests(unittest.TestCase):
    def test_the_renderer_never_gets_a_key_it_could_send_frames_online_with(self) -> None:
        home = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, home, ignore_errors=True)
        entry = home / "hyperframes" / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs"
        entry.parent.mkdir(parents=True)
        entry.write_text("", encoding="utf-8")
        seen: Dict[str, Any] = {}

        def fake_run(command: List[str], **options: Any) -> subprocess.CompletedProcess:
            seen.update(options["env"])
            return subprocess.CompletedProcess(command, 0, "", "")

        keys = {"DRAMA_SKILLS_HOME": str(home), "GEMINI_API_KEY": "x", "GOOGLE_API_KEY": "x", "HEYGEN_API_KEY": "x",
                "ELEVENLABS_API_KEY": "x", "SOMETHING_ELSE": "kept"}
        with mock.patch.dict(os.environ, keys), mock.patch.object(animation, "node_program", return_value="node"), \
                mock.patch.object(animation.subprocess, "run", side_effect=fake_run):
            animation.run_tool(home, ["snapshot"])
        self.assertFalse(set(animation.CLOUD_KEYS) & set(seen))
        self.assertEqual(seen["SOMETHING_ELSE"], "kept")
        self.assertEqual(seen["HYPERFRAMES_NO_TELEMETRY"], "1")

    def test_snapshots_are_never_described_by_an_online_model(self) -> None:
        asked: List[List[str]] = []

        def fake_tool(folder: Path, arguments: List[str], **_: Any) -> subprocess.CompletedProcess:
            asked.append(list(arguments))
            return subprocess.CompletedProcess(arguments, 0, "", "")

        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        with mock.patch.object(animation, "run_tool", side_effect=fake_tool), mock.patch("builtins.print"), \
                self.assertRaises(SystemExit):
            animation.cmd_snapshot(argparse.Namespace(folder=folder, at="1,2"))
        self.assertEqual(asked[0][-2:], ["--describe", "false"])
        with mock.patch.object(animation, "run_tool", side_effect=fake_tool), self.assertRaises(cover.Problem):
            cover.stills(folder, [0.5], folder / "out")
        self.assertEqual(asked[1][-2:], ["--describe", "false"])


def make_film(path: Path, seconds: float, size: str, source: str = "testsrc2") -> None:
    pattern = f"{source}=size={size}:rate=30:duration={seconds}" if source == "testsrc2" else source
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", pattern,
                    "-pix_fmt", "yuv420p", "-t", str(seconds), str(path)], check=True)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class PictureTests(unittest.TestCase):
    def test_a_posters_ground_is_the_colour_round_its_subject(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        dark = folder / "dark.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0x0a0c10:size=320x180", "-vf", "drawbox=x=100:y=60:w=120:h=60:color=white:t=fill",
                        "-frames:v", "1", str(dark)], check=True)
        self.assertTrue(near(cover.ground_of(dark, None), "#0a0c10"), cover.ground_of(dark, None))
        width, height, pixels = cover.gray_pixels(dark, (320, 180))
        found = cover.bright_box(width, height, pixels)
        assert found is not None
        self.assertAlmostEqual(found[0], 100 / 320 - cover.MARGIN, delta=0.01)
        self.assertAlmostEqual(found[2], 120 / 320 + 2 * cover.MARGIN, delta=0.01)
        light = folder / "light.png"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=0xd0e0f0:size=320x180", "-frames:v", "1", str(light)], check=True)
        ground = cover.ground_of(light, None)
        self.assertLess(sum(int(ground[i:i + 2], 16) for i in (1, 3, 5)) / 3, cover.DARK_GROUND, "a light frame's ground is darkened")


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class RunTests(unittest.TestCase):
    """From a finished film to two covers, with the browser standing in: the renderer is replaced by
    a picture of the size the page asks for, so everything around it runs for real."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        # A project laid out the way the remake skill builds one: the film in its episode's production folder.
        self.project = self.root / "项目"
        self.film = self.project / "剧集" / "EP001" / "制作成果" / "成片" / "放量阳线-草稿.mp4"
        self.film.parent.mkdir(parents=True)
        make_film(self.film, 6.0, "540x960")
        self.folder = self.project / "制作" / "封面"

    def run_command(self, function: Any, **fields: Any) -> Dict[str, Any]:
        printed: List[str] = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))), \
                self.assertRaises(SystemExit):
            function(argparse.Namespace(**fields))
        return json.loads(printed[-1])

    def fake_install(self) -> Path:
        home = self.root / "home"
        modules = home / "node_modules"
        for package, name in animation.FONTS:
            (modules / package).mkdir(parents=True, exist_ok=True)
            (modules / package / name).write_bytes(b"font")
        (modules / "gsap" / "dist").mkdir(parents=True, exist_ok=True)
        (modules / "gsap" / "dist" / "gsap.min.js").write_text("// gsap", encoding="utf-8")
        return home

    def write_spec(self, **fields: Any) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        fields.setdefault("film", str(self.film))
        (self.folder / "封面.json").write_text(json.dumps(spec(**fields), ensure_ascii=False), encoding="utf-8")

    def ai_picture(self, side: str, size: str) -> Path:
        """A picture where the production skill would have put this cover's AI drawing."""
        picture = cover.ai_targets(self.film)[side][1]
        picture.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x203040:size={size}",
                        "-frames:v", "1", str(picture)], check=True)
        return picture

    def make(self, **fields: Any) -> tuple:
        self.write_spec(**fields)
        return self.render()

    def render(self) -> tuple:
        home = self.fake_install()
        pages: List[Dict[str, Any]] = []

        def fake_tool(folder: Path, arguments: List[str], **_: Any) -> subprocess.CompletedProcess:
            page = (folder / "index.html").read_text(encoding="utf-8")
            size = re.search(r'data-width="(\d+)" data-height="(\d+)"', page)
            assert size is not None
            data = (folder / "cover-data.js").read_text(encoding="utf-8")
            pages.append({"size": size.groups(), "data": json.loads(data[data.index("{"):data.rindex("}") + 1])})
            out = Path(arguments[arguments.index("-o") + 1])
            out.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                            f"color=c=black:size={size.group(1)}x{size.group(2)}", "-frames:v", "1",
                            str(out / "frame-00-at-0.5s.png")], check=True)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with mock.patch.object(animation, "ensure_install", return_value={"home": str(home)}), \
                mock.patch.object(animation, "ensure_browser", return_value=(True, "")), \
                mock.patch.object(animation, "run_tool", side_effect=fake_tool):
            done = self.run_command(cover.cmd_make, folder=self.folder)
        return done, pages

    def test_frames_lays_out_candidates_and_starts_a_spec_that_make_will_not_take_unfinished(self) -> None:
        done = self.run_command(cover.cmd_frames, film=self.film, out=self.folder, animation=None, clean=None, count=6)
        self.assertEqual([row["n"] for row in done["frames"]], [1, 2, 3, 4, 5, 6])
        moments = [row["at"] for row in done["frames"]]
        self.assertEqual(moments, sorted(moments))
        self.assertTrue(all(0 <= moment < 6.0 for moment in moments))
        target = Path(done["folder"])
        self.assertTrue(all((target / row["file"]).is_file() for row in done["frames"]))
        self.assertTrue((target / "汇总.jpg").is_file())
        started = json.loads((self.folder / "封面.json").read_text(encoding="utf-8"))
        self.assertEqual((started["title"], started["at"], started["sub"]), ([], None, ""))
        self.assertEqual(Path(started["film"]), self.film.resolve())
        with self.assertRaises(cover.Problem) as caught:
            cover.check_spec(started, self.folder, 6.0)
        self.assertIn("title 还没写", str(caught.exception))

    def test_a_clean_cut_of_another_length_is_refused(self) -> None:
        other = self.film.parent / "成片-未混音.mp4"
        make_film(other, 4.0, "540x960")
        with self.assertRaises(cover.Problem) as caught:
            cover.cmd_frames(argparse.Namespace(film=self.film, out=self.folder, animation=None, clean=other, count=6))
        self.assertIn("不是同一版剪辑", str(caught.exception))

    def test_prompt_writes_one_job_per_cover_that_the_production_tool_takes(self) -> None:
        sys.path.insert(0, str(SUITE / "skills/short-drama-produce/scripts"))
        import production_tool

        self.write_spec(at=2.0, visual="一根放量阳线被金色光圈圈住", sub="一句钩子")
        done = self.run_command(cover.cmd_prompt, folder=self.folder)
        self.assertEqual((done["price_yuan"], done["reference"]), (0.8, None))
        self.assertEqual(done["job_ids"], {"vertical": "EP001-cover-3x4", "horizontal": "EP001-cover-4x3"})
        for side in cover.SIDES:
            job = json.loads(Path(done["jobs"][side]).read_text(encoding="utf-8"))
            self.assertEqual((job["adapter"], job["modality"]), ("gpt-image-2", "image"))
            self.assertEqual((job["parameters"]["width"], job["parameters"]["height"]), cover.AI_SIZE[side])
            self.assertIn("「一句钩子」", job["prompt"])
            prepared = production_tool._normalize_job(self.project, job)
            self.assertEqual(prepared["outputs"], [cover.ai_targets(self.film)[side][0]])
        self.assertFalse(any(cover.ai_targets(self.film)[side][1].exists() for side in cover.SIDES), "nothing is drawn yet")
        asked = json.loads((self.folder / "AI出图" / "words.json").read_text(encoding="utf-8"))
        self.assertEqual(asked["sub"], "一句钩子")

    def test_footage_sends_a_frame_of_the_film_as_its_reference(self) -> None:
        sys.path.insert(0, str(SUITE / "skills/short-drama-produce/scripts"))
        import production_tool

        clean = self.film.parent / "成片-未混音.mp4"
        make_film(clean, 6.0, "540x960")
        self.write_spec(at=2.0, clean=str(clean), visual="女孩站在走廊里，表情害怕")
        done = self.run_command(cover.cmd_prompt, folder=self.folder)
        self.assertEqual(done["reference"], "制作/封面/AI出图/参考帧.png")
        job = json.loads(Path(done["jobs"]["vertical"]).read_text(encoding="utf-8"))
        self.assertEqual(job["reference_bindings"][0]["path"], "制作/封面/AI出图/参考帧.png")
        self.assertIn("参考图是片子里的一帧", job["prompt"])
        self.assertEqual(production_tool._normalize_job(self.project, job)["references"], ["制作/封面/AI出图/参考帧.png"])

    def test_prompt_needs_the_subject_and_a_film_inside_a_projects_episode(self) -> None:
        self.write_spec(at=2.0)
        with self.assertRaises(cover.Problem) as caught:
            cover.cmd_prompt(argparse.Namespace(folder=self.folder))
        self.assertIn("visual 还没写", str(caught.exception))
        loose = self.root / "放量阳线.mp4"
        shutil.copyfile(self.film, loose)
        self.write_spec(at=2.0, visual="一根阳线", film=str(loose))
        with self.assertRaises(cover.Problem) as caught:
            cover.cmd_prompt(argparse.Namespace(folder=self.folder))
        self.assertIn("剧集/EP001/制作成果/成片", str(caught.exception))

    def test_make_uses_a_current_ai_picture_and_draws_only_the_other_cover(self) -> None:
        self.write_spec(at=2.0, visual="一根放量阳线")
        self.run_command(cover.cmd_prompt, folder=self.folder)
        self.ai_picture("vertical", "1086x1448")
        done, pages = self.render()
        self.assertEqual(done["sources"], {"vertical": "ai", "horizontal": "photo"})
        self.assertEqual(cover.probe(Path(done["covers"]["vertical"]))["size"], (1080, 1440), "fitted to the cover exactly")
        self.assertEqual([page["size"] for page in pages], [("1440", "1080")], "only the wide one is drawn")

    def test_an_ai_picture_drawn_before_the_words_changed_or_turned_off_is_not_used(self) -> None:
        self.write_spec(at=2.0, visual="一根放量阳线")
        self.run_command(cover.cmd_prompt, folder=self.folder)
        self.ai_picture("vertical", "1086x1448")
        self.ai_picture("horizontal", "1448x1086")
        self.write_spec(at=2.0, visual="一根放量阳线", title=["换了标题"])
        done, _ = self.render()
        self.assertEqual(done["sources"], {"vertical": "photo", "horizontal": "photo"}, "the words changed after drawing")
        self.write_spec(at=2.0, visual="一根放量阳线", vertical={"ai": False})
        done, _ = self.render()
        self.assertEqual(done["sources"], {"vertical": "photo", "horizontal": "ai"})

    def test_the_program_asks_for_a_frame_only_when_it_has_to_draw(self) -> None:
        self.write_spec(at=None, visual="一根放量阳线")
        self.run_command(cover.cmd_prompt, folder=self.folder)
        self.ai_picture("vertical", "1086x1448")
        self.ai_picture("horizontal", "1448x1086")
        done, pages = self.render()
        self.assertEqual((done["sources"], pages), ({"vertical": "ai", "horizontal": "ai"}, []))
        (self.ai_picture("horizontal", "1448x1086")).unlink()
        with self.assertRaises(cover.Problem) as caught, \
                mock.patch.object(animation, "ensure_install", return_value={"home": str(self.fake_install())}):
            cover.cmd_make(argparse.Namespace(folder=self.folder))
        self.assertIn("横封面要用程序版，可还没定画面", str(caught.exception))

    def test_make_writes_both_photo_covers_beside_the_film(self) -> None:
        done, pages = self.make(at=2.0, tag="第 2 讲")
        self.assertEqual(done["sources"], {"vertical": "photo", "horizontal": "photo"})
        self.assertEqual([page["size"] for page in pages], [("1080", "1440"), ("1440", "1080")])
        self.assertEqual(pages[0]["data"]["lines"], ["看见<b>放量</b>", "先问这三句"])
        self.assertEqual((pages[0]["data"]["vertical"]["mode"], pages[0]["data"]["horizontal"]["mode"]), ("full", "card"))
        for side, (width, height) in cover.SIZE.items():
            made = Path(done["covers"][side])
            self.assertEqual(made.parent, self.film.parent.resolve())
            self.assertEqual(cover.probe(made)["size"], (width, height))
        self.assertEqual(sorted(p.name for p in self.film.parent.glob("*-封面-*.jpg")),
                         ["放量阳线-封面-横4比3.jpg", "放量阳线-封面-竖3比4.jpg"])
        self.assertEqual(sorted(p.name for p in (self.folder / "页面" / "lib" / "fonts").iterdir()), sorted(cover.FONT_FILES))
        self.assertFalse((self.folder / "页面" / ".出图").exists(), "the renderer's scratch folder is cleared away")

    def test_a_poster_finds_its_subject_and_its_ground_in_the_frame(self) -> None:
        # A dark frame with one bright block: the poster cuts that block out and lays it on the frame's ground.
        make_film(self.film, 6.0, "540x960", "color=c=0x080a0c:size=540x960:rate=30,drawbox=x=60:y=300:w=420:h=240:color=0xff3b3b:t=fill")
        done, pages = self.make(at=2.0, style="poster", sub="一句钩子", horizontal={"crop": [0, 0, 1, 0.5]})
        self.assertEqual(done["sources"], {"vertical": "poster", "horizontal": "poster"})
        vertical, horizontal = pages[0]["data"]["vertical"], pages[0]["data"]["horizontal"]
        left, top, wide, tall = vertical["crop"]
        self.assertAlmostEqual(left, 60 / 540 - cover.MARGIN, delta=0.01)
        self.assertAlmostEqual(top, 300 / 960 - cover.MARGIN, delta=0.01)
        self.assertAlmostEqual(wide, 420 / 540 + 2 * cover.MARGIN, delta=0.01)
        self.assertAlmostEqual(tall, 240 / 960 + 2 * cover.MARGIN, delta=0.01)
        self.assertTrue(near(vertical["bg"], "#080a0c"), vertical["bg"])
        self.assertEqual(horizontal["crop"], [0.0, 0.0, 1.0, 0.5], "a crop that was written is kept")
        self.assertEqual(done["crops"]["horizontal"], [0.0, 0.0, 1.0, 0.5])


if __name__ == "__main__":
    unittest.main()
