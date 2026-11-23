"""Fetch immutable upstream distributions. The caller locks the benchmark cache."""

import hashlib
import os
import subprocess
import tarfile
from pathlib import Path
from tempfile import TemporaryDirectory


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def unpack(url, digest, dest, proxy, *, include=None):
    part = dest.parent / f".{dest.name}.{digest}.part"
    if dest.is_dir():
        part.unlink(missing_ok=True)
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    # A previous process may have finished downloading but stopped before extraction.
    if not part.exists() or checksum(part) != digest:
        print(f"Downloading {url}", flush=True)
        env = os.environ.copy()
        if proxy:
            env.update(https_proxy=proxy, http_proxy=proxy)
        cmd = ["curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3"]
        offset = part.stat().st_size if part.exists() else 0
        resume = ["--continue-at", "-"] if offset else []
        transfer = subprocess.run(
            [*cmd, *resume, "-o", part, "--write-out", "%{http_code}", url],
            env=env,
            check=False,
            stdout=subprocess.PIPE,
        )
        range_rejected = transfer.returncode in (33, 36) or transfer.stdout == b"416"
        if transfer.returncode and not (offset and range_rejected):
            transfer.check_returncode()
        valid = transfer.returncode == 0 and checksum(part) == digest
        if not valid and offset:
            # The server may ignore Range or reject a partial from a stale response.
            subprocess.run([*cmd, "-o", part, url], env=env, check=True)
            valid = checksum(part) == digest
        if not valid:
            part.unlink(missing_ok=True)
            raise ValueError(f"Source checksum mismatch: {url}")
    with TemporaryDirectory(dir=dest.parent) as temp:
        tree = Path(temp)
        with tarfile.open(part, "r|gz") as tar:

            def members():
                for member in tar:
                    parts = Path(member.name).parts[1:]
                    if not parts or include is None or include(parts):
                        yield member

            tar.extractall(tree, members=members(), filter="data")
        (tree,) = tree.iterdir()
        tree.rename(dest)
    part.unlink()
