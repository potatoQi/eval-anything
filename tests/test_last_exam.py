import json
import os
import subprocess
import tempfile
import unittest
import uuid
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import envweaver as ew

from eval_anything.benchmarks.last_exam import LastExamDocker
from eval_anything.benchmarks.last_exam.execution import DesktopRuntime, DesktopVerifier
from eval_anything.runner import evaluate, run


class ExamTests(unittest.TestCase):
    def test_run_pins_before_setup_and_resume_ignores_branch_updates(self):
        first, second = "1" * 40, "2" * 40
        ref = "refs/heads/standalone-ale-claw"
        tasks = [SimpleNamespace(name=str(n)) for n in range(78)]
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.object(
                LastExamDocker, "git", side_effect=[f"{first}\t{ref}", f"{second}\t{ref}"]
            ) as git,
            patch.object(
                LastExamDocker,
                "generate",
                side_effect=[OSError("interrupted"), tasks, tasks, tasks[::-1], tasks],
            ) as generate,
            patch.object(LastExamDocker, "runtime", return_value=nullcontext({})),
            patch("eval_anything.runner.evaluate") as evaluate_run,
        ):
            root = Path(temp)
            args = SimpleNamespace(
                benchmark=LastExamDocker.name,
                benchmark_settings={},
                harness=None,
                harness_settings={},
                eval_settings={},
                setup_proxy=None,
                runtime_proxy=None,
                output=root / "run",
                cache=root / "cache",
                concurrency=1,
                resume=False,
            )
            with self.assertRaisesRegex(OSError, "interrupted"):
                run(args)
            self.assertEqual(json.loads((args.output / "run.json").read_text())["revision"], first)
            args.resume = True
            run(args)
            run(args)
            self.assertEqual(git.call_count, 1)
            with self.assertRaisesRegex(ValueError, "matching run settings and tasks"):
                run(args)
            args.output = root / "new-run"
            args.resume = False
            run(args)
            self.assertEqual(
                [call.args[1].config["version"] for call in evaluate_run.call_args_list],
                [first, first, second],
            )
            self.assertEqual(
                [call.args[0].name for call in generate.call_args_list],
                [first, first, first, first, second],
            )

    def test_partial_scores_and_incomplete_coverage(self):
        bench = LastExamDocker()
        for second, expected in (({"score": 0}, 0.375), ({}, None)):
            with self.subTest(second=second):
                metrics = bench.run_protocol(
                    [1, 2], lambda _, second=second: [("a", {"score": 0.75}), ("b", second)]
                )
                self.assertEqual(metrics["mean_score"], expected)

    @unittest.skipUnless(
        os.getenv("EA_TEST_ALE_DOCKER"), "requires local python:3.12-slim-bookworm"
    )
    def test_docker_setup_pairing_reference_and_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tag = "eval-anything/test-ale:" + uuid.uuid4().hex
            fixture = root / "build"
            fixture.mkdir()
            (fixture / "Dockerfile").write_text(
                "FROM python:3.12-slim-bookworm\nCOPY driver.py /opt/ea-driver.py\n"
                "RUN mkdir -p /opt/ale/.venv/bin && ln -s /usr/local/bin/python /opt/ale/.venv/bin/python\n"
                'CMD ["sleep", "infinity"]\n'
            )
            # The trusted controller must reach its own solver on localhost. Seeing
            # the other solver's input, or seeing the answer during setup, fails the run.
            (fixture / "driver.py").write_text(
                "import json,sys,urllib.request\n"
                "phase,_,key,url=sys.argv[1:]\n"
                'def read(name): return urllib.request.urlopen(url+"/"+name).read().decode()\n'
                'assert read("input/id")==key\n'
                'if phase=="setup":\n'
                ' try: read("reference/answer")\n'
                " except urllib.error.HTTPError as e: assert e.code==404\n"
                ' else: raise ValueError("premature reference")\n'
                'else: print(json.dumps({"score":float(read("reference/answer")) if read("output/id")==key else 0}))\n'
            )
            subprocess.run(["docker", "build", "-q", "-t", tag, fixture], check=True)
            self.addCleanup(
                subprocess.run, ["docker", "image", "rm", tag], check=True, capture_output=True
            )
            data = root / "data"
            data.mkdir()
            bench = LastExamDocker(data_dir=data)
            pool = ew.TaskPool(root=root / "pool", runtime=ew.Runtime(root=root / "runtime"))
            tasks = []
            for name, score in (("first", "0"), ("second", "0.75")):
                key = "examples/" + name
                source = key + "/base"
                for kind, file, value in (("input", "id", key), ("reference", "answer", score)):
                    dest = data / source / kind
                    dest.mkdir(parents=True)
                    (dest / file).write_text(value)
                (data / source / "input" / "link").symlink_to("/work/input/id")
                spec = {
                    "key": key,
                    "goal": key,
                    "source": source,
                    "base": "/work",
                    "judge": False,
                    "cpus": 1,
                    "memory": 1,
                    "timeout": 60,
                    "verify": 60,
                }
                bench.save(pool, spec, tag)
                task = pool.load(task_ref=pool.root / name / "task.json")
                ref = ew.RuntimeRef(
                    "docker",
                    "python:3.12-slim-bookworm",
                    {
                        "command": [
                            "python",
                            "-m",
                            "http.server",
                            "5000",
                            "--bind",
                            "127.0.0.1",
                            "--directory",
                            "/work",
                        ],
                        "args": [],
                    },
                )
                tasks.append(replace(task, task_runtime_ref=ref, cwd="/work"))
            out = root / "out"
            out.mkdir()
            for resume in (False, True):
                with (
                    patch(
                        "eval_anything.benchmarks.last_exam.execution.DRIVER", fixture / "driver.py"
                    ),
                    bench.runtime(output=out, proxy=None, config={}, agent={}),
                ):
                    evaluate(tasks, Solver(), {}, 2, bench, out, resume)
                result = json.loads((out / "summary.json").read_text())
                self.assertEqual((result["valid"], result["mean_score"]), (2, 0.375))

    def test_controller_rejects_ambiguous_solver(self):
        runtime = DesktopRuntime(root=None, label="scope", key="a", controller="ctl", timeout=60)
        with (
            patch("eval_anything.benchmarks.last_exam.execution.docker", return_value="a\nb"),
            self.assertRaisesRegex(ValueError, "found 2"),
        ):
            runtime.start(ew.RuntimeRef("docker", "ctl"))

    def test_controller_grades_without_a_task_relay(self):
        runtime = ew.Runtime()
        verifier = DesktopVerifier(
            entry=Path(__file__).with_name("run.py"),
            runtime=runtime,
            timeout=60,
            base="/work",
            reference=None,
            required_env=["JUDGE_API_KEY"],
        )
        verifier.timeout = 12  # Eval can override the task's verifier timeout.
        controller, desktop = object(), object()
        env = {"JUDGE_API_KEY": "test"}
        with patch.object(ew.Verifier, "run", autospec=True) as run:
            verifier.run(controller, task_instance=desktop, env=env)
        grader, instance = run.call_args.args
        self.assertIs(instance, controller)
        self.assertIs(grader.runtime, runtime)
        self.assertFalse(grader.requires_task_instance)
        self.assertEqual((grader.timeout, grader.required_env), (12, ["JUDGE_API_KEY"]))
        self.assertEqual(run.call_args.kwargs, {"env": env, "files": None})


class Solver(ew.Agent):
    def run(self, *, goal, runtime, instance, output, cwd):
        code, _, error = runtime.exec(
            instance=instance,
            command="test ! -e reference && test -L input/link && cp input/link output/id",
            cwd=cwd,
        )
        if code:
            raise RuntimeError(error)
        return output.finish([])
