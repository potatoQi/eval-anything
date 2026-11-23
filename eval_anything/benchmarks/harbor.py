import json
import shutil
import tempfile
import tomllib
from pathlib import Path

from envweaver import Verifier

from .base import Benchmark
from .docker import docker, present


class HarborBenchmark(Benchmark):
    def includes_source(self, parts):
        return (
            len(parts) >= 3
            and parts[0] == "tasks"
            and parts[2] in {"task.toml", "instruction.md", "tests"}
        )

    def catalog(self, source):
        return sorted(p.parent.name for p in (source / "tasks").glob("*/task.toml"))

    def run_protocol(self, tasks, run_round):
        results = run_round(1)
        passed = sum(v.get("score") == 1 for _, v in results)
        return {"passed_all_trials": passed, "pass_all_rate": passed / len(tasks)}


class HarborTask:
    def __init__(self, folder):
        self.folder = folder
        cfg = tomllib.loads((folder / "task.toml").read_text())
        self.agent_timeout = cfg["agent"]["timeout_sec"]
        self.verifier = cfg["verifier"]
        env = cfg["environment"]
        self.image = env["docker_image"]
        if not present(self.image):
            docker("pull", self.image)
        self.image_id, _, cwd = docker(
            "image", "inspect", "-f", "{{.Id}} {{.Config.WorkingDir}}", self.image
        ).partition(" ")
        self.cwd = cwd or "/"
        self.resources = {
            "cpus": env["cpus"],
            "memory": f"{env['memory_mb']}m",
            "command": ["sleep", "infinity"],
        }
        if env.get("env"):
            self.resources["env"] = env["env"]

    def save(
        self,
        pool,
        task_ref,
        verifier_ref,
        script,
        *,
        settings,
        settings_file,
        context=(),
        setup=None,
    ):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # TaskPool.save materializes these files into the standalone package.
            (root / "tests").symlink_to((self.folder / "tests").resolve(), target_is_directory=True)
            shutil.copyfile(script, root / "run.py")
            (root / settings_file).write_text(json.dumps(settings))
            pool.save(
                name=self.folder.name,
                goal=[{"type": "text", "text": (self.folder / "instruction.md").read_text()}],
                metadata={"agent_timeout": self.agent_timeout},
                context=list(context),
                setup=setup,
                cwd=self.cwd,
                task_runtime_ref=task_ref,
                verifier_runtime_ref=verifier_ref,
                verifier=Verifier(
                    entry=root / "run.py",
                    runtime=pool.runtime,
                    timeout=self.verifier["timeout_sec"] + 600,
                ),
            )
