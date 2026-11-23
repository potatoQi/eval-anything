"""Build the General supplement from the two pinned upstream archives."""

import argparse
import tarfile
import tempfile
from pathlib import Path

from eval_anything.benchmarks.claw import ClawGeneral
from eval_anything.sources import checksum


def checked(path, expected):
    if checksum(path) != expected:
        raise ValueError(f"Archive checksum mismatch: {path}")


def pack(source, fixtures, output):
    checked(source, ClawGeneral.checksum)
    checked(fixtures, "01c349b8eb3ae8bcd741578d6a7f94ba3f76e0aad74bd98233e02e629e65ff5b")
    with tarfile.open(source, "r|gz") as archive:
        existing = {
            Path(*Path(m.name).parts[2:]) for m in archive if Path(m.name).parts[1:2] == ("tasks",)
        }
    if output.exists():
        raise FileExistsError(output)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        tree = Path(temp) / "fixtures"
        tree.mkdir()
        with tarfile.open(fixtures, "r|gz") as archive:
            for m in archive:
                path = Path(m.name)
                if path.parts[0] not in ClawGeneral.names or path in existing or m.isdir():
                    continue
                if not m.isfile() or ".." in path.parts:
                    raise ValueError(f"Unexpected fixture member: {m.name}")
                archive.extract(m, tree, filter="data")
        tree.rename(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    pack(args.source, args.fixtures, args.output)
