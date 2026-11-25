"""Pair each trusted task controller with its own live Docker desktop."""

from dataclasses import replace
from pathlib import Path

import envweaver as ew

from ..docker import docker

DRIVER = Path(__file__).with_name("assets") / "driver.py"


class DesktopRuntime(ew.Runtime):
    def __init__(self, *, root, label, key, controller, timeout):
        super().__init__(root=root)
        self.label = label
        self.key = key
        self.controller = controller
        self.timeout = timeout

    def start(self, ref, context=None, *, setup=None, connection=None):
        if ref.image == self.controller:
            # Solver is already up. Resolve by a run- and task-specific label, even on retry.
            ids = docker("ps", "--no-trunc", "--filter", f"label={self.label}", "-q").split()
            if len(ids) != 1:
                raise ValueError(f"Expected one active desktop for {self.key}, found {len(ids)}")
            ref = replace(ref, config={**ref.config, "network": "container:" + ids[0]})
            context = [{"source": str(DRIVER), "target": "/opt/ea-driver.py"}]
            setup = {
                "command": [
                    "/opt/ale/.venv/bin/python",
                    "/opt/ea-driver.py",
                    "setup",
                    "/opt/ale",
                    self.key,
                    "http://127.0.0.1:5000",
                ],
                "timeout": self.timeout,
            }
        return super().start(ref, context=context, setup=setup, connection=connection)


class DesktopVerifier(ew.Verifier):
    def __init__(self, *, entry, runtime, timeout, base, reference, required_env):
        super().__init__(entry=entry, runtime=runtime, timeout=timeout, required_env=required_env)
        self.base = base
        self.reference = reference

    def run(self, verifier_instance, *, task_instance=None, files=None, env=None):
        # Local docker cp streams large reference directories directly into the desktop.
        if self.reference is not None:
            target = self.base + "/reference"
            code, _, error = self.runtime.exec(
                instance=task_instance, command=["rm", "-rf", "--", target]
            )
            if code:
                raise RuntimeError(error)
            self.runtime.upload(instance=task_instance, source=self.reference, target=target)
        # Only reference staging needs the task instance. The official grader uses CUA.
        grader = ew.Verifier(
            entry=self.entry,
            runtime=self.runtime,
            timeout=self.timeout,
            required_env=self.required_env,
            requires_task_instance=False,
        )
        return grader.run(verifier_instance, files=files, env=env)
