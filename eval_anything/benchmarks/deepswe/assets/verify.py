"""Run the original DeepSWE verifier in EnvWeaver's separate verifier sandbox."""

import argparse
import base64
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
from pathlib import Path

from envweaver import Runtime
from envweaver.errors import EnvWeaverError


def read_reward(logs):
    """Use Pier's file precedence; reject DeepSWE's verifier-failure sentinel."""
    text = logs / "reward.txt"
    reward = (
        {"reward": float(text.read_text())}
        if text.exists()
        else json.loads((logs / "reward.json").read_text())
    )
    if not isinstance(reward, dict) or reward.get("reward") not in (0, 1):
        raise ValueError(f"Invalid DeepSWE reward: {reward!r}")
    return reward


def run_tests(script, logs, timeout):
    with (logs / "stdout.txt").open("wb") as out, (logs / "stderr.txt").open("wb") as err:
        process = subprocess.run(
            ["bash", str(script)], stdout=out, stderr=err, timeout=timeout, check=False
        )
    (logs / "execution.json").write_text(json.dumps({"exit_code": process.returncode}))
    # Pier reads the reward even when the test command exits nonzero.
    return read_reward(logs)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    args = parser.parse_args()
    runtime = Runtime()
    instance = runtime.connect(args.instance)
    package = Path(__file__).parent
    config = json.loads((package / "config.json").read_text())
    artifacts = Path("/logs/artifacts")
    logs = Path("/logs/verifier")
    artifacts.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    shutil.copytree(package / "tests", "/tests", dirs_exist_ok=True)
    collected = []
    for hook in config["collect"]:
        try:
            code, out, err = runtime.exec(
                instance=instance, command=hook["command"], timeout=hook["timeout_sec"]
            )
            collected.append({"exit_code": code, "stdout": out, "stderr": err})
        except (EnvWeaverError, OSError, TimeoutError) as error:
            collected.append({"error": str(error)})
    # Pier continues grading an empty submission when collection fails.
    patch = artifacts / "model.patch"
    try:
        runtime.download(instance=instance, source=str(patch), target=patch)
    except (EnvWeaverError, OSError, TimeoutError) as error:
        collected.append({"missing_patch": str(error)})
    (logs / "collection.json").write_text(json.dumps(collected, indent=2))
    try:
        reward = run_tests(Path("/tests/test.sh"), logs, config["timeout_sec"])
    except Exception:
        # EnvWeaver retains stderr even when the verifier cannot return a score.
        import sys

        for name in (
            "stdout.txt",
            "stderr.txt",
            "collection.json",
            "reward.txt",
            "reward.json",
            "execution.json",
        ):
            if not (logs / name).exists():
                continue
            print(
                f"\n--- {name} ---\n" + (logs / name).read_text(errors="replace")[-20000:],
                file=sys.stderr,
            )
        raise
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz") as tar:
        tar.add(logs, arcname="verifier")
        if patch.exists():
            tar.add(patch, arcname="model.patch")
    patch_data = patch.read_bytes() if patch.exists() else b""
    print(
        json.dumps(
            {
                "score": reward["reward"],
                "official_reward": reward,
                "patch_bytes": len(patch_data),
                "patch_sha256": hashlib.sha256(patch_data).hexdigest(),
                "artifacts_tar_gz_b64": base64.b64encode(archive.getvalue()).decode(),
            }
        )
    )


if __name__ == "__main__":
    main()
