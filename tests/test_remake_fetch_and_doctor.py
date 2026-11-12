"""The remake skill's one-command Douyin fetch (with the browser step faked and the media served
from this machine) and its environment check."""

import contextlib
import http.server
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
SCRIPTS = SUITE / "skills/short-drama-remake/scripts"
sys.path.insert(0, str(SCRIPTS))

import fetch_reference  # noqa: E402

VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 64


class _Media(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(VIDEO_BYTES)))
        self.end_headers()
        self.wfile.write(VIDEO_BYTES)

    def log_message(self, *_args: object) -> None:
        pass


def fake_browser_step(folder: Path, body: str) -> list:
    """A stand-in for `node douyin_edge.mjs`: same arguments, same last-line JSON."""
    script = folder / "fake_edge.py"
    script.write_text(
        "import json, sys\nfrom pathlib import Path\n"
        "work, out = sys.argv[1], Path(sys.argv[sys.argv.index('--out') + 1])\n" + body,
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


class DouyinFetchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Media)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.media = f"http://127.0.0.1:{self.server.server_address[1]}/play.mp4"

    def test_detail_from_the_browser_step_is_downloaded_in_the_same_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            out = root / "R01"
            out.mkdir()
            command = fake_browser_step(root, (
                "detail = {'ok': True, 'aweme_detail': {'aweme_id': work, 'desc': '示例作品', 'author': {'nickname': '某作者'},\n"
                "  'statistics': {'digg_count': 12}, 'video': {'duration': 9000,\n"
                f"  'bit_rate': [{{'gear_name': 'normal_720', 'bit_rate': 900, 'is_h265': 0, 'play_addr': {{'url_list': ['{self.media}']}}}}]}}}}}}\n"
                "(out / 'douyin_detail.json').write_text(json.dumps(detail), encoding='utf-8')\n"
                "print('noise before the result')\nprint(json.dumps({'ok': True, 'file': str(out / 'douyin_detail.json')}))\n"
            ))
            detail, why = fetch_reference.edge_detail("7123456789012345678", out, command)
            self.assertEqual(why, "")
            self.assertEqual(detail, out / "douyin_detail.json")
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed), self.assertRaises(SystemExit) as done:
                fetch_reference.douyin_detail(detail, out)
            self.assertEqual(done.exception.code, 0)
            result = json.loads(printed.getvalue().strip().splitlines()[-1])
            self.assertTrue(result["ok"])
            self.assertEqual((out / "source.mp4").read_bytes(), VIDEO_BYTES)
            facts = json.loads((out / "source.json").read_text(encoding="utf-8"))
            self.assertEqual((facts["platform"], facts["id"], facts["author"]), ("douyin", "7123456789012345678", "某作者"))
            self.assertEqual(facts["rendition"], "normal_720")

    def test_a_refusal_from_the_browser_step_comes_back_with_its_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            command = fake_browser_step(root, "print(json.dumps({'ok': False, 'error': '遇到了验证页'}))\nsys.exit(1)\n")
            detail, why = fetch_reference.edge_detail("7123456789012345678", root, command)
            self.assertIsNone(detail)
            self.assertEqual(why, "遇到了验证页")

    def test_output_that_is_not_a_result_is_a_failure_not_a_crash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body in ("print('Segmentation fault')\n", "pass\n", "print(json.dumps({'ok': True}))\n"):
                with self.subTest(body=body):
                    detail, why = fetch_reference.edge_detail("7123456789012345678", root, fake_browser_step(root, body))
                    self.assertIsNone(detail)
                    self.assertTrue(why)

    def test_a_step_that_cannot_start_is_a_failure_with_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            detail, why = fetch_reference.edge_detail("7123456789012345678", Path(directory),
                                                      [str(Path(directory) / "no-such-program")])
            self.assertIsNone(detail)
            self.assertTrue(why)


class DoctorTests(unittest.TestCase):
    def run_doctor(self, home: Path, *arguments: str, **environment: str) -> tuple:
        # Keys this computer may really have set must not leak into what the check is shown.
        env = {name: value for name, value in os.environ.items()
               if name not in ("MINIMAX_API_KEY", "OPENAI_API_KEY", "DRAMA_SKILLS_ENV")}
        env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1", **environment)
        done = subprocess.run([sys.executable, "-B", str(SCRIPTS / "doctor.py"), *arguments], env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        lines = [line for line in done.stdout.splitlines() if line.strip()]
        return json.loads(lines[-1]), done.stdout + done.stderr

    def item(self, result: dict, name: str) -> dict:
        found = [i for i in result["items"] if i["name"] == name]
        self.assertEqual(len(found), 1, name)
        return found[0]

    def test_a_missing_key_file_is_named_with_what_it_is_for_and_how_to_fix_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result, _text = self.run_doctor(Path(directory))
            self.assertTrue(result["ok"])
            keys = self.item(result, "密钥文件")
            self.assertEqual(keys["state"], "missing")
            self.assertTrue(keys["use"] and keys["fix"])
            self.assertIn("密钥文件", result["missing"])
            self.assertFalse(result["ready"])

    def test_key_values_are_never_printed(self) -> None:
        secret = "sk-cp-" + "Zx9" * 14
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".drama-skills").mkdir()
            (home / ".drama-skills/providers.env").write_text(
                f"# comment\nMINIMAX_API_KEY={secret}\nMINIMAX_REGION=cn\nOPENAI_API_KEY=\n", encoding="utf-8")
            result, text = self.run_doctor(home)
            self.assertEqual(self.item(result, "密钥文件")["state"], "ok")
            self.assertNotIn(secret, text)
            self.assertNotIn(secret[6:], text)

    def test_an_empty_minimax_key_counts_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            (home / ".drama-skills").mkdir()
            (home / ".drama-skills/providers.env").write_text("MINIMAX_API_KEY=\nOPENAI_API_KEY=abc\n", encoding="utf-8")
            result, _text = self.run_doctor(home)
            self.assertEqual(self.item(result, "密钥文件")["state"], "missing")

    def test_keys_are_found_wherever_the_production_skill_would_find_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            usual = home / ".drama-skills/providers.env"
            usual.parent.mkdir()

            def state(**environment: str) -> str:
                return self.item(self.run_doctor(home, **environment)[0], "密钥文件")["state"]

            # Saved by an editor that adds a byte-order mark, in shell style.
            usual.write_bytes("\ufeffexport MINIMAX_API_KEY=abc\n".encode("utf-8"))
            self.assertEqual(state(), "ok")
            usual.unlink()
            self.assertEqual(state(), "missing")
            self.assertEqual(state(MINIMAX_API_KEY="abc"), "ok", "set in the environment")
            elsewhere = home / "keys-kept-elsewhere.env"
            elsewhere.write_text("MINIMAX_API_KEY='abc'\n", encoding="utf-8")
            self.assertEqual(state(DRAMA_SKILLS_ENV=str(elsewhere)), "ok", "in the file DRAMA_SKILLS_ENV names")

    def test_the_animation_tool_is_only_checked_for_the_motion_route(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            motion, _ = self.run_doctor(Path(directory), "--route", "motion")
            drama, _ = self.run_doctor(Path(directory), "--route", "drama")
            self.assertIn("动画工具", [i["name"] for i in motion["items"]])
            self.assertNotIn("动画工具", [i["name"] for i in drama["items"]])
            self.assertEqual(self.item(motion, "动画工具")["state"], "partial")


if __name__ == "__main__":
    unittest.main()
