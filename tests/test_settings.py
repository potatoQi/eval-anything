import json
import os
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import envweaver as ew

from eval_anything.benchmarks import BENCHMARKS, ClawGeneral, DeepSWE, TerminalBench
from eval_anything.cli import main

CLAW_SETTINGS = (
    '{"judge":{"base_url":"http://judge","api_key":"judge-key"},"search":{"api_key":"search-key"}}'
)
TERMINAL_SETTINGS = '{"model":"openai/Qwen3.8-27B","env":{"OPENAI_API_KEY":"your-model-key"}}'
CLAW_AGENT_SETTINGS = '{"model":{"model":"Qwen3.8-27B","context_window":262144}}'
CLAW, DEEP, TERMINAL = ClawGeneral.name, DeepSWE.name, TerminalBench.name


class SettingsTests(unittest.TestCase):
    def invoke(self, directory, benchmark, *options, expected_setup_proxy=None):
        argv = [
            "eval-anything",
            "run",
            benchmark,
            "--cache",
            str(directory / "cache"),
            "--output",
            str(directory / benchmark),
            *options,
        ]
        tasks = [
            SimpleNamespace(name=f"example-{n}", metadata={"web_search": False})
            for n in range(BENCHMARKS[benchmark].size)
        ]
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(sys, "argv", argv),
            patch(
                "eval_anything.benchmarks.base.Benchmark.generate", return_value=tasks
            ) as generate,
            patch("eval_anything.runner.evaluate") as evaluate,
            patch(
                "eval_anything.benchmarks.deepswe.model_network",
                return_value=nullcontext(("internal", "http://sandbox/v1", "local-token")),
            ) as gateway,
        ):
            main()
        generate.assert_called_once()
        self.assertEqual(generate.call_args.args[-1], expected_setup_proxy)
        self.evaluated_concurrency = evaluate.call_args.args[3]
        return evaluate.call_args.args[1:3], gateway

    def test_terminal_uses_selected_harness_settings(self):
        for cls in (ew.Terminus2Agent, ew.MiniSweAgent):
            with self.subTest(cls=cls), tempfile.TemporaryDirectory() as d:
                (agent, _), gw = self.invoke(
                    Path(d),
                    TERMINAL,
                    "--harness",
                    cls.__name__,
                    "--harness-settings",
                    TERMINAL_SETTINGS,
                )
                self.assertIs(type(agent), cls)
                self.assertEqual(agent.config["model"], "openai/Qwen3.8-27B")
                self.assertEqual(agent.config["env"]["OPENAI_API_KEY"], "your-model-key")
                gw.assert_not_called()

    def test_cli_passes_entire_native_mapping_to_sdk(self):
        native = {
            "model": "other",
            "options": {
                "temperature": 0,
                "llm_call_kwargs": {
                    "extra_body": {"chat_template_kwargs": {"enable_thinking": False}},
                },
            },
            "env": {"CUSTOM": "x"},
        }
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "native.json"
            settings.write_text(json.dumps(native))
            (agent, _), _ = self.invoke(
                Path(d),
                TERMINAL,
                "--harness",
                "Terminus2Agent",
                "--harness-settings",
                str(settings),
            )
            self.assertEqual(agent.config, ew.Terminus2Agent(**native).config)

    def test_deep_uses_native_mini_and_only_explicit_gateway_references(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = {
                "model": "openai/Qwen3.8-27B",
                "env": {
                    "OPENAI_API_KEY": "${GATEWAY_TOKEN}",
                    "UPSTREAM": "http://separate.example/v1",
                },
                "extra_args": ["-c", 'model.model_kwargs.api_base="${GATEWAY_URL}"'],
            }
            (agent, config), gw = self.invoke(
                Path(d),
                DEEP,
                "--harness-settings",
                json.dumps(cfg),
                "--benchmark-settings",
                '{"gateway":{"url":"http://your-gateway:8000/v1","api_key":"your-model-key"}}',
            )
            self.assertIs(type(agent), ew.MiniSweAgent)
            self.assertEqual(
                gw.call_args.args[:2], ("http://your-gateway:8000/v1", "your-model-key")
            )
            self.assertEqual(config["task_runtime_config"]["network"], "internal")
            self.assertEqual(agent.config["env"]["OPENAI_API_KEY"], "local-token")
            self.assertEqual(agent.config["env"]["UPSTREAM"], "http://separate.example/v1")
            self.assertEqual(
                agent.config["extra_args"],
                [arg.replace("${GATEWAY_URL}", "http://sandbox/v1") for arg in cfg["extra_args"]],
            )

    @unittest.skipUnless(hasattr(ew, "ClawEvalAgent"), "requires Claw SDK")
    def test_claw_native_model_is_independent_of_judge(self):
        with tempfile.TemporaryDirectory() as d:
            (agent, config), _ = self.invoke(
                Path(d),
                CLAW,
                "--benchmark-settings",
                CLAW_SETTINGS,
                "--harness-settings",
                CLAW_AGENT_SETTINGS,
            )
            self.assertEqual(agent.config["model"]["model"], "Qwen3.8-27B")
            self.assertEqual(agent.config["model"]["context_window"], 262144)
            self.assertEqual(config["verifier_env"]["CLAW_JUDGE_BASE_URL"], "http://judge")

    def test_setup_and_runtime_proxy_are_independent(self):
        setup = "http://build.example:8080"
        runtime = "http://tasks.example:9090"
        for prepare_proxy, runtime_proxy in ((setup, None), (None, runtime), (setup, runtime)):
            with (
                self.subTest(setup=prepare_proxy, runtime=runtime_proxy),
                tempfile.TemporaryDirectory() as d,
            ):
                flags = []
                if prepare_proxy:
                    flags.extend(["--setup-proxy", prepare_proxy])
                if runtime_proxy:
                    flags.extend(["--runtime-proxy", runtime_proxy])
                (_, cfg), _ = self.invoke(
                    Path(d),
                    TERMINAL,
                    "--harness-settings",
                    TERMINAL_SETTINGS,
                    *flags,
                    expected_setup_proxy=prepare_proxy,
                )
                env = cfg.get("task_runtime_config", {}).get("env", {})
                self.assertEqual(env.get("HTTP_PROXY"), runtime_proxy)
                self.assertEqual(env.get("HTTPS_PROXY"), runtime_proxy)
                self.assertNotIn(setup, str(cfg))

    @unittest.skipUnless(hasattr(ew, "ClawEvalAgent"), "requires Claw SDK")
    def test_claw_applies_runtime_proxy_to_search_only(self):
        with tempfile.TemporaryDirectory() as d:
            (_, cfg), _ = self.invoke(
                Path(d),
                CLAW,
                "--benchmark-settings",
                CLAW_SETTINGS,
                "--harness-settings",
                CLAW_AGENT_SETTINGS,
                "--runtime-proxy",
                "http://search.example",
            )
            self.assertEqual(
                cfg["task_runtime_config"]["env"],
                {"CLAW_SEARCH_PROXY_URL": "http://search.example"},
            )
            self.assertNotIn("http://search.example", str(cfg["verifier_env"]))

    def test_deep_rejects_runtime_proxy_before_preparation(self):
        with (
            patch("eval_anything.benchmarks.base.Benchmark.generate") as generate,
            patch.object(
                sys,
                "argv",
                [
                    "eval-anything",
                    "run",
                    DEEP,
                    "--benchmark-settings",
                    '{"gateway":{"url":"http://example","api_key":"test"}}',
                    "--runtime-proxy",
                    "http://tasks.example",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            main()
        generate.assert_not_called()

    def test_claw_rejects_wrong_harness_before_preparing_tasks(self):
        with (
            patch("eval_anything.benchmarks.base.Benchmark.generate") as generate,
            patch.object(
                sys,
                "argv",
                [
                    "eval-anything",
                    "run",
                    CLAW,
                    "--benchmark-settings",
                    CLAW_SETTINGS,
                    "--harness",
                    "MiniSweAgent",
                ],
            ),
            self.assertRaises(SystemExit),
        ):
            main()
        generate.assert_not_called()

    def test_json_inputs_are_equivalent_on_resume(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            settings = root / "harness.json"
            settings.write_text(TERMINAL_SETTINGS)
            limits = {"verify": 1, "setup": 2, "agent": 4}
            opts = [
                "--harness-settings",
                TERMINAL_SETTINGS,
                "--eval-settings",
                '{"max_retries":0}',
                "--concurrency",
            ]
            (_, cfg), _ = self.invoke(root, TERMINAL, *opts, json.dumps(limits))
            self.assertEqual(self.evaluated_concurrency, limits)
            self.assertEqual(cfg["max_retries"], 0)
            path = root / "limits.json"
            path.write_text(json.dumps(limits))
            self.invoke(
                root,
                TERMINAL,
                "--harness-settings",
                str(settings),
                "--eval-settings",
                '{"max_retries":0}',
                "--concurrency",
                str(path),
                "--resume",
            )
            with self.assertRaises(SystemExit):
                self.invoke(root, TERMINAL, *opts, "2", "--resume")
            self.assertNotIn("your-model-key", (root / TERMINAL / "run.json").read_text())

    def test_rejects_invalid_settings_before_task_preparation(self):
        for flag, value in (
            ("--config", "old.yaml"),
            ("--task", "example"),
            ("--trials", "2"),
            ("--concurrency", "true"),
            ("--concurrency", '{"agent":2}'),
            ("--eval-settings", "[]"),
            ("--eval-settings", '{"unknown":3}'),
            ("--harness-settings", "missing.json"),
            ("--benchmark-settings", '{"gateway":{}}'),
        ):
            with (
                self.subTest(flag=flag, value=value),
                patch("eval_anything.benchmarks.base.Benchmark.generate") as generate,
                patch.object(sys, "argv", ["eval-anything", "run", TERMINAL, flag, value]),
                self.assertRaises(SystemExit),
            ):
                main()
            generate.assert_not_called()

    def test_search_credentials_only_reach_selected_search_task_setup(self):
        from dataclasses import replace

        benchmark = ClawGeneral(
            judge={"base_url": "http://judge", "api_key": "judge-key"},
            search={"api_url": "http://search", "api_key": "search-key"},
        )
        setup = {"command": ["python", "services.py"], "timeout": 60}
        ref = ew.RuntimeRef("docker", "test-image")
        task = ew.Task(
            task_ref=Path("task.json"),
            context=[],
            cwd="/workspace",
            name="example",
            goal=[{"type": "text", "text": "example"}],
            task_runtime_ref=ref,
            verifier_runtime_ref=ref,
            setup=setup,
            verifier=ew.Verifier(entry=Path("run.py"), runtime=ew.Runtime(), timeout=60),
            metadata={"web_search": False},
        )
        with tempfile.TemporaryDirectory() as d:
            normal, search = benchmark.round_tasks(
                [task, replace(task, metadata={"web_search": True})], Path(d), 1
            )
            self.assertEqual(normal.setup, setup)
            self.assertEqual(
                search.setup["command"],
                ["env", "SERP_DEV_KEY=search-key", "SERP_API_URL=http://search", *setup["command"]],
            )
            self.assertEqual(task.setup, setup)
