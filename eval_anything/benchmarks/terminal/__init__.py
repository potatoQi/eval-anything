import shlex
from contextlib import nullcontext
from urllib.parse import urlsplit

from envweaver import RuntimeRef

from ..docker import build, present
from ..harbor import HarborBenchmark, HarborTask
from .downloads import ASSETS, with_download_cache
from .preparation import QEMU, TORCH, identifier, network, prepared_image, torch_grader

TB_VERIFIER = "envweaver/tb21-verifier:20261028"


def endpoint_hosts(value):
    if isinstance(value, str) and value.startswith(("http://", "https://")):
        yield urlsplit(value).hostname
    elif isinstance(value, (list, dict)):
        for item in value.values() if isinstance(value, dict) else value:
            yield from endpoint_hosts(item)


class TerminalBench(HarborBenchmark):
    name = "terminal-bench-2.1"
    harness = "Terminus2Agent"
    size = 89
    repo = "harbor-framework/terminal-bench-2-1"
    revision = "7131e4375048a0e408a8fb404b5f499d726b695b"
    checksum = "aa992a8848a1f3ed5191a34fa72ed5700d67dd9f968413a0e0a567b6188cb527"

    def __init__(
        self, *, apt_mirror=None, pypi_index=None, direct_hosts=None, retries=3, timeout=60
    ):
        self.direct_hosts = [] if direct_hosts is None else direct_hosts
        self.network_command = network(apt_mirror, self.direct_hosts, retries, timeout)
        if pypi_index is not None:
            if not isinstance(pypi_index, str):
                raise ValueError("pypi_index must be an HTTP(S) URL")
            parsed = urlsplit(pypi_index)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.query
                or parsed.fragment
                or any(c.isspace() for c in pypi_index)
            ):
                raise ValueError(
                    "pypi_index must be a public HTTP(S) index URL without credentials"
                )
        self.pypi_index = pypi_index
        self.retries = retries
        self.timeout = timeout
        self.pool_directory = "pool-" + identifier(
            ["prepared-v2", self.network_command, pypi_index]
        )

    def prepare(self, pool, source, missing, proxy):
        derived = QEMU | TORCH
        index = self.pypi_index or "https://pypi.org/simple"

        def prepare_image(task):
            return prepared_image(task, proxy, index, self.direct_hosts, self.retries, self.timeout)

        for name in sorted(derived.difference(missing)):
            task = HarborTask(source / "tasks" / name)
            saved = pool.load(task_ref=pool.root / name / "task.json")
            if prepare_image(task) != saved.task_runtime_ref.image:
                raise ValueError(
                    f"{name}: prepared image changed; generate a new pool with a fresh --cache"
                )
        if not missing:
            return
        if not present(TB_VERIFIER):
            build(
                TB_VERIFIER,
                "FROM python:3.12-slim-bookworm\nRUN --mount=type=cache,target=/root/.cache/pip,sharing=locked pip install envweaver==0.0.2\n",
                proxy,
            )
        verifier = pool.runtime.create(
            backend="docker", image=TB_VERIFIER, config={"command": ["sleep", "infinity"]}
        )
        for name in missing:
            task = HarborTask(source / "tasks" / name)
            image = prepare_image(task) if name in derived else task.image_id
            if name == "install-windows-3.11":
                # The image's default CMD starts the web and VNC services via supervisord.
                del task.resources["command"]
            env = task.resources.setdefault("env", {})
            env.update(
                PIP_RETRIES=str(self.retries),
                PIP_DEFAULT_TIMEOUT=str(self.timeout),
                UV_HTTP_RETRIES=str(self.retries),
                UV_HTTP_TIMEOUT=str(self.timeout),
            )
            if self.direct_hosts:
                env["no_proxy"] = env["NO_PROXY"] = ",".join(self.direct_hosts)
            if self.pypi_index:
                env["PIP_INDEX_URL"] = env["UV_DEFAULT_INDEX"] = self.pypi_index
            task.resources["python_prepare_command"] = shlex.join(self.network_command)
            ref = RuntimeRef("docker", image, task.resources)
            settings = {"cwd": task.cwd, "timeout": task.verifier["timeout_sec"]}
            if name in TORCH:
                settings["prepared_grader"] = torch_grader(
                    (task.folder / "tests/test.sh").read_text()
                )
                settings["prepared_index"] = index
            task.save(
                pool,
                ref,
                verifier,
                ASSETS / "grader/run.py",
                settings_file="settings.json",
                settings=settings,
                setup={"command": self.network_command, "timeout": 180},
            )

    def round_tasks(self, tasks, output, number):
        return [with_download_cache(task) for task in tasks]

    def runtime(self, *, proxy, config, agent, output):
        if proxy:
            env = config.setdefault("task_runtime_config", {}).setdefault("env", {})
            skip = [
                "localhost",
                "127.0.0.1",
                "::1",
                *endpoint_hosts(agent),
                *self.direct_hosts,
                env.get("no_proxy"),
                env.get("NO_PROXY"),
            ]
            bypass = ",".join(dict.fromkeys(filter(None, skip)))
            env.update(
                http_proxy=proxy,
                https_proxy=proxy,
                HTTP_PROXY=proxy,
                HTTPS_PROXY=proxy,
                NO_PROXY=bypass,
                no_proxy=bypass,
            )
        return nullcontext({})
