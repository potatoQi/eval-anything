import json
from pathlib import Path

from .data import BASELINES, Dataset, safe_path

# The pinned OpenAI release contains a truncated PrivateCrypMixV2.zip.
EXCLUDED_SUBMISSIONS = {"human-expert": {"0e386e32-df20-4d1f-b536-7159bc409ad5"}}


def export_run(root, expected, run_round):
    def submission(batch_dir, result):
        directory = safe_path(batch_dir, result["output_dir"]) / "agent"
        agent = result.get("agent_result") or {}
        files = {}
        for original, relative in agent.get("metadata", {}).get("submitted_files", {}).items():
            path = safe_path(directory, relative)
            if not path.is_file():
                raise ValueError(f"Missing saved submission: {path}")
            files[original] = str(path.relative_to(root))
        status = "submitted" if files else "empty"
        return {
            "status": status if result["score_valid"] else "error",
            "stop": result["agent_stop_reason"],
            "files": files,
            "text": "\n".join(b["text"] for b in agent.get("output", []) if b["type"] == "text"),
        }

    entries = {}
    for name, entry in run_round(1, result_value=submission):
        if name in entries:
            raise ValueError(f"Duplicate generated task: {name}")
        entries[name] = entry
    if entries.keys() != expected:
        raise ValueError("Incomplete generation results")
    path = root / "submissions.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2))
    tmp.replace(path)
    return entries


def load_source(value, cache, proxy):
    if value in BASELINES:
        ds = Dataset(cache, BASELINES[value], proxy)
        excluded = EXCLUDED_SUBMISSIONS.get(value, ())
        return {
            i: {
                "status": "submitted",
                "text": row.get("deliverable_text") or "",
                "files": {p: safe_path(ds.root, p) for p in row["deliverable_files"]},
            }
            for i, row in ds.rows.items()
            if row["deliverable_files"] and i not in excluded
        }, ds
    path = Path(value).resolve()
    if path.is_dir():
        path /= "submissions.json"
    rows = json.loads(path.read_text())
    result = {}
    for task, row in rows.items():
        status = row["status"]
        if status not in ("submitted", "empty", "error") or not isinstance(row["text"], str):
            raise ValueError(f"Invalid submission: {task}")
        files = {name: safe_path(path.parent, p) for name, p in row["files"].items()}
        if status == "submitted" and not files:
            raise ValueError(f"Missing submitted files: {task}")
        result[task] = {**row, "files": files}
    return result, None
