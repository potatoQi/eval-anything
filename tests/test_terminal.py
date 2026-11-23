import unittest

from eval_anything.benchmarks.terminal import TerminalBench
from eval_anything.benchmarks.terminal.preparation import torch_grader, uvx_command


class TerminalPreparationTests(unittest.TestCase):
    def test_network_profile_follows_task_and_preserves_gateway_bypass(self):
        bench = TerminalBench(
            apt_mirror="https://mirror.example",
            pypi_index="https://mirror.example/simple",
            direct_hosts=["mirror.example"],
            retries=5,
            timeout=42,
        )
        self.assertNotEqual(bench.pool_directory, TerminalBench().pool_directory)
        self.assertEqual(
            bench.network_command[-4:], ["https://mirror.example", "5", "42", "mirror.example"]
        )
        cfg = {"task_runtime_config": {"env": {"NO_PROXY": "other.example"}}}
        bench.runtime(
            proxy="http://proxy:8888",
            config=cfg,
            agent={"api_base": "https://gateway.example/v1"},
            output=None,
        )
        env = cfg["task_runtime_config"]["env"]
        self.assertTrue(
            {"mirror.example", "other.example", "gateway.example"}
            <= set(env["no_proxy"].split(","))
        )
        self.assertEqual(env["http_proxy"], "http://proxy:8888")

    def test_invalid_network_settings(self):
        for args in [
            {"apt_mirror": "https://host/a;cmd"},
            {"direct_hosts": "host"},
            {"pypi_index": "https://secret@host/simple"},
            {"retries": True},
            {"timeout": 0},
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                TerminalBench(**args)

    def test_prepared_grader_keeps_official_test_and_reward_commands(self):
        setup = (
            "#!/bin/bash\napt-get update\napt-get install -y curl\n"
            "curl -LsSf https://astral.sh/uv/0.9.5/install.sh | sh\n"
        )
        body = (
            "source $HOME/.local/bin/env\nuvx \\\n -p 3.13 -w pytest==8.4.1 -w torch==2.7.1 "
            "pytest --ctrf /logs/verifier/ctrf.json /tests/test_outputs.py\necho $?\n"
        )
        self.assertEqual(torch_grader(setup + body), body)
        self.assertEqual(
            uvx_command(body),
            "/root/.local/bin/uvx -p 3.13 -w pytest==8.4.1 -w torch==2.7.1 pytest --version",
        )
        with self.assertRaises(ValueError):
            torch_grader(setup + "prepare-task-data\n" + body)
        with self.assertRaises(ValueError):
            uvx_command(body.replace("-p 3.13", "-p 3.12"))
