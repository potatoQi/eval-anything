import hashlib
import json
from contextlib import nullcontext
from pathlib import Path
from typing import ClassVar

import envweaver as ew

from ..base import Benchmark
from .brave import brave_gateway
from .data import GOLD, Dataset
from .submissions import export_run

ASSETS = Path(__file__).parent / "assets"
HOME = "/home/user"


class GDPval(Benchmark):
    name = "gdpval-aa-v2.1"
    revision = GOLD[1]
    size = 220
    harness = "StirrupAgent"
    fixed_harness = True
    generation_only = True
    harness_defaults: ClassVar[dict] = {"StirrupAgent": {"view_image": True}}

    def __init__(self, *, image=None, verifier_image=None, brave_api_key=None):
        self.brave_keys = []
        if brave_api_key is not None:
            keys = brave_api_key if isinstance(brave_api_key, list) else [brave_api_key]
            if not keys or not all(isinstance(key, str) and key.strip() for key in keys):
                raise ValueError("brave_api_key requires a nonempty string or list of keys")
            self.brave_keys = list(dict.fromkeys(keys))
        default_tag = "2.1-brave" if len(self.brave_keys) > 1 else "2.1"
        self.image = image or f"eval-anything/gdpval:{default_tag}"
        self.verifier_image = verifier_image or self.image
        self.pool_directory = (
            "pool-office-v1-"
            + hashlib.sha256(json.dumps([self.image, self.verifier_image]).encode()).hexdigest()[
                :16
            ]
        )

    def load_source(self, cache, proxy):
        return Dataset(cache.parent.parent, GOLD, proxy)

    def catalog(self, source):
        return sorted(source.rows)

    def prepare(self, pool, source, missing, proxy):
        if not missing:
            return
        references = source.preload("reference_files", missing, resolve=source.reference)
        config = {"command": ["sleep", "infinity"]}
        task_ref = ew.RuntimeRef("docker", self.image, config)
        verifier_ref = ew.RuntimeRef("docker", self.verifier_image, config)
        verifier = ew.Verifier(
            entry=ASSETS / "receipt/run.py",
            runtime=pool.runtime,
            timeout=60,
            requires_task_instance=False,
        )
        template = (ASSETS / "user.txt").read_text()
        without_refs = (
            template[: template.index("## Reference Files Location")]
            + template[template.index("## Completing Your Work") :]
        )
        for name in missing:
            row = source.rows[name]
            context = [
                {"source": str(references[p]), "target": f"{HOME}/{p}"}
                for p in row["reference_files"]
            ]
            if context:
                prompt = template.replace(
                    "- [absolute path to each reference file]",
                    "\n".join("- " + c["target"] for c in context),
                )
            else:
                prompt = without_refs
            pool.save(
                name=name,
                goal=[
                    {"type": "text", "text": prompt.replace("[task description]", row["prompt"])}
                ],
                cwd=HOME,
                metadata={"agent_timeout": 10800},
                task_runtime_ref=task_ref,
                context=context,
                verifier_runtime_ref=verifier_ref,
                verifier=verifier,
            )

    def runtime(self, *, output, proxy, config, agent):
        self.output = output
        required = {
            "version": "0.2.2",
            "max_turns": 250,
            "command_timeout": 600,
            "context_summarization_cutoff": 0.7,
            "allow_abandon": True,
            "web": True,
            "submission_dir": HOME,
            "system_prompt_mode": "replace",
            "system_prompt": (ASSETS / "system.txt").read_text(),
        }
        for k, v in required.items():
            if k in agent and agent[k] != v:
                raise ValueError(f"GDPval-AA requires the official submission setting for {k}")
        agent.update(required)
        for key in ("model", "context_window_tokens"):
            if key not in agent:
                raise ValueError(f"Set {key} for the selected model")
        env = agent.setdefault("env", {})
        if proxy:
            env.update(HTTP_PROXY=proxy, HTTPS_PROXY=proxy)
        if len(self.brave_keys) > 1:
            network = (config.get("task_runtime_config") or {}).get("network") or "bridge"
            return brave_gateway(output, env, self.brave_keys, network, proxy, self.image)
        if self.brave_keys:
            env["BRAVE_API_KEY"] = self.brave_keys[0]
        elif not env.get("BRAVE_API_KEY"):
            raise ValueError("GDPval-AA requires brave_api_key or harness env.BRAVE_API_KEY")
        return nullcontext({})

    def run_protocol(self, tasks, run_round):
        entries = export_run(self.output, {t.name for t in tasks}, run_round)
        return {
            "submissions": len(entries),
            "submitted": sum(v["status"] == "submitted" for v in entries.values()),
            "empty": sum(v["status"] == "empty" for v in entries.values()),
            "errors": sum(v["status"] == "error" for v in entries.values()),
        }
