"""The remake skill's smaller helpers: saving the shot tool's output in any shell, finding the
speech recogniser's own Python, comparing a finished film with its reference, aiming a mix at the
reference's loudness, and the record of where the music came from."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SCRIPTS = SUITE / "skills/short-drama-remake/scripts"
sys.path.insert(0, str(SCRIPTS))

import fetch_music  # noqa: E402
import media_profile  # noqa: E402
import mix_audio  # noqa: E402
import shots  # noqa: E402
import transcribe  # noqa: E402


def printing(text: str) -> list:
    """A command that prints `text` as UTF-8 bytes, whatever the console's own encoding is."""
    return [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({text!r}.encode('utf-8'))"]


class ShotOutputTests(unittest.TestCase):
    def test_output_is_saved_byte_for_byte_as_utf8(self) -> None:
        # PowerShell 5.1's `>` would have written this as UTF-16, which the tool's next step cannot read.
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "sub" / "shots.json"
            result = shots.save_output(printing('{"title": "片名", "shots": []}\n'), out)
            self.assertTrue(result["ok"], result)
            self.assertEqual(out.read_bytes(), '{"title": "片名", "shots": []}\n'.encode("utf-8"))
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["title"], "片名")

    def test_a_failed_or_silent_run_leaves_the_earlier_file_alone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "shots.json"
            out.write_text('{"kept": true}', encoding="utf-8")
            failed = shots.save_output([sys.executable, "-c", "import sys; print('half'); sys.exit(3)"], out)
            self.assertEqual((failed["ok"], failed["exit"]), (False, 3))
            silent = shots.save_output([sys.executable, "-c", "pass"], out)
            self.assertFalse(silent["ok"])
            self.assertEqual(out.read_text(encoding="utf-8"), '{"kept": true}')
            self.assertEqual([p.name for p in Path(directory).iterdir()], ["shots.json"])

    def test_a_step_can_read_the_file_it_is_about_to_replace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "shots.json"
            out.write_text('{"cuts": 1}', encoding="utf-8")
            again = [sys.executable, "-c",
                     "import json, sys; doc = json.load(open(sys.argv[1], encoding='utf-8')); doc['cuts'] += 1; print(json.dumps(doc))",
                     str(out)]
            self.assertTrue(shots.save_output(again, out)["ok"])
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {"cuts": 2})

    @unittest.skipUnless(shutil.which("node"), "needs node")
    def test_the_bundled_tool_runs_through_the_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "usage.txt"
            result = shots.save_output([shutil.which("node"), str(shots.TOOL), "--help"], out)
            self.assertTrue(result["ok"], result)
            self.assertIn("seed", out.read_text(encoding="utf-8"))


class RecogniserPythonTests(unittest.TestCase):
    def test_the_recognisers_own_python_is_found_only_when_it_is_there_and_is_not_this_one(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            running = home / "system" / "python"
            self.assertIsNone(transcribe.recogniser_python(home, running))
            own = home / "asr-venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            own.parent.mkdir(parents=True)
            own.write_bytes(b"")
            self.assertEqual(transcribe.recogniser_python(home, running), own)
            self.assertIsNone(transcribe.recogniser_python(home, own), "already running there: nothing to hand over to")


def measured(duration: float, lufs: float, speech: object = None) -> dict:
    return {"duration_s": duration,
            "audio": {"integrated_lufs": lufs, "true_peak_dbfs": -1.5, "long_pauses": 0, "continuous_bed": True, "pauses_per_minute": 0.0},
            "motion": {"longest_still_s": 2.0, "still_share": 0.3, "still_in_first_3s": 0.0},
            "speech": speech}


class FilmComparisonTests(unittest.TestCase):
    def levels(self, new: dict, ref: dict) -> dict:
        return {item["check"]: item["level"] for item in media_profile.compare(new, ref)}

    def test_a_film_more_than_a_tenth_longer_or_shorter_is_not_close(self) -> None:
        ref = measured(100.0, -13.0)
        self.assertEqual(self.levels(measured(108.0, -13.0), ref)["时长接近"], "pass")
        self.assertEqual(self.levels(measured(113.0, -13.0), ref)["时长接近"], "fail")
        self.assertEqual(self.levels(measured(88.0, -13.0), ref)["时长接近"], "fail")

    def test_speech_that_could_not_be_compared_is_said_not_dropped(self) -> None:
        pace = {"chars_per_second": 4.8}
        missing = media_profile.compare(measured(100.0, -13.0), measured(100.0, -13.0, pace))
        note = next(item for item in missing if item["check"] == "语速不拖")
        self.assertEqual(note["level"], "note")
        self.assertIn("transcribe.py", note["detail"])
        self.assertEqual(self.levels(measured(100.0, -13.0, {"chars_per_second": 5.0}), measured(100.0, -13.0, pace))["语速不拖"], "pass")
        self.assertEqual(self.levels(measured(100.0, -13.0, {"chars_per_second": 3.5}), measured(100.0, -13.0, pace))["语速不拖"], "fail")
        self.assertNotIn("语速不拖", self.levels(measured(100.0, -13.0), measured(100.0, -13.0)), "a reference nobody speaks in")

    def test_a_film_much_quieter_than_its_reference_passes_with_a_note(self) -> None:
        found = self.levels(measured(100.0, -13.9), measured(100.0, -9.4))
        self.assertEqual((found["音量够"], found["和原片一样响"]), ("pass", "note"))
        self.assertNotIn("和原片一样响", self.levels(measured(100.0, -11.0), measured(100.0, -9.4)))
        # A reference louder than the mix is ever taken to: the note stays, the advice to use --like goes.
        at_the_limit = next(item for item in media_profile.compare(measured(100.0, -11.2), measured(100.0, -6.5))
                            if item["check"] == "和原片一样响")
        self.assertEqual(at_the_limit["level"], "note")
        self.assertNotIn("--like", at_the_limit["detail"])


class MixTargetTests(unittest.TestCase):
    def test_the_references_loudness_is_followed_within_limits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "source.profile.json"
            for heard, aim in ((-9.4, -11.0), (-12.3, -12.3), (-18.4, -14.0)):
                profile.write_text(json.dumps({"audio": {"integrated_lufs": heard}}), encoding="utf-8")
                self.assertEqual(mix_audio.target_like(profile), aim)
            profile.write_text(json.dumps({"audio": None}), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                mix_audio.target_like(profile)


class MusicRecordTests(unittest.TestCase):
    TRACKS = [{"id": 175, "name": "Digital Clouds", "artist": "A. M."}, {"id": 634, "name": "Your Breath", "artist": "E. M."}]

    def test_every_candidate_stays_on_record_and_the_one_used_is_marked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            (out / "mixkit-175.mp3").write_bytes(b"first")
            first = fetch_music.write_notes(out, self.TRACKS, 175).read_text(encoding="utf-8")
            self.assertIn("编号 175（" + fetch_music.BED_MARK, first)
            # A second candidate is fetched for the creator to hear; no bed is made from it.
            (out / "mixkit-634.mp3").write_bytes(b"second")
            (out / "mixkit-999.mp3").write_bytes(b"not in the catalog")
            both = fetch_music.write_notes(out, self.TRACKS, None).read_text(encoding="utf-8")
            self.assertIn("Digital Clouds", both)
            self.assertIn("Your Breath", both)
            self.assertNotIn("999", both)
            self.assertEqual(both.count(fetch_music.BED_MARK), 1)
            self.assertIn("编号 175（" + fetch_music.BED_MARK, both)
            switched = fetch_music.write_notes(out, self.TRACKS, 634).read_text(encoding="utf-8")
            self.assertIn("编号 634（" + fetch_music.BED_MARK, switched)
            self.assertEqual(switched.count(fetch_music.BED_MARK), 1)


if __name__ == "__main__":
    unittest.main()
