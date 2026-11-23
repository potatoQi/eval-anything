"""Collect the official post-trial grading evidence from this attempt's tool server."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from claw_eval.cli import _collect_env_snapshot
from claw_eval.models.task import TaskDefinition
from claw_eval.runner.sandbox_runner import SandboxRunner

directory = Path("/opt/claw/tasks") / sys.argv[1]
task = TaskDefinition.from_yaml(directory / "task.yaml")
handle = SimpleNamespace(sandbox_url=Path("/tmp/envweaver-claw-tools.url").read_text().strip())
count = SandboxRunner.inject_grader_files(handle, task, task_dir=directory)
if count != len(task.sandbox_grader_files):
    raise ValueError("Incomplete grading fixtures")
snapshot = _collect_env_snapshot(handle.sandbox_url, task)
Path("/tmp/claw-snapshot.json").write_text(json.dumps(snapshot))
