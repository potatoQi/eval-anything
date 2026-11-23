from contextlib import nullcontext

from ..base import Benchmark
from .prepare import ASSETS, RUNNER_INPUTS, ensure_claw, save_claw
from .rounds import for_round


class ClawGeneral(Benchmark):
    name = "claw-eval-general"
    harness = "ClawEvalAgent"
    size = 161
    trials = 3
    fixed_harness = True
    repo = "claw-eval/claw-eval"
    revision = "5680b8b11ff2ee5dd2b07b89086a29a5c5c984d7"
    checksum = "dcd009657da238a0b2b20b90606a61f2d1197a2810e190c9c7431f66c8caab97"

    def __init__(self, *, judge, search):
        if (
            not isinstance(judge, dict)
            or set(judge) != {"base_url", "api_key"}
            or not all(isinstance(v, str) and v.strip() for v in judge.values())
        ):
            raise ValueError("Claw judge requires base_url and api_key")
        if (
            not isinstance(search, dict)
            or not {"api_key"} <= search.keys() <= {"api_key", "api_url"}
            or not all(isinstance(v, str) and v.strip() for v in search.values())
        ):
            raise ValueError("Claw search requires api_key and optionally api_url")
        self.judge = judge
        self.search = search

    names = tuple((ASSETS / "task_ids.txt").read_text().splitlines())

    def includes_source(self, parts):
        if parts[0] == "tasks":
            return len(parts) >= 2 and parts[1] in self.names
        return parts[0] in {
            ".dockerignore",
            "Dockerfile.agent",
            "requirements-sandbox-server.txt",
            *RUNNER_INPUTS,
        }

    def catalog(self, source):
        return self.names

    def prepare(self, pool, source, missing, proxy):
        ensure_claw(source, self.names, proxy)
        if missing:
            save_claw(pool, missing)

    def agent_timeout(self, task):
        return max(10800, task.metadata["timeout_seconds"] + 300)

    def round_tasks(self, tasks, output, number):
        return [for_round(t, output, number, self.search) for t in tasks]

    def run_protocol(self, tasks, run_round):
        passed = {task.name for task in tasks}
        for number in range(1, self.trials + 1):
            results = run_round(number)
            passed &= {name for name, v in results if v.get("passed", False)}
        rate = len(passed) / len(tasks)
        return {"passed_all_trials": len(passed), "pass_all_rate": rate, "pass_cubed": rate}

    def runtime(self, *, proxy, config, agent, output):
        config.setdefault("verifier_env", {}).update(
            CLAW_JUDGE_BASE_URL=self.judge["base_url"], CLAW_JUDGE_API_KEY=self.judge["api_key"]
        )
        if proxy:
            config.setdefault("task_runtime_config", {}).setdefault("env", {})[
                "CLAW_SEARCH_PROXY_URL"
            ] = proxy
        return nullcontext({})
