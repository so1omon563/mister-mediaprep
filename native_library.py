"""Shared native update, preflight and verification helpers."""
import json
from pathlib import Path

from contextlib import redirect_stdout
import hashlib
import time

from mister_mediaprep import main as cli

APP = Path(__file__).resolve().parent
if APP.suffix == ".pyz":
    APP = APP.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(args, output, *, allow_issues=False):
    started = time.monotonic()
    with output.open("w", encoding="utf-8") as stream, redirect_stdout(stream):
        code = cli(args)
    if code and not (allow_issues and code == 1):
        raise RuntimeError("CLI exited %s; inspect %s" % (code, output))
    return json.loads(output.read_text()), time.monotonic() - started


def require_closed():
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            running = path.read_text().strip()
        except FileNotFoundError:
            continue
        if running == "ConsoleMode_arm":
            raise RuntimeError("Close Console Mode before running the library update")


def update(jobs, results):
    # Finish every system's read-only preflight before publishing any artwork.
    plans = []
    for name, args in jobs:
        print("Previewing %s" % name, flush=True)
        plan, seconds = run(["scan"] + args, results / (name + "-preview.json"))
        plans.append((name, args, plan))
        print("%s: %s (%.2fs)" % (name, plan["summary"], seconds), flush=True)
    summary = []
    for name, args, plan in plans:
        require_closed()
        protected = {path: (Path(path).stat().st_size, Path(path).stat().st_mtime_ns)
                     for path in plan["protected_inputs"]}
        wanted = {row["destination"]: sha(Path(row["source"])) for row in plan["rows"]
                  if row["operation"] in {"copy", "replace"}}
        journal = results / (name + ".jsonl")
        if journal.exists():
            args = list(args)
            while "--rom" in args:
                index = args.index("--rom")
                del args[index:index + 2]
        print("Updating %s" % name, flush=True)
        outcome, seconds = run(["apply"] + args + ["--resume" if journal.exists() else "--journal", str(journal)],
                               results / (name + "-apply.json"))
        recovered = [row for row in outcome.get("rows", []) if row.get("resume_cleanup")]
        wanted.update({row["destination"]: sha(Path(row["source"])) for row in recovered})
        for destination, expected in wanted.items():
            if sha(Path(destination)) != expected:
                raise RuntimeError("Output verification failed: %s" % destination)
        for path, expected in protected.items():
            current = Path(path).stat()
            if (current.st_size, current.st_mtime_ns) != expected:
                raise RuntimeError("Protected input changed: %s" % path)
        summary.append(dict(system=name, outputs_verified=len(wanted), apply_seconds=seconds,
                            copied=outcome.get("copied_files", 0), unchanged=outcome.get("unchanged_files", 0),
                            preserved=plan["summary"].get("preserve", 0) - len(recovered),
                            missing=plan["summary"].get("missing", 0),
                            resumed_complete=outcome.get("resume_complete", False)))
        (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        print("%s: %d outputs verified in %.2fs" % (name, len(wanted), seconds), flush=True)
