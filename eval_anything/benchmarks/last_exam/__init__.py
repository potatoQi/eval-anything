import fcntl
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

import envweaver as ew

from ..base import Benchmark
from ..docker import build_args, docker
from .execution import DesktopRuntime, DesktopVerifier

ASSETS = Path(__file__).parent / "assets"
LIST = "selected_tasks/docker_support.txt"
DESKTOP = "agentslastexam/ale-ubuntu22-docker:v1.1@sha256:0b223c5042bedfa78f37f17881f6b410aa3d745310b667e5dff62637363c5085"


def resolve_desktop():
    digest = DESKTOP.split("@")[1]
    # Registry mirrors can store the same pinned manifest under a different repository.
    images = docker("image", "ls", "--digests", "--no-trunc", "--format", "{{.Digest}} {{.ID}}")
    for line in images.splitlines():
        manifest, image_id = line.split()
        if manifest == digest:
            return image_id
    docker("pull", DESKTOP)
    return docker("image", "inspect", "--format", "{{.Id}}", DESKTOP)


class LastExamDocker(Benchmark):
    name = "agents-last-exam-docker"
    harness = "AleClawAgent"
    size = 78
    repo = "potatoQi/agents-last-exam"
    branch = "standalone-ale-claw"

    @property
    def harness_defaults(self):
        return {self.harness: {"port": 5000, "version": self.revision}}

    def git(self, proxy, *args):
        return subprocess.check_output(
            ["git", *(["-c", f"http.proxy={proxy}"] if proxy else []), *args], text=True
        ).strip()

    @property
    def git_url(self):
        return f"https://github.com/{self.repo}.git"

    def resolve_revision(self, saved, proxy):
        if saved is None:
            ref = f"refs/heads/{self.branch}"
            saved, _, resolved = self.git(
                proxy, "ls-remote", "--exit-code", self.git_url, ref
            ).partition("\t")
            if resolved != ref:
                raise ValueError(f"Cannot resolve {self.repo} branch {self.branch}")
        if not isinstance(saved, str) or not re.fullmatch("[0-9a-f]{40}", saved):
            raise ValueError("Invalid ALE source commit in run.json")
        return saved

    def fetch_source(self, source, proxy):
        if source.is_dir():
            return
        source.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=source.parent) as temp:
            tree = Path(temp) / "source"
            self.git(proxy, "init", "--quiet", tree)
            self.git(
                proxy,
                "-C",
                tree,
                "fetch",
                "--quiet",
                "--no-tags",
                "--depth=1",
                self.git_url,
                self.revision,
            )
            if self.git(proxy, "-C", tree, "rev-parse", "FETCH_HEAD") != self.revision:
                raise ValueError("Fetched ALE commit differs from the run revision")
            self.git(proxy, "-C", tree, "checkout", "--quiet", "--detach", self.revision)
            shutil.rmtree(tree / ".git")
            tree.rename(source)

    def __init__(self, *, data_dir=None):
        self.data = Path(data_dir).expanduser().resolve(strict=True) if data_dir else None

        stamp = hashlib.sha256()
        for file in (
            ASSETS / "Dockerfile",
            ASSETS / "driver.py",
            ASSETS / "run.py",
            Path(__file__),
        ):
            stamp.update(file.read_bytes())
        stamp.update(str(self.data).encode())
        self.pool_directory = "pool-" + stamp.hexdigest()[:16]

    def catalog(self, source):
        release = json.loads((source / "releases/v1.1/assets.json").read_text())
        selection = (source / LIST).read_bytes()
        if hashlib.sha256(selection).hexdigest() != release["selected_task_lists"][LIST]["sha256"]:
            raise ValueError("ALE v1.1 Docker task list checksum mismatch")
        self.keys = {
            line.split("/")[1]: line
            for raw in selection.decode().splitlines()
            if (line := raw.strip()) and not line.startswith("#")
        }
        return list(self.keys)

    def prepare(self, pool, source, missing, proxy):
        if self.data is None:
            release = json.loads((source / "releases/v1.1/assets.json").read_text())
            digest = release["task_data"]["archive_sha256"]
            if not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("Invalid ALE data archive checksum")
            # Code revisions often share the same 39 GB data archive.
            data_cache = pool.root.parent.parent / "data" / digest
            data_cache.mkdir(parents=True, exist_ok=True)
            self.data = data_cache / "task-data-v1.1"
            with (data_cache / "download.lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                if not self.data.is_dir():
                    subprocess.run(
                        [
                            "bash",
                            source / "scripts/fetch_task_data.sh",
                            "--revision",
                            release["huggingface"]["archive"]["revision"],
                            "--sha256",
                            digest,
                            self.data,
                        ],
                        check=True,
                        env=os.environ
                        | ({"https_proxy": proxy, "http_proxy": proxy} if proxy else {}),
                    )
        if not self.data.is_dir():
            raise ValueError(f"Expected the extracted v1.1 task data directory: {self.data}")
        controller = self.build_controller(source, proxy)
        self.desktop_image = resolve_desktop()
        if not missing:
            return
        specs = json.loads(
            docker(
                "run",
                "--rm",
                "-i",
                "--network",
                "none",
                controller,
                "python",
                "-",
                "catalog",
                "/opt/ale",
                *(self.keys[n] for n in missing),
                input=(ASSETS / "driver.py").read_text(),
            )
        )
        if [s["key"] for s in specs] != [self.keys[n] for n in missing]:
            raise ValueError("Incomplete ALE task descriptions")
        for spec in specs:
            self.save(pool, spec, controller)

    def build_controller(self, source, proxy):
        recipe = ASSETS / "Dockerfile"
        digest = hashlib.sha256(self.revision.encode() + recipe.read_bytes()).hexdigest()[:20]
        tag = f"eval-anything/ale-controller:{digest}"
        if not docker("image", "ls", "-q", tag):
            network = (
                ["--network", "host"]
                if proxy and urlsplit(proxy).hostname in {"localhost", "127.0.0.1", "::1"}
                else []
            )
            docker("build", *network, *build_args(proxy), "-f", recipe, "-t", tag, source)
        return tag

    def save(self, pool, spec, controller):
        name = spec["key"].split("/")[1]
        base = Path(spec["base"])
        folder = self.data / spec["source"]
        if not (folder / "input").is_dir() or (
            (folder / "reference.7z").exists() and not (folder / "reference").is_dir()
        ):
            raise ValueError(f"Expected unpacked private v1.1 task data at {folder}")
        # TaskPool materializes directory symlinks. Archive the official tree as-is
        # so desktop-side links (including absolute paths) survive the transfer.
        target = str(base / ".ea-inputs.tar")
        commands = [
            shlex.join(["tar", "-xf", target, "-C", str(base)]),
            shlex.join(["rm", "--", target]),
            shlex.join(["mkdir", "-p", "--", str(base / "output")]),
        ]
        software = folder / "software"
        if software.is_dir():
            commands.append(
                "find " + shlex.quote(str(base / "software")) + " -type f -exec chmod +x {} +"
            )
        with tempfile.TemporaryDirectory(dir=pool.root.parent) as temp:
            root = Path(temp)
            bundle = root / "inputs.tar"
            with tarfile.open(bundle, "w", dereference=False) as archive:
                archive.add(folder / "input", arcname="input")
                if software.is_dir():
                    archive.add(software, arcname="software")
            verifier = root / "verifier"
            verifier.mkdir()
            shutil.copyfile(ASSETS / "run.py", verifier / "run.py")
            (verifier / "spec.json").write_text(json.dumps({"key": spec["key"]}))
            pool.save(
                name=name,
                cwd="/home/user",
                goal=[{"type": "text", "text": spec["goal"]}],
                metadata={
                    "agent_timeout": spec["timeout"],
                    "ale_key": spec["key"],
                    "data_source": spec["source"],
                    "data_base": str(base),
                },
                context=[{"source": str(bundle), "target": target}],
                setup={"command": " && ".join(commands), "timeout": 600},
                task_runtime_ref=ew.RuntimeRef(
                    "docker",
                    DESKTOP,
                    {
                        "args": ["--init", "--entrypoint", "/dockerstartup/entrypoint.sh"],
                        "command": ["--wait"],
                        "cpus": spec["cpus"],
                        "memory": f"{spec['memory']}g",
                        "shm_size": "2g",
                        "env": {
                            "LANG": "C.UTF-8",
                            "LC_ALL": "C.UTF-8",
                            "ALE_ENABLE_DIND": "0",
                            "ALE_SCREEN_RESOLUTION": "1024x768",
                        },
                    },
                ),
                verifier_runtime_ref=ew.RuntimeRef(
                    "docker", controller, {"python": "/opt/ale/.venv/bin/python"}
                ),
                verifier=ew.Verifier(
                    entry=verifier / "run.py",
                    runtime=pool.runtime,
                    timeout=spec["verify"],
                    required_env=["JUDGE_API_KEY"] if spec["judge"] else [],
                ),
            )

    @contextmanager
    def runtime(self, *, output, proxy, config, agent):
        self.setup_timeout = config.get("setup_timeout", 600)
        self.judge_key_configured = bool(
            config.get("verifier_env", {}).get("JUDGE_API_KEY", os.environ.get("JUDGE_API_KEY"))
        )
        self.network = "ea-ale-" + hashlib.sha256(str(output).encode()).hexdigest()[:20]
        opts = config.setdefault("task_runtime_config", {})
        if {"network", "args"} & opts.keys() or "network" in config.get(
            "verifier_runtime_config", {}
        ):
            raise ValueError("ALE manages the desktop and controller network")
        if proxy:
            opts.setdefault("env", {}).update(
                HTTP_PROXY=proxy, HTTPS_PROXY=proxy, NO_PROXY="localhost,127.0.0.1"
            )
        # One bridge per run. Its desktops cannot talk to each other. Each trusted
        # controller joins just its own desktop's namespace for CUA access.
        label = "eval-anything.ale=" + self.network
        if docker("network", "ls", "--filter", f"name=^{self.network}$", "-q"):
            if (
                docker(
                    "network",
                    "inspect",
                    "-f",
                    '{{index .Labels "eval-anything.ale"}}',
                    self.network,
                )
                != self.network
            ):
                raise ValueError(f"Docker network name already in use: {self.network}")
        else:
            docker(
                "network",
                "create",
                "--label",
                label,
                "--opt",
                "com.docker.network.bridge.enable_icc=false",
                self.network,
            )
        try:
            yield {}
        finally:
            docker("network", "rm", self.network)

    def round_tasks(self, tasks, output, number):
        if any(t.verifier.required_env for t in tasks) and not self.judge_key_configured:
            raise ValueError(
                "The ALE Docker split needs JUDGE_API_KEY for judge-graded tasks; set verifier_env in --eval-settings"
            )
        for task in tasks:
            spec = task.metadata
            label = "eval-anything.desktop=" + self.network + "." + task.name
            ref = task.task_runtime_ref
            runtime = DesktopRuntime(
                root=task.verifier.runtime.root,
                label=label,
                key=spec["ale_key"],
                controller=task.verifier_runtime_ref.image,
                timeout=self.setup_timeout,
            )
            ref = replace(
                ref,
                image=self.desktop_image if ref.image == DESKTOP else ref.image,
                config={
                    **ref.config,
                    "network": self.network,
                    "args": [*ref.config["args"], "--label", label],
                },
            )
            folder = self.data / spec["data_source"]
            reference = folder / "reference"
            if not (folder / "input").is_dir():
                raise ValueError(f"Missing official task data: {folder}")
            verifier = DesktopVerifier(
                entry=task.verifier.entry,
                runtime=runtime,
                timeout=task.verifier.timeout,
                base=spec["data_base"],
                reference=reference if reference.is_dir() else None,
                required_env=task.verifier.required_env,
            )
            yield replace(task, task_runtime_ref=ref, verifier=verifier)

    def run_protocol(self, tasks, run_round):
        scores = [v.get("score") for _, v in run_round(1)]
        return {
            "mean_score": sum(scores) / len(tasks) if all(s is not None for s in scores) else None
        }
