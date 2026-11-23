"""Start a trial's mock services; signal readiness to EnvWeaver setup."""

import os
import signal
import subprocess
import sys
from pathlib import Path

from claw_eval.models.task import TaskDefinition
from claw_eval.runner.services import ServiceManager

root = Path("/opt/claw")
task_id = sys.argv[1]
if len(sys.argv) == 2:
    reader, writer = os.pipe()
    with open("/tmp/claw-services.log", "w") as log:
        subprocess.Popen(
            [sys.executable, __file__, task_id, str(writer)],
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            pass_fds=(writer,),
        )
    os.close(writer)
    with os.fdopen(reader, "rb") as signal_pipe:
        if signal_pipe.read(1) != b"1":
            raise RuntimeError(Path("/tmp/claw-services.log").read_text()[-2000:])
else:
    task = TaskDefinition.from_yaml(root / "tasks" / task_id / "task.yaml")
    os.environ["ERROR_RATE"] = "0"
    proxy = os.environ.pop("CLAW_SEARCH_PROXY_URL", "")
    if proxy:
        bypass = ",".join(
            filter(
                None, ("localhost,127.0.0.1,::1", os.getenv("no_proxy") or os.getenv("NO_PROXY"))
            )
        )
        for service in task.services:
            if service.name in {"web_real", "web_real_injection"}:
                service.env.update(
                    HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY=bypass, no_proxy=bypass
                )
    with ServiceManager(task.services, cwd=root, mock_today=task.environment.mock_today):
        with os.fdopen(int(sys.argv[2]), "wb") as ready:
            ready.write(b"1")
        signal.pause()
