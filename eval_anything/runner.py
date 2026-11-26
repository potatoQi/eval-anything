import fcntl
import hashlib
import json
import os
import re
from collections import defaultdict
from contextlib import ExitStack

import envweaver as ew

from .benchmarks import BENCHMARKS, ClawGeneral, LastExamDocker


def expand(value, env):
    if isinstance(value, str):
        return re.sub(r"\$\{([A-Za-z_][A-Za-z_0-9]*)\}", lambda m: env[m[1]], value)
    if isinstance(value, dict):
        return {k: expand(v, env) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v, env) for v in value]
    return value


def agent_class(benchmark, harness):
    if not isinstance(harness, str) or not harness.isidentifier():
        raise ValueError("harness must be an EnvWeaver Agent class name")
    if benchmark.fixed_harness and harness != benchmark.harness:
        raise ValueError(f"{benchmark.name} requires {benchmark.harness}")
    for other in BENCHMARKS.values():
        if other.fixed_harness and harness == other.harness and benchmark.name != other.name:
            raise ValueError(f"{harness} requires {other.name} tasks")
    cls = getattr(ew, harness, None)
    if not isinstance(cls, type) or not issubclass(cls, ew.Agent) or cls is ew.Agent:
        raise ValueError(f"Installed EnvWeaver does not provide Agent {harness}")
    if isinstance(benchmark, ClawGeneral):
        from .benchmarks.claw.agent import ClawEvalAgent

        return ClawEvalAgent
    if isinstance(benchmark, LastExamDocker) and harness == LastExamDocker.harness:
        from .benchmarks.last_exam.agent import AleClawAgent

        return AleClawAgent
    return cls


def run(args):
    name = args.benchmark
    env = dict(os.environ)
    bench_settings = expand(args.benchmark_settings, env)
    try:
        benchmark = BENCHMARKS[name](**bench_settings)
    except TypeError as exc:
        raise ValueError(f"Invalid --benchmark-settings for {name}: {exc}") from exc
    harness = args.harness or benchmark.harness
    cls = agent_class(benchmark, harness)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with ExitStack() as stack:
        lock = stack.enter_context((out / ".lock").open("a"))
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record = out / "run.json"
        if not args.resume and any(p.name != ".lock" for p in out.iterdir()):
            raise ValueError("Output exists. Choose a new --output or use --resume")
        previous = None
        if args.resume:
            previous = json.loads(record.read_text()) if record.is_file() else {}
            if previous.get("benchmark") != name or not previous.get("revision"):
                raise ValueError("--resume requires a matching run.json with a source revision")
        benchmark.revision = benchmark.resolve_revision(
            previous["revision"] if args.resume else None, args.setup_proxy
        )
        values = env | {k: "${" + k + "}" for k in benchmark.bindings}
        native = benchmark.harness_defaults.get(harness, {}) | args.harness_settings
        agent_settings = expand(native, values)
        eval_cfg = expand(args.eval_settings, env)
        ew.EvalConfig(**eval_cfg)
        runtime = benchmark.runtime(
            output=out, proxy=args.runtime_proxy, config=eval_cfg, agent=agent_settings
        )
        fingerprint = {
            "agent": agent_settings,
            "eval": eval_cfg,
            "benchmark": bench_settings,
            "concurrency": args.concurrency,
        }
        if benchmark.pool_directory != "pool":
            fingerprint["task_pool"] = benchmark.pool_directory
        signature = {
            "benchmark": name,
            "revision": benchmark.revision,
            "harness": harness,
            "trials": benchmark.trials,
            "settings_sha256": hashlib.sha256(
                json.dumps(fingerprint, sort_keys=True).encode()
            ).hexdigest(),
            "proxy": args.runtime_proxy,
        }

        def checkpoint():
            tmp = record.with_suffix(".tmp")
            tmp.write_text(json.dumps(signature, indent=2))
            tmp.replace(record)

        if previous is None:
            checkpoint()
        elif {k: v for k, v in previous.items() if k != "tasks"} != signature:
            raise ValueError("--resume requires matching run settings and tasks")

        cache = args.cache.resolve() / name / benchmark.revision
        cache.mkdir(parents=True, exist_ok=True)
        tasks = benchmark.generate(cache, args.setup_proxy)
        signature["tasks"] = [t.name for t in tasks]
        if previous is not None and "tasks" in previous:
            if previous["tasks"] != signature["tasks"]:
                raise ValueError("--resume requires matching run settings and tasks")
        else:
            checkpoint()
        bindings = stack.enter_context(runtime)
        agent = cls(**(expand(native, env | bindings) if bindings else agent_settings))
        evaluate(tasks, agent, eval_cfg, args.concurrency, benchmark, out, args.resume)


def evaluate(tasks, agent, eval_cfg, concurrency, benchmark, out, resume):
    valid = invalid = 0
    total_score = 0.0

    def run_round(number):
        nonlocal valid, invalid, total_score
        batches = defaultdict(list)
        for task in benchmark.round_tasks(tasks, out, number):
            timeout = eval_cfg.get("agent_timeout") or benchmark.agent_timeout(task)
            batches[timeout].append(task)
        for timeout, group in batches.items():
            dest = out / f"round-{number}" / f"eval-{timeout:g}"
            results = ew.eval(
                tasks=group,
                agent=agent,
                config=ew.EvalConfig(**{**eval_cfg, "agent_timeout": timeout}),
                concurrency=concurrency,
                output_dir=dest,
                resume=resume and (dest / "manifest.json").exists(),
            )
            if sorted(r["name"] for r in results) != sorted(t.name for t in group):
                raise ValueError(f"Incomplete or duplicate evaluation results in {dest}")
            for r in results:
                v = r["verification_result"] if r["score_valid"] else {}
                if (score := v.get("score")) is None:
                    invalid += 1
                else:
                    valid += 1
                    total_score += score
                yield r["name"], v

    metrics = benchmark.run_protocol(tasks, run_round)
    result = {
        "tasks": len(tasks),
        "trials_per_task": benchmark.trials,
        "valid": valid,
        "invalid": invalid,
        "mean_valid_score": total_score / valid if valid else None,
        **metrics,
    }
    text = json.dumps(result, indent=2)
    (out / "summary.tmp").write_text(text + "\n")
    (out / "summary.tmp").replace(out / "summary.json")
    print(text)
