"""EnvWeaver verifier entry point in the trusted ALE controller."""

import json
import subprocess
import sys
from pathlib import Path

key = json.loads(Path("spec.json").read_text())["key"]
subprocess.run(
    [sys.executable, "/opt/ea-driver.py", "verify", "/opt/ale", key, "http://127.0.0.1:5000"],
    check=True,
)
