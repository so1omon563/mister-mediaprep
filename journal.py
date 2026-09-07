"""Append-only progress records. Resume selects work; never executes saved plans."""

from contextlib import contextmanager
import json
import os
from pathlib import Path

from deployment import directory, state
from mister_mediaprep import ROLES, checked_path, digest


def settings(args):
    from corrections import read as read_corrections
    values = {name: getattr(args, name) for name in
              ("gamelist", "rom_root", "output", "box_dir", "background_dir",
               "map_root", "replace", "experimental_layout")}
    if getattr(args, "arcade_layout", False):
        values["arcade_layout"] = True
    # Keep default settings compatible with journals from earlier versions.
    values.update({role + "_source": getattr(args, role + "_source", default)
                   for role, default in ROLES.items() if getattr(args, role + "_source", default) != default})
    corrections = read_corrections(args.rom_root)[1]
    if corrections is not None:
        values["corrections"] = corrections
    return values


def journal_path(args, value):
    path, _ = checked_path(value, missing=True)
    roots = [args.rom_root, args.output, str(Path(args.gamelist).parent), args.consolemode_root,
             args.box_dir, args.background_dir, *[new for _, new in args.map_root]]
    for value in roots:
        if value:
            root, _ = checked_path(value, missing=True)
            if path.is_relative_to(root):
                raise ValueError("Keep the journal outside artwork, ROM, gamelist, and frontend directories")
    return path


def resume(args):
    try:
        return _resume(args)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("Malformed journal record") from exc


def _resume(args):
    if args.rom:
        raise ValueError("--resume selects unfinished games; do not combine it with --rom")
    path = journal_path(args, args.resume)
    before = state(path)
    with path.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Journal exceeds 16 MiB")
    lines = data.splitlines(keepends=True)
    valid_bytes = sum(len(line) for line in lines if line.endswith(b"\n"))
    events = [json.loads(line) for line in lines if line.endswith(b"\n")]
    if not events or events[0].get("kind") != "plan" or events[0].get("schema") != 1:
        raise ValueError("Invalid or incomplete journal header")
    header = events[0]
    if header["settings"] != settings(args):
        raise ValueError("Resume requires the original paths, policy, overrides, game corrections, and layout option; start a new update if corrections changed")
    root, _ = checked_path(args.rom_root)
    output, _ = checked_path(args.output)
    source_roots = [root, Path(args.gamelist).absolute().parent]
    source_roots += [Path(value).absolute() for value in (args.box_dir, args.background_dir) if value]
    source_roots += [Path(new).absolute() for _, new in args.map_root]
    gamelist_changed = state(args.gamelist) != header["gamelist_state"]
    allowed = set()
    for item in header["rows"]:
        rom = Path(item["rom"])
        if ".." in rom.parts or not rom.is_relative_to(root) or item["role"] not in {"box", "background"}:
            raise ValueError("Invalid journal selection")
        allowed.add((str(rom), item["role"]))
    latest = {}
    for event in events[1:]:
        if event.get("kind") != "result" or (event.get("rom"), event.get("role")) not in allowed:
            raise ValueError("Unexpected journal record")
        latest[event["rom"], event["role"]] = event
    pending = set()
    args.journal_pending_outputs = {}
    for identity in allowed:
        event = latest.get(identity, {})
        complete = not gamelist_changed and event.get("result") in {"applied", "unchanged", "skipped", "preserved"}
        if complete and event.get("result") in {"applied", "unchanged"}:
            dest = Path(event["destination"])
            source = Path(event["source"])
            stem = Path(identity[0]).stem + ("-BG" if identity[1] == "background" else "")
            if (dest.parent != output or dest.stem != stem or dest.suffix not in {".png", ".jpg", ".jpeg"}
                    or ".." in source.parts or not any(source.is_relative_to(base) for base in source_roots)):
                raise ValueError("Journal artwork paths do not match the selected game or input roots")
            for field in ("source", "destination"):
                try:
                    current, _ = checked_path(event[field])
                    complete &= state(current) == event[field + "_state"]
                except (OSError, ValueError, KeyError):
                    complete = False
        if not complete:
            pending.add(identity[0])
            if event.get("result") in {"error", "raw_applied_cleanup_failed", "raw_applied_cache_failed"}:
                args.journal_pending_outputs[identity] = event
    args.rom = sorted(pending)
    args.journal_resume_state = (before, valid_bytes)
    return bool(pending)


def recover_fill_missing(args, report):
    """Finish cleanup for an interrupted copy only when the current bytes match."""
    if args.replace:
        return
    for row in report["rows"]:
        previous = args.journal_pending_outputs.get((row["rom"], row["role"]))
        if previous is None or row["operation"] != "preserve":
            continue
        if (row["source"] != previous.get("source") or row["destination"] != previous.get("destination")
                or row["existing_candidates"] != [row["destination"]]
                or digest(row["source"]) != digest(row["destination"])):
            raise ValueError("An unfinished fill-missing output now differs from its source or original paths; "
                             "review it before a selected --replace repair: %s" % row["rom"])
        row["operation"] = "replace"
        row["resume_cleanup"] = True
        row["reason"] = "Resume cleanup for a verified byte-identical output from the interrupted copy"
        report["summary"]["preserve"] -= 1
        report["summary"]["replace"] += 1


@contextmanager
def recording(args, report):
    value = args.resume or args.journal
    if not value:
        yield None
        return
    path = journal_path(args, value)
    with directory(path.parent) as parent:
        flags = os.O_RDWR | os.O_NOFOLLOW
        flags |= 0 if args.resume else os.O_CREAT | os.O_EXCL
        fd = os.open(path.name, flags, 0o600, dir_fd=parent)
        with os.fdopen(fd, "r+", encoding="utf-8") as stream:
            if args.resume:
                expected, valid_bytes = args.journal_resume_state
                if state(path.name, parent) != expected:
                    raise ValueError("Journal changed since resume inspection")
                stream.truncate(valid_bytes)
                stream.seek(0, os.SEEK_END)
            else:
                header = dict(kind="plan", schema=1, settings=settings(args), gamelist_state=state(args.gamelist),
                              rows=[{k: row[k] for k in ("rom", "role")} for row in report["rows"]])
                stream.write(json.dumps(header) + "\n")
                stream.flush()
            def record(row):
                event = {k: row.get(k) for k in ("rom", "role", "result", "source", "destination",
                                                "source_state", "destination_state", "reason")}
                event["kind"] = "result"
                stream.write(json.dumps(event) + "\n")
                # A process interruption loses at most the in-flight row. A power
                # loss may lose buffered records; their outputs are safely rechecked.
                stream.flush()
            yield record
