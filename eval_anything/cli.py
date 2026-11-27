import argparse
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from .benchmarks import BENCHMARKS
from .runner import run


def proxy_url(value):
    try:
        url = urlsplit(value)
        if (
            url.scheme in ("http", "https")
            and url.hostname
            and url.username is None
            and url.password is None
            and url.path in ("", "/")
            and not url.query
            and not url.fragment
            and url.port != 0
        ):
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("expected an HTTP(S) proxy URL without credentials")


def json_value(raw):
    text = raw.strip()
    try:
        if not text.startswith(("{", "[")) and text.endswith(".json"):
            text = Path(text).read_text(encoding="utf-8")
        return json.loads(text)
    except (ValueError, OSError) as exc:
        raise argparse.ArgumentTypeError(f"expected JSON or a .json file: {exc}") from exc


def json_object(raw):
    value = json_value(raw)
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return value


def concurrency(raw):
    value = json_value(raw)
    if type(value) is int and value > 0:
        return value
    if (
        isinstance(value, dict)
        and set(value) == {"setup", "agent", "verify"}
        and all(type(n) is int and n > 0 for n in value.values())
    ):
        return value
    raise argparse.ArgumentTypeError(
        "expected a positive integer or positive setup, agent and verify limits"
    )


def main():
    parser = argparse.ArgumentParser(prog="eval-anything")
    commands = parser.add_subparsers(required=True)
    command = commands.add_parser("run")
    command.set_defaults(handler=run)
    command.add_argument("benchmark", choices=BENCHMARKS)
    command.add_argument(
        "--harness", help="EnvWeaver Agent class (defaults to the benchmark preset)"
    )
    for flag, description in (
        ("--harness-settings", "constructor arguments for the selected EnvWeaver Agent"),
        ("--eval-settings", "EnvWeaver EvalConfig constructor arguments"),
        ("--benchmark-settings", "options interpreted by the selected benchmark"),
    ):
        command.add_argument(
            flag, type=json_object, default={}, metavar="JSON|FILE.json", help=description
        )
    command.add_argument(
        "--concurrency",
        type=concurrency,
        default=1,
        metavar="N|JSON|FILE.json",
        help="parallel evaluations (default 1), or separate setup/agent/verify limits",
    )
    command.add_argument(
        "--cache",
        type=Path,
        default=Path(".cache/eval-anything"),
        help="task and source cache (default .cache/eval-anything)",
    )
    command.add_argument(
        "--output",
        type=Path,
        default=Path("outputs/run"),
        help="results directory (default outputs/run)",
    )
    command.add_argument(
        "--setup-proxy",
        type=proxy_url,
        metavar="URL",
        help="proxy for upstream task downloads, fixtures and image build dependencies",
    )
    command.add_argument(
        "--runtime-proxy",
        type=proxy_url,
        metavar="URL",
        help="HTTP(S) proxy for benchmark runtime traffic",
    )

    command.add_argument(
        "--resume", action="store_true", help="continue the matching run in --output"
    )
    from .benchmarks.gdpval.score import score

    scoring = commands.add_parser("score", help="blind pairwise evaluation of saved submissions")
    scoring.add_argument("benchmark", choices=("gdpval-aa-v2.1",))
    scoring.add_argument(
        "--left", required=True, help="run directory, submissions.json, gpt-5-low or human-expert"
    )
    scoring.add_argument("--right", required=True, help="second submission source")
    scoring.add_argument("--judges", required=True, type=json_object, metavar="JSON|FILE.json")
    scoring.add_argument("--renderer-image", default="eval-anything/gdpval:2.1")
    scoring.add_argument("--output", required=True, type=Path)
    scoring.add_argument("--cache", type=Path, default=Path(".cache/eval-anything"))
    scoring.add_argument("--setup-proxy", type=proxy_url)
    scoring.add_argument(
        "--task", action="append", help="score a task ID; repeat to select several"
    )
    scoring.add_argument("--seed", type=int, default=0)
    scoring.add_argument("--concurrency", type=int, default=1)
    scoring.add_argument("--resume", action="store_true")
    scoring.set_defaults(handler=score)
    try:
        args = parser.parse_args()
        args.handler(args)
    except (ValueError, TypeError, KeyError, OSError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"eval-anything: {exc}\n")


if __name__ == "__main__":
    main()
