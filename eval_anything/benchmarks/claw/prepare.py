import json
import shutil
import tempfile
from pathlib import Path

from envweaver import RuntimeRef, Verifier

from ..docker import build_args, docker, present

ASSETS = Path(__file__).parent / "assets"
RUNNER_INPUTS = ("pyproject.toml", "src", "mock_services")
CLAW_IMAGE = "eval-anything/claw-general:5680b8b"


def build_runner(source, names, proxy):
    # The upstream .dockerignore applies to the sidecar. The runner needs its own
    # filter, including when an existing cache contains all three splits.
    with tempfile.TemporaryDirectory() as temp:
        recipe = Path(temp) / "Dockerfile"
        shutil.copyfile(ASSETS / "Dockerfile", recipe)
        inputs = [*RUNNER_INPUTS, *(f"tasks/{name}" for name in names)]
        recipe.with_suffix(".dockerignore").write_text("*\n" + "".join(f"!{p}\n" for p in inputs))
        docker(
            "build",
            *(
                build_args(proxy)
                if proxy
                else ["--build-arg", "HTTPS_PROXY", "--build-arg", "HTTP_PROXY"]
            ),
            "--build-arg",
            "PYTHON_BASE_IMAGE",
            "--build-arg",
            "PIP_INDEX_URL",
            "--build-context",
            f"adapter={ASSETS}",
            "-f",
            recipe,
            "-t",
            CLAW_IMAGE,
            source,
        )


def ensure_claw(source, names, proxy):
    if not present("claw-eval-agent:latest"):
        docker(
            "build",
            *build_args(proxy),
            "-f",
            source / "Dockerfile.agent",
            "-t",
            "claw-eval-agent:latest",
            source,
        )
    if not present(CLAW_IMAGE):
        build_runner(source, names, proxy)


def save_claw(pool, names):
    image = docker("image", "inspect", "-f", "{{.Id}}", CLAW_IMAGE)
    defs = json.loads(
        docker(
            "run",
            "--rm",
            "-i",
            "--network",
            "none",
            "--entrypoint",
            "python",
            image,
            "-",
            *names,
            input=(ASSETS / "inventory.py").read_text(),
        )
    )
    config = {"args": ["--entrypoint", "sleep"], "command": ["infinity"], "python": "python"}
    ref = RuntimeRef("docker", image, config)
    with tempfile.TemporaryDirectory() as temp:
        materials = Path(temp) / "verifier"
        shutil.copytree(
            ASSETS / "verifier", materials, ignore=shutil.ignore_patterns("__pycache__")
        )
        for name in names:
            entry = defs[name]
            (materials / "task_id.txt").write_text(name)
            pool.save(
                name=name,
                goal=[{"type": "text", "text": entry["text"]}],
                metadata={"web_search": entry["search"], "timeout_seconds": entry["timeout"]},
                task_runtime_ref=ref,
                verifier_runtime_ref=ref,
                cwd=f"/opt/claw/tasks/{name}",
                setup={"command": ["python", "/opt/claw/services.py", name], "timeout": 600},
                verifier=Verifier(entry=materials / "run.py", runtime=pool.runtime, timeout=3600),
            )
