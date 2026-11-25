"""Run inside the pinned ALE Python environment."""

import asyncio
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from ale_run.environments.env import _force_close_session
from ale_run.environments.images import get
from ale_run.environments.providers.docker import DockerProvider, _wait_cua_ready
from ale_run.environments.providers.gcloud import _DEFAULT_CPU_MACHINE, _parse_gce_machine_type
from ale_run.orchestration.judge_preflight import judge_requirements
from ale_run.orchestration.lifecycle import _append_prompt_suffix, _extract_score
from ale_run.tasks.driver import TaskDriver
from ale_run.tasks.loader import TaskLoader

IMAGE = get("ale-ubuntu22-docker-v1-1")


def describe(root, key):
    info = TaskLoader(str(root / "tasks" / key)).load(0)
    data = info["task_data"]
    shape = _parse_gce_machine_type(info.get("machine_type") or _DEFAULT_CPU_MACHINE)
    cpus = info.get("vcpus") or shape.vcpus
    memory = info.get("memory_gb") or shape.memory_gb
    _append_prompt_suffix(
        info,
        "",
        sandbox_metadata={
            "provider": "docker",
            "resource_limits": {"cpu_quota": cpus, "memory_limit_bytes": int(memory * 2**30)},
        },
    )
    if not data.requires_task_data or data.sandbox_task_name not in (None, data.task_name):
        raise ValueError(f"Unexpected data layout for the pinned Docker split: {key}")
    base = (
        Path(IMAGE.task_data_root)
        / data.domain_name
        / (data.sandbox_task_name or data.task_name)
        / data.variant_name
    )
    return {
        "key": key,
        "goal": info["description"],
        "cpus": cpus,
        "memory": memory,
        "timeout": info.get("timeout_s") or 7200,
        "verify": info["metadata"].get("evaluation_timeout_s") or 7200,
        "source": data.source_relpath,
        "judge": bool(judge_requirements([key])),
        "base": str(base),
    }


async def execute(phase, root, key, url):
    if not await _wait_cua_ready(url):
        raise RuntimeError(f"Desktop CUA unavailable at {url}")
    sandbox = SimpleNamespace(endpoint=url, os="linux")
    provider = DockerProvider({})
    session = provider.open_session(sandbox)

    async def reconnect():
        nonlocal session
        await _force_close_session(session)
        session = provider.open_session(sandbox)
        return session

    driver = TaskDriver(str(root / "tasks" / key), session, session_rebuilder=reconnect)
    try:
        if phase == "setup":
            await driver.setup()
            return None
        result = await driver.evaluate()
        score = _extract_score(result)
        if result.get("error") or score is None or not 0 <= score <= 1:
            raise ValueError(f"Invalid ALE evaluation: {result}")
        return {**result, "score": score}
    finally:
        await driver.close()
        await _force_close_session(session)


if __name__ == "__main__":
    phase, root, *args = sys.argv[1:]
    with redirect_stdout(sys.stderr):
        if phase == "catalog":
            result = [describe(Path(root), key) for key in args]
        else:
            result = asyncio.run(execute(phase, Path(root), *args))
    print(json.dumps(result, allow_nan=False))
