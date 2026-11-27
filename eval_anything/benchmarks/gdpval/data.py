"""Immutable HF datasets, shared by generation and offline reference sets."""

import fcntl
import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from functools import cached_property
from pathlib import Path
from urllib.parse import quote

from ...sources import checksum
from .office import office_copy

GOLD = ("openai/gdpval", "11e7900cdcac61bc4daf59e65feb238acda98fbf")
GPT5 = ("jeqcho/2025-10-16T21-42-02plus00-00_gdpval", "87f1744868c7e6d5110f1b6899a9a15343a46e03")
BASELINES = {"human-expert": GOLD, "gpt-5-low": GPT5}
TABLE = "data/train-00000-of-00001.parquet"


def safe_path(root, name):
    path = Path(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"Invalid relative filename: {name}")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Path outside submission: {name}")
    return resolved


def fetch(url, path, proxy, info=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Publish only complete downloads. Concurrent cache users share the same file.
    with path.with_name(path.name + ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists() and (not info or path.stat().st_size == info["size"]):
            return path
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as tmp:
            part = Path(tmp.name)
        try:
            subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--http1.1",
                    "--speed-limit",
                    "32",
                    "--speed-time",
                    "60",
                    "--retry-all-errors",
                    "--retry",
                    "4",
                    "--connect-timeout",
                    "25",
                    *(["--proxy", proxy] if proxy else []),
                    "-o",
                    str(part),
                    url,
                ],
                check=True,
            )
            if info:
                size = part.stat().st_size
                if "lfs" in info:
                    digest = checksum(part)
                    expected = info["lfs"]["sha256"]
                else:
                    h = hashlib.sha1(f"blob {size}\0".encode())
                    with part.open("rb") as f:
                        for chunk in iter(lambda: f.read(1 << 20), b""):
                            h.update(chunk)
                    digest, expected = h.hexdigest(), info["blobId"]
                if size != info["size"] or digest != expected:
                    raise ValueError(f"Corrupt HF artifact: {path.name}")
            os.replace(part, path)
        finally:
            part.unlink(missing_ok=True)
    return path


class Dataset:
    def __init__(self, cache, source, proxy=None):
        self.repo, self.revision = source
        self.root = cache / "gdpval-assets" / self.revision
        self.proxy = proxy
        index = fetch(
            f"https://huggingface.co/api/datasets/{self.repo}/revision/{self.revision}?blobs=true",
            self.root / "index.json",
            proxy,
        )
        meta = json.loads(index.read_text())
        if meta["sha"] != self.revision:
            raise ValueError("Unexpected HF revision")
        self.index = {v["rfilename"]: v for v in meta["siblings"]}

    def file(self, name):
        return fetch(
            f"https://huggingface.co/datasets/{self.repo}/resolve/{self.revision}/{quote(name)}",
            safe_path(self.root, name),
            self.proxy,
            self.index[name],
        )

    @cached_property
    def rows(self):
        try:
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ValueError(
                "Install eval-anything[gdpval] to read the pinned task tables"
            ) from exc
        rows = pq.read_table(self.file(TABLE)).to_pylist()
        catalog = {r["task_id"]: r for r in rows}
        if len(rows) != 220 or len(catalog) != len(rows):
            raise ValueError("Unexpected GDPval catalog")
        return catalog

    def reference(self, name):
        return office_copy(self.file(name), safe_path(self.root / "office-v1", name))

    def preload(self, column, ids, *, resolve=None):
        names = sorted({name for task in ids for name in self.rows[task][column]})
        with ThreadPoolExecutor(max_workers=8) as pool:
            return dict(zip(names, pool.map(resolve or self.file, names), strict=True))
