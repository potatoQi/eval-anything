import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from envweaver import Runtime
from envweaver.errors import EnvWeaverError

args = argparse.ArgumentParser()
args.add_argument("--instance", required=True)
runtime = Runtime()
instance = runtime.connect(path=args.parse_args().instance)
settings = json.loads(Path("settings.json").read_text())
runtime.upload(instance=instance, source=Path("tests"), target="/tests")
# Set download locations only inside the grading subprocess. Custom package indexes,
# credentials and uv configuration always take precedence.
grade = r"""
mirror=$1
if [ -n "$mirror" ]; then
    if [ -z "${UV_DOWNLOAD_URL:-}${INSTALLER_DOWNLOAD_URL:-}${UV_INSTALLER_GHE_BASE_URL:-}${UV_INSTALLER_GITHUB_BASE_URL:-}${UV_GITHUB_TOKEN:-}" ]; then
        export UV_DOWNLOAD_URL="${mirror}uv"
    fi
    if [ -z "${UV_PYTHON_INSTALL_MIRROR:-}${UV_PYTHON_DOWNLOADS_JSON_URL:-}" ]; then
        export UV_PYTHON_INSTALL_MIRROR="${mirror}python"
    fi
    if [ -z "${UV_CONFIG_FILE:-}${UV_INDEX:-}${UV_EXTRA_INDEX_URL:-}${UV_DEFAULT_INDEX:-}${UV_INDEX_URL:-}${UV_OFFLINE:-}${UV_NO_INDEX:-}${UV_NO_CACHE:-}${UV_KEYRING_PROVIDER:-}${UV_SYSTEM_CONFIG_DIR:-}${NETRC:-}${XDG_CONFIG_DIRS:-}" ] &&
        [ ! -e /etc/uv/uv.toml ] && [ ! -e /etc/xdg/uv/uv.toml ] && [ ! -e "${XDG_CONFIG_HOME:-$HOME/.config}/uv/uv.toml" ] && [ ! -e "$HOME/.netrc" ]; then
        dir=$PWD
        while [ ! -e "$dir/uv.toml" ] && [ ! -e "$dir/pyproject.toml" ]; do
            if [ "$dir" = / ]; then export UV_DEFAULT_INDEX="${mirror}simple/"; break; fi
            dir=${dir%/*}
            dir=${dir:-/}
        done
    fi
    export no_proxy="${no_proxy:-${NO_PROXY:-}},$2"
    export NO_PROXY="$no_proxy"
fi
mkdir -p /logs/verifier
rm -f /logs/verifier/reward.txt /logs/verifier/ctrf.json
export TEST_DIR=/tests
if [ -n "$3" ]; then
    UV_DEFAULT_INDEX="$4" UV_OFFLINE=1 bash -c "$3" /tests/test.sh
else
    bash /tests/test.sh
fi > /logs/verifier/official.log 2>&1
"""
mirror = os.environ.get("TB_RELEASE_MIRROR", "")
try:
    code, out, err = runtime.exec(
        instance=instance,
        command=[
            "sh",
            "-c",
            grade,
            "grade",
            mirror,
            urlsplit(mirror).hostname or "",
            settings.get("prepared_grader", ""),
            settings.get("prepared_index", ""),
        ],
        cwd=settings["cwd"],
        timeout=settings["timeout"],
    )
except Exception:
    try:
        _, log, _ = runtime.exec(
            instance=instance,
            command=["tail", "-c", "16000", "/logs/verifier/official.log"],
            timeout=15,
        )
        print(log, file=sys.stderr)
    except (EnvWeaverError, OSError, TimeoutError) as diagnostic_error:
        print(f"Verifier log unavailable: {diagnostic_error}", file=sys.stderr)
    raise

read_script = """import json
from pathlib import Path
root=Path('/logs/verifier')
reward=root/'reward.txt'
if not reward.is_file():
    raise RuntimeError('Official test did not produce reward.txt: '+(root/'official.log').read_text(errors='replace')[-8000:])
report=root/'ctrf.json'
ctrf=json.loads(report.read_text()) if report.exists() else None
if ctrf is None or ctrf.get('results',{}).get('summary',{}).get('tests',0)==0:
    raise RuntimeError('Official pytest did not report collected tests: '+(root/'official.log').read_text(errors='replace')[-8000:])
print(json.dumps({'score':float(reward.read_text().strip()),'ctrf':ctrf,'official_log_tail':(root/'official.log').read_text(errors='replace')[-16000:]},allow_nan=False))
"""
status, stdout, stderr = runtime.exec(
    instance=instance, command=["python3", "-c", read_script], timeout=30
)
if status:
    raise RuntimeError(stderr)
result = json.loads(stdout)
result["official_exit_code"] = code
if err:
    result["official_stderr"] = err[-4000:]
print(json.dumps(result, allow_nan=False))
