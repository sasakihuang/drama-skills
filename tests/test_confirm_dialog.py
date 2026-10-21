"""Confirming a prepared job by the creator's click in a system dialog."""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SUITE = Path(__file__).resolve().parents[1]
TOOL = SUITE / "skills/short-drama-produce/scripts/production_tool.py"
PROJECT_TOOL = SUITE / "skills/short-drama/scripts/project_tool.py"
SPEC = importlib.util.spec_from_file_location("short_drama_production_tool_dialog", TOOL)
assert SPEC is not None and SPEC.loader is not None
production_tool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(production_tool)


class DialogConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        base = Path(self._directory.name)
        subprocess.run(
            [sys.executable, str(PROJECT_TOOL), "init", str(base / "project"), "--title", "弹窗测试"],
            check=True,
            capture_output=True,
        )
        self.root = base / "project"
        self.job_file = base / "job.json"
        self.job_file.write_text(
            json.dumps(
                {
                    "job_id": "VID-DIALOG",
                    "modality": "video",
                    "adapter": "minimax-hailuo-2.3-fast",
                    "prompt": "[固定] 小黑猫抬头看星星，尾巴轻轻摆动。",
                    "outputs": ["剧集/EP001/制作成果/video/dialog.mp4"],
                    "parameters": {"duration": 6, "resolution": "768P", "ratio": "9:16"},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        production_tool.prepare_job(self.root, self.job_file)
        self.receipt = production_tool._confirmation_path(self.root, "VID-DIALOG")

    def tearDown(self) -> None:
        self._directory.cleanup()

    def test_yes_writes_the_same_one_shot_receipt(self) -> None:
        shown: list[str] = []

        def click_yes(title: str, message: str) -> bool:
            shown.append(message)
            return True

        result = production_tool.confirm_with_dialog(self.root, job_id="VID-DIALOG", ask=click_yes)
        self.assertEqual(result, {"job_id": "VID-DIALOG", "state": "confirmed", "via": "dialog"})
        self.assertTrue(self.receipt.is_file())
        # The creator sees the job and its price before clicking.
        self.assertIn("VID-DIALOG", shown[0])
        self.assertIn("minimax-hailuo-2.3-fast", shown[0])
        self.assertIn("1.35 元", shown[0])
        self.assertIn("小黑猫抬头看星星", shown[0])

    def test_no_and_no_dialog_leave_the_job_unconfirmed(self) -> None:
        for answer, state in ((False, "declined"), (None, "dialog_unavailable")):
            with self.subTest(state=state):
                result = production_tool.confirm_with_dialog(
                    self.root, job_id="VID-DIALOG", ask=lambda title, message: answer
                )
                self.assertEqual(result["state"], state)
                self.assertFalse(self.receipt.exists())

    def test_cli_exit_codes(self) -> None:
        for answer, code in ((False, 3), (None, 4), (True, 0)):
            with self.subTest(answer=answer):
                with mock.patch.object(
                    production_tool, "_native_dialog", return_value=answer
                ), contextlib.redirect_stdout(io.StringIO()):
                    returned = production_tool.main(
                        ["confirm", str(self.root), "--job-id", "VID-DIALOG", "--dialog"]
                    )
                self.assertEqual(returned, code)
        self.assertTrue(self.receipt.is_file())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            production_tool.main(
                ["confirm", str(self.root), "--job-id", "VID-DIALOG", "--dialog", "--confirmation", "x"]
            )

    def test_an_image_job_says_its_price_is_not_estimated(self) -> None:
        job = {
            "job_id": "IMG-1", "modality": "image", "adapter": "gpt-image-2", "prompt": "猫",
            "outputs": ["a.png"], "parameters": {"width": 864, "height": 1536},
            "reference_bindings": [],
        }
        self.assertIn("本工具不估算", production_tool._dialog_message(job))


if __name__ == "__main__":
    unittest.main()
