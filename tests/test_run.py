import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from eval_anything.benchmarks import ClawGeneral, DeepSWE, TerminalBench
from eval_anything.runner import evaluate
from eval_anything.sources import unpack


class EvaluationTests(unittest.TestCase):
    def test_source_download_is_checked_before_publication_and_reused_offline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "distribution.tar.gz"
            with tarfile.open(archive, "w:gz") as tar:
                entry = tarfile.TarInfo("upstream/tasks/example/instruction.md")
                entry.size = 4
                tar.addfile(entry, io.BytesIO(b"task"))
                tar.addfile(tarfile.TarInfo("upstream/tasks/example/solution/solve.sh"))
            destination = root / "source"
            with self.assertRaisesRegex(ValueError, "checksum"):
                unpack(archive.as_uri(), "wrong", destination, None)
            self.assertFalse(destination.exists())
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            unpack(
                archive.as_uri(), digest, destination, None, include=TerminalBench().includes_source
            )
            self.assertFalse((destination / "tasks/example/solution").exists())
            archive.unlink()
            unpack(archive.as_uri(), digest, destination, None)
            self.assertEqual((destination / "tasks/example/instruction.md").read_text(), "task")

    def test_general_requires_pass_in_every_trial_and_counts_invalid(self):
        tasks = [SimpleNamespace(name=n, metadata={"timeout_seconds": 600}) for n in ("a", "b")]

        def result(name, valid=True):
            return {
                "name": name,
                "score_valid": valid,
                "verification_result": {"score": 0.8, "passed": True} if valid else None,
            }

        rounds = [
            [result("a"), result("b")],
            [result("a"), result("b", False)],
            [result("a"), result("b")],
        ]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch("eval_anything.runner.ew.eval", side_effect=rounds),
            patch("eval_anything.benchmarks.claw.for_round", side_effect=lambda task, *_: task),
        ):
            evaluate(
                tasks,
                object(),
                {},
                1,
                ClawGeneral(
                    judge={"base_url": "http://judge", "api_key": "judge-key"},
                    search={"api_key": "search-key"},
                ),
                Path(temp),
                False,
            )
            summary = json.loads((Path(temp) / "summary.json").read_text())
            self.assertEqual(
                (summary["valid"], summary["invalid"], summary["pass_cubed"]), (5, 1, 0.5)
            )

    def test_full_protocols_run_every_task_and_count_invalid_scores(self):
        for bench in (
            TerminalBench(),
            DeepSWE(gateway={"url": "http://model", "api_key": "key"}),
            ClawGeneral(
                judge={"base_url": "http://judge", "api_key": "key"}, search={"api_key": "key"}
            ),
        ):
            with self.subTest(benchmark=bench.name), tempfile.TemporaryDirectory() as temp:
                tasks = [
                    SimpleNamespace(
                        name=f"task-{i}", metadata={"timeout_seconds": 60, "agent_timeout": 60}
                    )
                    for i in range(bench.size)
                ]

                def simulated_eval(*, tasks, output_dir, size=bench.size, **kwargs):
                    self.assertEqual(len(tasks), size)
                    results = []
                    for i, t in enumerate(tasks):
                        invalid = i == 0 and output_dir.parent.name == "round-1"
                        results.append(
                            {
                                "name": t.name,
                                "score_valid": not invalid,
                                "verification_result": None
                                if invalid
                                else {"score": 1, "passed": True},
                            }
                        )
                    return results

                with (
                    patch.object(bench, "round_tasks", side_effect=lambda tasks, *_: tasks),
                    patch("eval_anything.runner.ew.eval", side_effect=simulated_eval) as sdk,
                ):
                    evaluate(tasks, object(), {}, 4, bench, Path(temp), False)
                summary = json.loads((Path(temp) / "summary.json").read_text())
                rounds = 3 if isinstance(bench, ClawGeneral) else 1
                self.assertEqual(sdk.call_count, rounds)
                self.assertEqual(
                    (summary["tasks"], summary["valid"], summary["invalid"]),
                    (bench.size, bench.size * rounds - 1, 1),
                )
                self.assertEqual(summary["passed_all_trials"], bench.size - 1)
                self.assertEqual(summary["pass_all_rate"], (bench.size - 1) / bench.size)
                self.assertEqual("pass_cubed" in summary, isinstance(bench, ClawGeneral))

    def test_claw_runner_selects_the_general_split_from_a_full_checkout(self):
        from eval_anything.benchmarks.claw.prepare import build_runner

        def check_build(*args):
            recipe = Path(args[args.index("-f") + 1])
            rules = recipe.with_suffix(".dockerignore").read_text().splitlines()
            self.assertEqual(rules[0], "*")
            self.assertEqual([r for r in rules if r.startswith("!tasks/")], ["!tasks/T1"])
            self.assertIn("!src", rules)
            self.assertIn("--build-context", args)

        with patch("eval_anything.benchmarks.claw.prepare.docker", side_effect=check_build):
            build_runner(Path("/checkout"), ["T1"], None)
