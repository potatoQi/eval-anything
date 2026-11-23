"""Run the pinned Claw-Eval grader on the trace and post-trial sandbox state."""

import argparse
import base64
import io
import json
import os
import re
import sys
from contextlib import redirect_stdout
from pathlib import Path

from claw_eval.cli import _grade_with_optional_params
from claw_eval.graders.llm_judge import LLMJudge
from claw_eval.graders.registry import get_grader
from claw_eval.models.scoring import compute_task_score, is_pass
from claw_eval.models.task import TaskDefinition
from claw_eval.trace.reader import load_trace
from envweaver import Runtime
from judge_log import judge_requests


def collect_evidence(instance_path, task):
    runtime = Runtime()
    instance = runtime.connect(path=instance_path)
    code, trace, err = runtime.exec(
        instance=instance,
        command=[
            "python3",
            "-c",
            "from glob import glob; p, = glob('/tmp/envweaver-agent-*/traces/*.jsonl'); print(p)",
        ],
    )
    if code:
        raise ValueError(f"Cannot locate Claw trace: {err}")
    runtime.download(instance=instance, source=trace.strip(), target="trace.jsonl")
    snapshot = {}
    if task.sandbox_grader_files or task.env_snapshot_commands or task.env_snapshot_files:
        code, _, err = runtime.exec(
            instance=instance,
            command=["python", "/opt/claw/snapshot.py", task.task_id],
            timeout=600,
        )
        if code:
            raise ValueError(f"Cannot collect Claw snapshot: {err}")
        runtime.download(
            instance=instance, source="/tmp/claw-snapshot.json", target="snapshot.json"
        )
        snapshot = json.loads(Path("snapshot.json").read_text())
    return snapshot


def grade(task_id, instance_path, trace_path):
    root = Path("/opt/claw")
    directory = root / "tasks" / task_id
    task = TaskDefinition.from_yaml(directory / "task.yaml")
    if instance_path:
        snapshot = collect_evidence(instance_path, task)
        trace_path = "trace.jsonl"
    else:
        if task.sandbox_grader_files or task.env_snapshot_commands or task.env_snapshot_files:
            raise ValueError("This task needs a sandbox snapshot to replay its grade")
        snapshot = {}
    start, messages, dispatches, media, end, audit = load_trace(trace_path)
    if start.task_id != task.task_id or not end or end.failure_modes:
        raise ValueError("Incomplete or mismatched Claw trial")
    for name in task.local_grader_files:
        snapshot[f"local_file:{name}"] = {
            "encoding": "base64",
            "content": base64.b64encode((directory / name).read_bytes()).decode(),
        }
    judge = LLMJudge(
        model_id="gemini-3-flash-preview",
        base_url=os.environ["CLAW_JUDGE_BASE_URL"],
        api_key=os.environ["CLAW_JUDGE_API_KEY"],
    )
    grader = get_grader(task.task_id, tasks_dir=root / "tasks", task_dir=directory)
    os.chdir(root)
    # Two task graders return a numeric zero after exhausting judge API retries.
    # Report an invalid score when the judge failed.
    log = io.StringIO()
    with judge_requests(judge, os.environ["CLAW_JUDGE_API_KEY"]), redirect_stdout(log):
        dimensions, calls = _grade_with_optional_params(
            grader,
            messages,
            dispatches,
            task,
            audit_data=audit,
            judge=judge,
            media_events=media,
            env_snapshot=snapshot,
        )
    attempts = re.findall(r"\[judge(?:-visual)?-retry\][^\n]*attempt (\d+)/(\d+)", log.getvalue())
    if any(int(attempt) > int(limit) for attempt, limit in attempts):
        raise RuntimeError("Claw judge exhausted its retries")
    score = compute_task_score(dimensions)
    return {
        "score": score,
        "passed": is_pass(score),
        "dimensions": dimensions.model_dump(),
        "judge_calls": calls,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--instance")
    source.add_argument("--trace")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    task_id = args.task_id or Path(__file__).with_name("task_id.txt").read_text().strip()
    with redirect_stdout(sys.stderr):
        result = grade(task_id, args.instance, args.trace)
    print(json.dumps(result))
