"""Read official task definitions inside the immutable benchmark image."""

import json
import sys
from pathlib import Path

from claw_eval.graders.registry import get_grader
from claw_eval.models.task import TaskDefinition

root = Path("/opt/claw")
result = {}
for task_id in sys.argv[1:]:
    directory = root / "tasks" / task_id
    task = TaskDefinition.from_yaml(directory / "task.yaml")
    if task.task_id != task_id or task.user_agent.enabled:
        raise ValueError(f"Unexpected General task: {task_id}")
    get_grader(task_id, tasks_dir=root / "tasks", task_dir=directory)
    files = [
        *task.prompt.attachments,
        *(task.sandbox_files or task.environment.fixtures),
        *task.sandbox_grader_files,
        *task.local_grader_files,
    ]
    for file in files:
        if not (directory / file).is_file() and not (root / file).is_file():
            raise FileNotFoundError(f"{task_id}: {file}")
    result[task_id] = {
        "text": task.prompt.text,
        "timeout": task.environment.timeout_seconds,
        "search": any("mock_services/web_real" in s.command for s in task.services),
    }
print(json.dumps(result))
