"""The remake skill's covers: what a cover may say, which picture each of the two takes, how the
page is sized, and the run from a finished film to two pictures beside it."""

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
        self.refused(spec(at=None), "还没定画面")
        self.refused(spec(at=40.0), "40 秒", "40.00 秒")
        self.refused(spec(vertical={"image": "没有这张.png"}), "找不到")

    def test_unknown_fields_and_values_out_of_range_are_named(self) -> None:
        self.refused(spec(titel=["错字"]), "titel")
        self.refused(spec(vertical={"postion": "top"}), "postion")
        self.refused(spec(horizontal={"place": "top"}), "left / center / right")
        self.refused(spec(vertical={"zoom": 5}), "1 到 3")
        self.refused(spec(vertical={"focus": [0.5]}), "[横向, 竖向]")
        self.refused(spec(vertical={"focus": [0.5, 2]}), "[横向, 竖向]")
        self.refused(spec(accent="gold"), "#ffd23f")
        self.refused(spec(font="hei"), "bold", "brush")
        self.refused(spec(tag="**重点**"), "tag")
        self.refused(spec(at="3"), "要写数字")
        moving = self.folder / "动画"
        moving.mkdir()
        (moving / "index.html").write_text("<html></html>", encoding="utf-8")
        (moving / "film.js").write_text("window.FILM = {};", encoding="utf-8")
        (self.folder / "无字幕.mp4").write_bytes(b"mp4")
        self.assertEqual(self.check(spec(animation="动画"))["animation"], moving.resolve())
        self.refused(spec(animation="动画", clean="无字幕.mp4"), "只写一个")
        self.refused(spec(animation="不存在的目录"), "不是排好的动画目录")

    def test_defaults_fill_in_what_was_left_out(self) -> None:
        made = self.check(spec())
        self.assertEqual((made["font"], made["accent"], made["tag"]), ("bold", cover.ACCENT, ""))
        self.assertEqual(made["sides"]["vertical"]["place"], "top")
        self.assertEqual(made["sides"]["horizontal"]["place"], "left")
        self.assertEqual(made["sides"]["horizontal"]["zoom"], 1.0)
        self.assertEqual(made["sides"]["horizontal"]["focus"], [0.5, 0.5])
        self.assertEqual(made["film"], (self.folder / "成片.mp4").resolve())


class LayoutTests(unittest.TestCase):
    def test_a_picture_that_does_not_suit_its_cover_goes_on_a_card(self) -> None:
        self.assertEqual(cover.shape_for("vertical", (1080, 1920)), "full")
        self.assertEqual(cover.shape_for("vertical", (1536, 2048)), "full", "a 3:4 picture loses a little at the sides")
        self.assertEqual(cover.shape_for("vertical", (1920, 1080)), "card")
        self.assertEqual(cover.shape_for("vertical", (1024, 1024)), "card")
        self.assertEqual(cover.shape_for("horizontal", (1920, 1080)), "full")
        self.assertEqual(cover.shape_for("horizontal", (1440, 1080)), "full")
        self.assertEqual(cover.shape_for("horizontal", (1080, 1920)), "card")

    def test_covers_sit_beside_the_film_without_its_draft_or_final_mark(self) -> None:
        film = Path("项目/剧集/EP001/制作成果/成片/放量阳线-草稿.mp4")
        paths = cover.cover_paths(film)
        self.assertEqual(paths["vertical"], film.parent / "放量阳线-封面-竖.jpg")
        self.assertEqual(paths["horizontal"], film.parent / "放量阳线-封面-横.jpg")
        self.assertEqual(cover.cover_paths(film.with_name("放量阳线-正式版.mp4")), paths)
        self.assertEqual(cover.cover_paths(Path("成片.mp4"))["vertical"].name, "成片-封面-竖.jpg")

    def test_the_page_states_one_size_everywhere_on_either_cover(self) -> None:
        upright, wide = cover.page_for("vertical"), cover.page_for("horizontal")
        self.assertIn('data-width="1080" data-height="1920"', upright)
        self.assertIn('data-width="1920" data-height="1080"', wide)
        self.assertIn("width: 1920px; height: 1080px", wide)
        # The size is named in exactly four places (viewport, page, root, root's data): the layout
        # itself reads it from the page, so turning the page on its side turns all of it.
        self.assertEqual((upright.count("1080"), upright.count("1920")), (4, 4))
        self.assertEqual((wide.count("1080"), wide.count("1920")), (4, 4))

    def test_the_page_is_told_each_covers_picture_shape_and_words(self) -> None:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)
        made = cover.check_spec(spec(title=["<放量> & **阳线**"], tag="第 2 讲", horizontal={"place": "right", "zoom": 1.4}),
                                folder, 40.0)
        text = cover.page_data(made, {"vertical": ("pic-vertical.png", (1080, 1920)),
                                      "horizontal": ("pic-horizontal.png", (1080, 1920))})
        data = json.loads(text[text.index("{"):text.rindex("}") + 1])
        self.assertEqual(data["lines"], ["&lt;放量&gt; &amp; <b>阳线</b>"])
        self.assertEqual(data["tag"], "第 2 讲")
        self.assertEqual(data["vertical"]["mode"], "full")
        self.assertEqual(data["horizontal"], {"picture": "pic-horizontal.png", "aspect": 0.5625, "mode": "card",
                                              "place": "right", "zoom": 1.4, "focus": [0.5, 0.5]})


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


def make_film(path: Path, seconds: float, size: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=size={size}:rate=30:duration={seconds}", "-pix_fmt", "yuv420p", str(path)], check=True)


@unittest.skipUnless(FFMPEG, "needs ffmpeg")
class RunTests(unittest.TestCase):
    """From a finished film to two covers, with the browser standing in: the renderer is replaced by
    a picture of the size the page asks for, so everything around it runs for real."""

    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.film = self.root / "成片" / "放量阳线-草稿.mp4"
        self.film.parent.mkdir()
        make_film(self.film, 6.0, "540x960")
        self.folder = self.root / "制作" / "封面"

    def run_command(self, function: Any, **fields: Any) -> Dict[str, Any]:
        printed: List[str] = []
        with mock.patch("builtins.print", side_effect=lambda *a, **k: printed.append(" ".join(map(str, a)))), \
                self.assertRaises(SystemExit):
            function(argparse.Namespace(**fields))
        return json.loads(printed[-1])

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
        self.assertEqual((started["title"], started["at"]), ([], None))
        self.assertEqual(Path(started["film"]), self.film.resolve())
        with self.assertRaises(cover.Problem) as caught:
            cover.check_spec(started, self.folder, 6.0)
        self.assertIn("title 还没写", str(caught.exception))

    def test_a_clean_cut_of_another_length_is_refused(self) -> None:
        other = self.root / "成片" / "成片-未混音.mp4"
        make_film(other, 4.0, "540x960")
        with self.assertRaises(cover.Problem) as caught:
            cover.cmd_frames(argparse.Namespace(film=self.film, out=self.folder, animation=None, clean=other, count=6))
        self.assertIn("不是同一版剪辑", str(caught.exception))

    def test_make_writes_both_covers_beside_the_film(self) -> None:
        home = self.root / "home"
        modules = home / "node_modules"
        for package, name in animation.FONTS:
            (modules / package).mkdir(parents=True, exist_ok=True)
            (modules / package / name).write_bytes(b"font")
        (modules / "gsap" / "dist").mkdir(parents=True)
        (modules / "gsap" / "dist" / "gsap.min.js").write_text("// gsap", encoding="utf-8")
        self.folder.mkdir(parents=True)
        (self.folder / "封面.json").write_text(json.dumps(spec(film=str(self.film), at=2.0, tag="第 2 讲"),
                                                         ensure_ascii=False), encoding="utf-8")
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
        self.assertEqual(done["shapes"], {"vertical": "full", "horizontal": "card"})
        self.assertEqual([page["size"] for page in pages], [("1080", "1920"), ("1920", "1080")])
        self.assertEqual(pages[0]["data"]["lines"], ["看见<b>放量</b>", "先问这三句"])
        for side, (width, height) in cover.SIZE.items():
            made = Path(done["covers"][side])
            self.assertEqual(made.parent, self.film.parent.resolve())
            self.assertEqual(cover.probe(made)["size"], (width, height))
        self.assertEqual(sorted(p.name for p in self.film.parent.glob("*-封面-*.jpg")),
                         ["放量阳线-封面-横.jpg", "放量阳线-封面-竖.jpg"])
        self.assertEqual(sorted(p.name for p in (self.folder / "页面" / "lib" / "fonts").iterdir()), sorted(cover.FONT_FILES))
        self.assertFalse((self.folder / "页面" / ".出图").exists(), "the renderer's scratch folder is cleared away")


if __name__ == "__main__":
    unittest.main()
