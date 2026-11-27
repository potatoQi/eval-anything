import fcntl
import hashlib
import json
import os
import random
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import BadZipFile

import urllib3

from .data import GOLD, Dataset
from .judge import PANEL, judge, rendered
from .submissions import EXCLUDED_SUBMISSIONS, load_source


def dump(path, value):
    part = path.with_suffix(".tmp")
    part.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    part.replace(path)


def score(args):
    from ...runner import expand

    config = expand(args.judges, os.environ)
    if set(config) != set(PANEL):
        raise ValueError("Provide gpt, claude, and gemini endpoints in --judges")
    for slot in PANEL:
        if set(config[slot]) != {"base_url", "api_key"} or not all(config[slot].values()):
            raise ValueError(f"Provide base_url and api_key for {slot}")
        url = urlsplit(config[slot]["base_url"])
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError(f"Provide a service base URL without credentials or query for {slot}")
    if args.left == args.right:
        raise ValueError("Choose two distinct submission sources")
    if args.concurrency < 1:
        raise ValueError("--concurrency must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if not args.resume and any(p.name != ".lock" for p in root.iterdir()):
            raise ValueError("Score output exists; use --resume or a new --output")
        left, dl = load_source(args.left, args.cache, args.setup_proxy)
        right, dr = load_source(args.right, args.cache, args.setup_proxy)
        gold = Dataset(args.cache, GOLD, args.setup_proxy)
        if (left.keys() | right.keys()) - gold.rows.keys():
            raise ValueError("Unknown GDPval task ID in submissions")
        ids = left.keys() & right.keys()
        requested = set(args.task) if args.task else left.keys() | right.keys()
        excluded = set().union(
            *(EXCLUDED_SUBMISSIONS.get(side, ()) for side in (args.left, args.right))
        )
        skipped = requested & excluded
        if args.task:
            if requested - skipped - ids:
                raise ValueError("Requested task missing on one or both sides")
            ids &= requested
        ids = sorted(ids)
        if not ids and not skipped:
            raise ValueError("No comparable tasks")
        gold.preload("reference_files", ids)
        for source in (dl, dr):
            if source:
                source.preload("deliverable_files", ids)
        image = None
        if ids:
            image = subprocess.check_output(
                ["docker", "image", "inspect", "--format", "{{.Id}}", args.renderer_image],
                text=True,
            ).strip()

        # Include actual bytes, so replacing a submission at the same path cannot reuse a vote.
        def identify(entry):
            files = {}
            for name, path in entry["files"].items():
                with path.open("rb") as f:
                    files[name] = hashlib.file_digest(f, "sha256").hexdigest()
            return {"text": entry["text"], "status": entry["status"], "files": files}

        protocol = hashlib.sha256(
            b"".join(
                (Path(__file__).parent / name).read_bytes()
                for name in ("score.py", "judge.py", "office.py", "assets/render.py")
            )
        ).hexdigest()
        descriptor = {
            "protocol": protocol,
            "gold_revision": GOLD[1],
            "left": args.left,
            "right": args.right,
            "seed": args.seed,
            "judges": {k: [*PANEL[k], v["base_url"]] for k, v in config.items()},
            "image": image,
            "inputs": {i: [identify(left[i]), identify(right[i])] for i in ids},
            "skipped_tasks": sorted(skipped),
        }
        fingerprint = hashlib.sha256(json.dumps(descriptor, sort_keys=True).encode()).hexdigest()
        settings = root / "match.json"
        if args.resume:
            if json.loads(settings.read_text())["sha256"] != fingerprint:
                raise ValueError("Score inputs or settings changed; choose a new output")
        else:
            dump(settings, {"sha256": fingerprint, **descriptor})
        (root / "votes").mkdir(exist_ok=True)
        rng = random.Random(args.seed)
        order = ids.copy()
        rng.shuffle(order)
        slots = list(PANEL)
        rng.shuffle(slots)
        schedule = {task: (slots[n % 3], bool(rng.randrange(2))) for n, task in enumerate(order)}

        def compare(task):
            record = root / "votes" / (task + ".json")
            if args.resume and record.exists():
                existing = json.loads(record.read_text())
                if existing["outcome"] != "error":
                    return existing["outcome"]
            slot, swap = schedule[task]
            result = {"task_id": task, "a_source": "right" if swap else "left"}
            try:
                lhs, rhs = left[task], right[task]
                if "error" in (lhs["status"], rhs["status"]):
                    raise ValueError("Generation error; repair the run before comparing")
                if "empty" in (lhs["status"], rhs["status"]):
                    outcome = (
                        "tie"
                        if lhs["status"] == rhs["status"]
                        else ("right" if lhs["status"] == "empty" else "left")
                    )
                    result["reason"] = "No files submitted"
                else:
                    a, b = (rhs, lhs) if swap else (lhs, rhs)
                    row = gold.rows[task]
                    parts = [{"text": "Task:\n" + row["prompt"] + "\nReference files:"}]
                    for label, files in [
                        (None, {n: gold.reference(n) for n in row["reference_files"]}),
                        ("A", a),
                        ("B", b),
                    ]:
                        if label:
                            parts.append({"text": f"Submission {label}:\n" + files["text"]})
                            files = files["files"]
                        for name, path in files.items():
                            parts.extend(rendered(Path(name).name, path, args.cache, image))
                    if any(p.get("mime", "").startswith(("audio/", "video/")) for p in parts):
                        slot = "gemini"
                    result["judge"] = PANEL[slot]
                    decision = judge(config, slot, parts)
                    choice = decision["winner"]
                    outcome = (
                        "tie"
                        if choice == "tie"
                        else ("left" if (choice == "A") != swap else "right")
                    )
                    result.update(decision)
                result["outcome"] = outcome
            except (
                BadZipFile,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                IndexError,
                subprocess.SubprocessError,
                urllib3.exceptions.HTTPError,
            ) as exc:
                result.update(outcome="error", error=str(exc))
            dump(record, result)
            return result["outcome"]

        with ThreadPoolExecutor(args.concurrency) as pool:
            counts = Counter(pool.map(compare, ids))
        valid = sum(counts[k] for k in ("left", "right", "tie"))
        summary = {
            "left": args.left,
            "right": args.right,
            "compared": valid,
            "selected": len(ids),
            "skipped_tasks": sorted(skipped),
            "missing_left": len(gold.rows.keys() - left.keys()),
            "missing_right": len(gold.rows.keys() - right.keys()),
            "left_wins": counts["left"],
            "ties": counts["tie"],
            "right_wins": counts["right"],
            "errors": counts["error"],
            "left_preference": (counts["left"] + 0.5 * counts["tie"]) / valid if valid else None,
        }
        dump(root / "summary.json", summary)
        print(json.dumps(summary, ensure_ascii=False))
        if counts["error"]:
            raise ValueError("Some comparisons failed; see votes/ and retry with --resume")
