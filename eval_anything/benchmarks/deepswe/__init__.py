import hashlib
from contextlib import contextmanager
from pathlib import Path

from envweaver import RuntimeRef

from ..docker import build, docker, present
from ..harbor import HarborBenchmark, HarborTask
from .network import model_network

ASSETS = Path(__file__).parent / "assets"
TOOLS = "envweaver/deepswe-tools:mini-2.4.6"


class DeepSWE(HarborBenchmark):
    name = "deepswe-v1.1"
    harness = "MiniSweAgent"
    size = 113
    repo = "datacurve-ai/deep-swe"
    revision = "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea"
    checksum = "2c3178146ae5d5e4dce988691e90e79c608fcc9d2ea0c9f20ac96e137f73b94b"
    bindings = ("GATEWAY_URL", "GATEWAY_TOKEN")

    def __init__(self, *, gateway):
        if (
            not isinstance(gateway, dict)
            or set(gateway) != {"url", "api_key"}
            or not all(isinstance(v, str) and v.strip() for v in gateway.values())
        ):
            raise ValueError(
                "DeepSWE --benchmark-settings requires gateway with nonempty url and api_key"
            )
        self.gateway = gateway

    def prepare(self, pool, source, missing, proxy):
        if not missing:
            return
        if not present(TOOLS):
            base = "public.ecr.aws/d3j8x8q7/swe-bench-202605:kh75679ajj3b8dtd7se3h7z0a1833y6r-v1.1"
            build(
                TOOLS,
                f"FROM {base}\nRUN --mount=type=cache,target=/root/.cache/pip,sharing=locked python3 -m venv /opt/deepswe/venv && /opt/deepswe/venv/bin/pip install envweaver==0.0.2 mini-swe-agent==2.4.6 'mcp>=2.3,<3'\n",
                proxy,
            )
        tools_id = docker("image", "inspect", "-f", "{{.Id}}", TOOLS)
        for name in missing:
            task = HarborTask(source / "tasks" / name)
            tag = (
                "eval-anything/deepswe:"
                + hashlib.sha256((task.image_id + tools_id).encode()).hexdigest()[:24]
            )
            if not present(tag):
                build(
                    tag,
                    f"FROM {TOOLS} AS tools\nFROM {task.image}\nCOPY --link --from=tools /opt/deepswe /opt/deepswe\n",
                    proxy,
                )
            image = docker("image", "inspect", "-f", "{{.Id}}", tag)
            ref = RuntimeRef(
                "docker",
                image,
                dict(task.resources, network="none", python="/opt/deepswe/venv/bin/python"),
            )
            task.cwd = "/app"
            task.save(
                pool,
                ref,
                ref,
                ASSETS / "verify.py",
                settings={key: task.verifier[key] for key in ("collect", "timeout_sec")},
                settings_file="config.json",
                context=[{"source": str(ASSETS / "mini.yaml"), "target": "/opt/deepswe/mini.yaml"}],
            )

    def runtime(self, *, output, proxy, config, agent):
        if proxy:
            raise ValueError("DeepSWE uses an isolated task network; omit --runtime-proxy")
        return self.connect(output, config)

    @contextmanager
    def connect(self, output, config):
        with model_network(
            self.gateway["url"], self.gateway["api_key"], output / ".gateway.json"
        ) as (network, url, token):
            config.setdefault("task_runtime_config", {})["network"] = network
            yield {"GATEWAY_URL": url, "GATEWAY_TOKEN": token}
