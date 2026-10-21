"""MiniMax region, built-in video model catalog, Hailuo v1, prices and setup."""

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SUITE = Path(__file__).resolve().parents[1]
SCRIPT = SUITE / "skills/short-drama-produce/scripts/provider_adapters.py"
SPEC = importlib.util.spec_from_file_location("short_drama_provider_adapters_catalog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
provider_adapters = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(provider_adapters)
TOOL = SUITE / "skills/short-drama-produce/scripts/production_tool.py"
TOOL_SPEC = importlib.util.spec_from_file_location("short_drama_production_tool_catalog", TOOL)
assert TOOL_SPEC is not None and TOOL_SPEC.loader is not None
production_tool = importlib.util.module_from_spec(TOOL_SPEC)
TOOL_SPEC.loader.exec_module(production_tool)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 16
# Only what a test sets is visible: no real key, region or credentials file.
CLEAN = {
    "MINIMAX_API_KEY": "",
    "MINIMAX_REGION": "",
    "MINIMAX_BASE_URL": "",
    "MINIMAX_VIDEO_BASE_URL": "",
    "MINIMAX_VIDEO_MODEL": "",
    "MINIMAX_AIGC_WATERMARK": "",
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "",
}


def clean_environment(**values: str) -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items()
        if name not in CLEAN and not name.startswith("MINIMAX_") and name != "DRAMA_SKILLS_ENV"
    }
    environment.update(values)
    return environment


def isolated(**values: str):  # type: ignore[no-untyped-def]
    return mock.patch.dict(os.environ, clean_environment(**values), clear=True)


def frame_binding(path: str, role: str, order: int) -> dict[str, object]:
    return {
        "slot_id": f"REF-{order}",
        "order": order,
        "path": path,
        "label": "起始帧" if role == "first_frame" else "结束帧",
        "role": role,
        "may_control": ["构图"],
        "must_not_control": ["动作"],
    }


def video_job(*, references: list[str] = (), roles: list[str] = (), **parameters: object) -> dict:
    return {
        "modality": "video",
        "prompt": "[推进] 少年抬手，剑光亮起",
        "references": list(references),
        "reference_bindings": [
            frame_binding(path, role, index)
            for index, (path, role) in enumerate(zip(references, roles), 1)
        ],
        "outputs": ["制作成果/shot.mp4"],
        "parameters": parameters,
    }


class Response:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self.headers = {}

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self, *_: object) -> bytes:
        payload, self._payload = self._payload, b""
        return payload


class RegionTests(unittest.TestCase):
    def requested_hosts(self, **environment: str) -> list[tuple[str, str]]:
        requested: list[tuple[str, str]] = []

        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            url = request if isinstance(request, str) else request.full_url
            method = "GET" if isinstance(request, str) else request.method
            requested.append((method, url))
            if method == "POST":
                return Response(b'{"task_id": "task-1"}')
            if "/query/" in url:
                return Response(
                    b'{"task": {"status": "succeeded",'
                    b' "content": {"url": "https://cdn.example/out.mp4"}}}'
                )
            return Response(MP4)

        with tempfile.TemporaryDirectory() as directory:
            job = video_job(duration=6, ratio="9:16", resolution="768P")
            job.update(project_root=directory, output_root=directory)
            with isolated(MINIMAX_API_KEY="secret", **environment), mock.patch.object(provider_adapters.urllib.request, "urlopen", fake_urlopen):
                provider_adapters._run_minimax_video(job, "MiniMax-H3")
        return requested

    def test_china_site_is_the_default(self) -> None:
        requested = self.requested_hosts()
        self.assertEqual(requested[0], ("POST", "https://api.minimaxi.com/v2/video_generation"))
        self.assertEqual(
            requested[1], ("GET", "https://api.minimaxi.com/v2/query/video_generation/task-1")
        )

    def test_global_region_and_explicit_base_url(self) -> None:
        self.assertEqual(
            self.requested_hosts(MINIMAX_REGION="global")[0][1],
            "https://api.minimax.io/v2/video_generation",
        )
        self.assertEqual(
            self.requested_hosts(MINIMAX_VIDEO_BASE_URL="https://proxy.example/v2")[0][1],
            "https://proxy.example/v2/video_generation",
        )

    def test_unknown_region_fails_closed(self) -> None:
        with isolated(MINIMAX_REGION="mars"):
            with self.assertRaises(provider_adapters.AdapterFailure) as raised:
                provider_adapters._minimax_base("MINIMAX_BASE_URL", "v1")
        self.assertEqual(raised.exception.code, "invalid_region")


class CatalogProfileTests(unittest.TestCase):
    def compile(self, model: str, **parameters: object) -> dict:
        with isolated():
            ratios, resolutions, durations = provider_adapters._minimax_video_runtime_profile(model)
        return provider_adapters.compile_minimax_h3_payload(
            video_job(**parameters),
            model=model,
            allowed_ratios=ratios,
            allowed_resolutions=resolutions,
            duration_range=durations,
        )

    def test_built_in_profiles_follow_the_published_envelopes(self) -> None:
        self.assertEqual(self.compile("MiniMax-H3", duration=4, ratio="9:16", resolution="2K")["duration"], 4)
        self.assertEqual(
            self.compile("MiniMax-H3-Max", duration=5, ratio="9:16", resolution="480P")["resolution"],
            "480P",
        )
        for model, parameters in (
            ("MiniMax-H3-Max", {"duration": 4, "ratio": "9:16", "resolution": "768P"}),
            ("MiniMax-H3-Max", {"duration": 6, "ratio": "9:16", "resolution": "2K"}),
            ("MiniMax-H3", {"duration": 6, "ratio": "9:16", "resolution": "480P"}),
            ("MiniMax-H3", {"duration": 16, "ratio": "9:16", "resolution": "768P"}),
        ):
            with self.subTest(model=model, parameters=parameters):
                with self.assertRaises(ValueError):
                    self.compile(model, **parameters)

    def test_an_unlisted_model_still_needs_an_explicit_profile(self) -> None:
        with isolated():
            self.assertEqual(
                provider_adapters._minimax_video_runtime_profile("some-new-model"),
                (None, None, None),
            )

    def test_watermark_is_on_unless_switched_off(self) -> None:
        with isolated():
            self.assertIs(provider_adapters._minimax_aigc_watermark(), True)
        with isolated(MINIMAX_AIGC_WATERMARK="false"):
            self.assertIs(provider_adapters._minimax_aigc_watermark(), False)
        with isolated(MINIMAX_AIGC_WATERMARK="maybe"):
            with self.assertRaises(provider_adapters.AdapterFailure):
                provider_adapters._minimax_aigc_watermark()


class HailuoCompilerTests(unittest.TestCase):
    compile = staticmethod(provider_adapters.compile_minimax_hailuo_payload)
    START = "data:image/png;base64,AAAA"
    END = "data:image/png;base64,BBBB"

    def test_first_frame_job_compiles_to_plain_fields(self) -> None:
        job = video_job(
            references=["start.png"], roles=["first_frame"],
            duration=10, ratio="9:16", resolution="768P", prompt_language="zh-CN",
        )
        body = self.compile(
            job, model="MiniMax-Hailuo-2.3-Fast",
            reference_urls=[self.START], reference_roles=["first_frame"],
        )
        self.assertEqual(
            body,
            {
                "model": "MiniMax-Hailuo-2.3-Fast",
                "prompt": job["prompt"],
                "duration": 10,
                "resolution": "768P",
                "prompt_optimizer": False,
                "first_frame_image": self.START,
            },
        )

    def test_first_and_last_frame_only_on_hailuo_02(self) -> None:
        job = video_job(
            references=["start.png", "end.png"], roles=["first_frame", "last_frame"],
            duration=6, resolution="1080P",
        )
        body = self.compile(
            job, model="MiniMax-Hailuo-02",
            reference_urls=[self.START, self.END], reference_roles=["first_frame", "last_frame"],
        )
        self.assertEqual(body["last_frame_image"], self.END)
        with self.assertRaises(ValueError):
            self.compile(
                job, model="MiniMax-Hailuo-2.3",
                reference_urls=[self.START, self.END], reference_roles=["first_frame", "last_frame"],
            )
        low = video_job(
            references=["start.png", "end.png"], roles=["first_frame", "last_frame"],
            duration=6, resolution="512P",
        )
        with self.assertRaises(ValueError):
            self.compile(
                low, model="MiniMax-Hailuo-02",
                reference_urls=[self.START, self.END], reference_roles=["first_frame", "last_frame"],
            )

    def test_published_limits_are_enforced(self) -> None:
        cases = (
            # Fast is image-to-video only.
            ("MiniMax-Hailuo-2.3-Fast", video_job(duration=6, resolution="768P"), [], []),
            # 1080P is 6 seconds only.
            ("MiniMax-Hailuo-2.3", video_job(duration=10, resolution="1080P"), [], []),
            # Text-to-video renders landscape only.
            ("MiniMax-Hailuo-2.3", video_job(duration=6, ratio="9:16", resolution="768P"), [], []),
            # 512P needs a first frame.
            ("MiniMax-Hailuo-02", video_job(duration=6, resolution="512P"), [], []),
            # No multi-reference conditioning on v1.
            (
                "MiniMax-Hailuo-2.3",
                video_job(references=["a.png"], roles=["reference_image"], duration=6, resolution="768P"),
                [self.START],
                ["reference_image"],
            ),
            # A last frame alone is not a request Hailuo accepts.
            (
                "MiniMax-Hailuo-02",
                video_job(references=["end.png"], roles=["last_frame"], duration=6, resolution="768P"),
                [self.END],
                ["last_frame"],
            ),
            # H3 is not compiled by the v1 compiler.
            ("MiniMax-H3", video_job(duration=6, resolution="768P"), [], []),
        )
        for model, job, urls, roles in cases:
            with self.subTest(model=model, parameters=job["parameters"], roles=roles):
                with self.assertRaises(ValueError):
                    self.compile(job, model=model, reference_urls=urls, reference_roles=roles)
        long_prompt = {**video_job(duration=6, resolution="768P"), "prompt": "长" * 2001}
        with self.assertRaises(ValueError):
            self.compile(long_prompt, model="MiniMax-Hailuo-2.3")
        landscape = self.compile(
            video_job(duration=6, ratio="16:9", resolution="768P"), model="MiniMax-Hailuo-2.3"
        )
        self.assertNotIn("ratio", landscape)


class HailuoRuntimeTests(unittest.TestCase):
    def run_job(self, responses: dict[str, list[bytes]], **environment: str):
        requested: list[tuple[str, str, object]] = []

        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            url = request if isinstance(request, str) else request.full_url
            method = "GET" if isinstance(request, str) else request.method
            body = None
            if not isinstance(request, str) and request.data:
                body = json.loads(request.data)
            requested.append((method, url, body))
            for fragment, queue in responses.items():
                if fragment in url:
                    return Response(queue.pop(0))
            raise AssertionError(f"unexpected request: {method} {url}")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "start.png").write_bytes(PNG)
            outputs = root / "out"
            outputs.mkdir()
            job = video_job(
                references=["start.png"], roles=["first_frame"], duration=6, resolution="768P"
            )
            job.update(project_root=str(root), output_root=str(outputs))
            with isolated(MINIMAX_API_KEY="secret", **environment), mock.patch.object(
                provider_adapters.urllib.request, "urlopen", fake_urlopen
            ), mock.patch.object(provider_adapters.time, "sleep"), mock.patch.object(
                provider_adapters, "_reference_paths", return_value=[root / "start.png"]
            ):
                try:
                    path, task_id = provider_adapters._run_minimax_video(
                        job, "MiniMax-Hailuo-2.3-Fast"
                    )
                    return requested, path.read_bytes(), task_id
                except provider_adapters.AdapterFailure as exc:
                    return requested, exc, None

    def test_submits_polls_retrieves_and_downloads(self) -> None:
        requested, content, task_id = self.run_job(
            {
                "/v1/video_generation": [b'{"task_id": "t1", "base_resp": {"status_code": 0}}'],
                "/v1/query/video_generation?task_id=t1": [
                    b'{"task_id": "t1", "status": "Queueing", "base_resp": {"status_code": 0}}',
                    b'{"task_id": "t1", "status": "Processing", "base_resp": {"status_code": 0}}',
                    b'{"task_id": "t1", "status": "Success", "file_id": 205258526306433,'
                    b' "base_resp": {"status_code": 0}}',
                ],
                "/v1/files/retrieve?file_id=205258526306433": [
                    b'{"file": {"download_url": "https://cdn.example/hailuo.mp4"},'
                    b' "base_resp": {"status_code": 0}}'
                ],
                "https://cdn.example/hailuo.mp4": [MP4],
            }
        )
        self.assertEqual((content, task_id), (MP4, "t1"))
        method, url, body = requested[0]
        self.assertEqual((method, url), ("POST", "https://api.minimaxi.com/v1/video_generation"))
        self.assertEqual(body["model"], "MiniMax-Hailuo-2.3-Fast")
        self.assertTrue(body["first_frame_image"].startswith("data:image/png;base64,"))
        self.assertIs(body["aigc_watermark"], True)
        self.assertIs(body["prompt_optimizer"], False)
        self.assertEqual(body["prompt"], "[推进] 少年抬手，剑光亮起")

    def test_failed_and_rejected_tasks_fail_closed(self) -> None:
        _, failure, _ = self.run_job(
            {
                "/v1/video_generation": [b'{"task_id": "t1", "base_resp": {"status_code": 0}}'],
                "/v1/query/video_generation": [
                    b'{"task_id": "t1", "status": "Fail", "base_resp": {"status_code": 0}}'
                ],
            }
        )
        self.assertEqual(failure.code, "task_failed")
        _, rejected, _ = self.run_job(
            {
                "/v1/video_generation": [
                    b'{"task_id": "", "base_resp": {"status_code": 1008, "status_msg": "secret text"}}'
                ],
            }
        )
        self.assertEqual(rejected.code, "1008")
        self.assertNotIn("secret text", json.dumps(rejected.public("minimax-video")))

    def test_collect_resumes_without_submitting_again(self) -> None:
        requested: list[str] = []

        def fake_urlopen(request, timeout=None):  # type: ignore[no-untyped-def]
            url = request if isinstance(request, str) else request.full_url
            requested.append(url)
            if "/query/" in url:
                return Response(b'{"status": "Success", "file_id": "f1", "base_resp": {"status_code": 0}}')
            if "/files/retrieve" in url:
                return Response(b'{"file": {"download_url": "https://cdn.example/a.mp4"}}')
            return Response(MP4)

        with tempfile.TemporaryDirectory() as directory:
            job = video_job(duration=6, resolution="768P")
            job.update(output_root=directory, collect_provider_job_id="t9")
            with isolated(MINIMAX_API_KEY="secret"), mock.patch.object(provider_adapters.urllib.request, "urlopen", fake_urlopen):
                _, task_id = provider_adapters._run_minimax_video(job, "MiniMax-Hailuo-02")
        self.assertEqual(task_id, "t9")
        self.assertFalse(any(url.endswith("/video_generation") for url in requested))


class PriceTests(unittest.TestCase):
    def test_official_prices(self) -> None:
        price = provider_adapters.minimax_video_price
        self.assertEqual(price("MiniMax-H3", "768P", 6), 3.00)
        self.assertEqual(price("MiniMax-H3", "2K", 10), 8.00)
        self.assertEqual(price("MiniMax-H3-Max", "480P", 10), 3.30)
        self.assertEqual(price("MiniMax-Hailuo-2.3-Fast", "768P", 6), 1.35)
        self.assertEqual(price("MiniMax-Hailuo-2.3-Fast", "1080P", 6), 2.31)
        self.assertEqual(price("MiniMax-Hailuo-02", "512P", 10), 1.00)
        for model, resolution, duration in (
            ("MiniMax-Hailuo-2.3-Fast", "1080P", 10),
            ("MiniMax-H3", "768P", 3),
            ("MiniMax-H3-Max", "2K", 6),
            ("unknown", "768P", 6),
        ):
            with self.subTest(model=model, resolution=resolution, duration=duration):
                with self.assertRaises(ValueError):
                    price(model, resolution, duration)

    def test_table_lists_every_model_with_prices_and_no_plan_allowance(self) -> None:
        with isolated():
            table = provider_adapters.price_table()
        for model, entry in provider_adapters.MINIMAX_VIDEO_MODELS.items():
            self.assertIn(f"| {model} | `{entry['profile']}` |", table)
        self.assertIn(provider_adapters.MINIMAX_PRICE_SOURCE, table)
        self.assertNotIn("%", table)
        with isolated(MINIMAX_REGION="global"):
            self.assertIn("国际站", provider_adapters.price_table())

    def test_estimate_prices_a_planned_film_on_every_offered_combination(self) -> None:
        table = provider_adapters.estimate_table([10, 10, 10])
        self.assertIn("3 段视频（10、10、10 秒，共 30 秒）", table)
        self.assertIn("| MiniMax-Hailuo-2.3-Fast | `minimax-hailuo-2.3-fast` | 768P | 6.75 元 |", table)
        self.assertIn("| MiniMax-H3 | `minimax-h3` | 768P | 15.00 元 |", table)
        self.assertIn("| MiniMax-H3 | `minimax-h3` | 2K | 24.00 元 |", table)
        self.assertIn("MiniMax-Hailuo-2.3-Fast 1080P", table.split("改镜长：", 1)[1])
        self.assertLess(table.index("6.75 元"), table.index("24.00 元"))
        self.assertNotIn("%", table)
        odd = provider_adapters.estimate_table([8])
        self.assertIn("| MiniMax-H3 | `minimax-h3` | 768P | 4.00 元 |", odd)
        self.assertNotIn("| MiniMax-Hailuo-2.3-Fast |", odd)
        for bad in ([], [0], [True]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                provider_adapters.estimate_table(bad)

    def test_quote_prices_each_job_by_profile_or_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = {
                "a.json": {"job_id": "SHOT-1", "modality": "video", "adapter": "minimax-h3",
                           "parameters": {"duration": 6, "resolution": "768P"}},
                "b.json": {"job_id": "SHOT-2", "modality": "video", "adapter": "minimax-hailuo-2.3-fast",
                           "parameters": {"duration": 10, "resolution": "768P"}},
                "c.json": {"job_id": "IMG-1", "modality": "image", "adapter": "gpt-image-2"},
                "d.json": {"job_id": "SHOT-3", "modality": "video", "adapter": "my-fast",
                           "parameters": {"duration": 6, "resolution": "1080P"}},
            }
            for name, job in jobs.items():
                (root / name).write_text(json.dumps(job), encoding="utf-8")
            config = root / "adapters.json"
            config.write_text(json.dumps({"adapters": {"my-fast": {
                "command": ["python", "x.py", "minimax-video", "--model", "MiniMax-Hailuo-2.3-Fast"]
            }}}), encoding="utf-8")
            quote = provider_adapters.quote_jobs(
                [root / name for name in jobs], model=None, config_path=config
            )
            self.assertIn("| SHOT-1 | MiniMax-H3 | 768P | 6 秒 | 3.00 元 |", quote)
            self.assertIn("| SHOT-2 | MiniMax-Hailuo-2.3-Fast | 768P | 10 秒 | 2.25 元 |", quote)
            self.assertIn("| SHOT-3 | MiniMax-Hailuo-2.3-Fast | 1080P | 6 秒 | 2.31 元 |", quote)
            self.assertIn("IMG-1", quote)
            self.assertIn("合计 3 段视频、22 秒，按官方价约 7.56 元", quote)
            forced = provider_adapters.quote_jobs(
                [root / "a.json"], model="MiniMax-H3-Max", config_path=None
            )
            self.assertIn("MiniMax-H3-Max", forced)
            (root / "e.json").write_text(json.dumps({**jobs["a.json"], "adapter": "mystery"}), encoding="utf-8")
            with self.assertRaises(ValueError):
                provider_adapters.quote_jobs([root / "e.json"], model=None, config_path=None)


class CredentialsFileTests(unittest.TestCase):
    def load(self, text: str, **environment: str) -> dict[str, str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "providers.env"
            path.write_text(text, encoding="utf-8")
            with isolated(**environment, DRAMA_SKILLS_ENV=str(path)):
                provider_adapters._load_provider_env()
                return {name: os.environ.get(name, "") for name in CLEAN}

    def test_fills_unset_values_and_never_overrides_the_environment(self) -> None:
        loaded = self.load(
            "﻿# comment\n\nMINIMAX_API_KEY=sk-cp-file\nexport OPENAI_BASE_URL=\"https://api.yoyochatai.com/v1\"\n"
            "OPENAI_API_KEY='from-file'\nMINIMAX_REGION=\n",
            OPENAI_API_KEY="from-environment",
        )
        self.assertEqual(loaded["MINIMAX_API_KEY"], "sk-cp-file")
        self.assertEqual(loaded["OPENAI_BASE_URL"], "https://api.yoyochatai.com/v1")
        self.assertEqual(loaded["OPENAI_API_KEY"], "from-environment")
        self.assertEqual(loaded["MINIMAX_REGION"], "")

    def test_a_bad_line_is_reported_by_number_only(self) -> None:
        with self.assertRaises(provider_adapters.AdapterFailure) as raised:
            self.load("MINIMAX_API_KEY=ok\nsk-cp-pasted-secret-without-a-name\n")
        self.assertIn("line 2", str(raised.exception))
        self.assertNotIn("sk-cp-pasted", str(raised.exception))

    def test_refuses_a_file_inside_a_project(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "short-drama.json").write_text("{}", encoding="utf-8")
            path = project / "secrets" / "providers.env"
            path.parent.mkdir()
            path.write_text("MINIMAX_API_KEY=x\n", encoding="utf-8")
            with isolated(DRAMA_SKILLS_ENV=str(path)):
                with self.assertRaises(provider_adapters.AdapterFailure) as raised:
                    provider_adapters._load_provider_env()
        self.assertEqual(raised.exception.code, "env_file_in_project")

    def test_a_missing_named_file_fails_closed(self) -> None:
        with isolated(DRAMA_SKILLS_ENV="/nonexistent/providers.env"):
            with self.assertRaises(provider_adapters.AdapterFailure) as raised:
                provider_adapters._load_provider_env()
        self.assertEqual(raised.exception.code, "missing_env_file")

    def test_template_points_images_at_the_relay_and_holds_no_key(self) -> None:
        template = (SCRIPT.parent.parent / "references/providers.env.example").read_text(encoding="utf-8")
        values = dict(
            line.split("=", 1) for line in template.splitlines()
            if line and not line.startswith("#")
        )
        self.assertEqual(values["OPENAI_BASE_URL"], "https://api.yoyochatai.com/v1")
        self.assertEqual(values["OPENAI_API_KEY"], "")
        self.assertEqual(values["MINIMAX_API_KEY"], "")
        self.assertEqual(values["MINIMAX_REGION"], "cn")


class SetupTests(unittest.TestCase):
    def test_written_config_loads_in_the_production_tool(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "home" / "adapters.json"
            provider_adapters.write_adapter_config(config, force=False)
            with self.assertRaises(FileExistsError):
                provider_adapters.write_adapter_config(config, force=False)
            provider_adapters.write_adapter_config(config, force=True)
            project = root / "project"
            project.mkdir()
            for model, entry in provider_adapters.MINIMAX_VIDEO_MODELS.items():
                with self.subTest(model=model):
                    command, timeout, roles = production_tool._load_adapter(
                        config, entry["profile"], project
                    )
                    self.assertEqual(command[-3:], ["minimax-video", "--model", model])
                    self.assertTrue(Path(command[1]).is_absolute())
                    self.assertEqual(timeout, 3600)
                    self.assertIn("first_frame", roles)
            _, _, fast_roles = production_tool._load_adapter(config, "minimax-hailuo-2.3-fast", project)
            self.assertEqual(fast_roles, frozenset({"first_frame"}))
            for profile in ("gpt-image-2", "minimax-speech"):
                production_tool._load_adapter(config, profile, project)

    def test_cli_tables_and_check_never_print_a_key(self) -> None:
        secret = "sk-cp-credential-must-not-appear"
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / "providers.env"
            env_file.write_text(f"MINIMAX_API_KEY={secret}\nOPENAI_API_KEY=also-secret\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--check-config", "--price-table"],
                env=clean_environment(DRAMA_SKILLS_ENV=str(env_file)),
                check=False,
                capture_output=True,
                encoding="utf-8",
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("订阅套餐 Key", completed.stdout)
        self.assertIn("MiniMax-Hailuo-2.3-Fast", completed.stdout)
        self.assertNotIn(secret, completed.stdout + completed.stderr)
        self.assertNotIn("also-secret", completed.stdout + completed.stderr)

    def test_model_flag_is_limited_to_minimax_video(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "gpt-image-2", "--model", "MiniMax-H3"],
            input="{}",
            env=clean_environment(),
            check=False,
            capture_output=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 2)


if __name__ == "__main__":
    unittest.main()
