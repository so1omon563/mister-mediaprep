"""Small Scripts-menu interface to the existing artwork CLI."""
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
import sys
import subprocess
import tempfile
import xml.etree.ElementTree as ET

from native_library import APP, require_closed, run, update
from deployment import configured_systems, layout_problem
from corrections import read as read_corrections, save as save_correction
from mister_mediaprep import (VERSION, ROLES, ARTWORK_SOURCES, EXTENSIONS, artwork_candidates, artwork_preferences,
                             artwork_name, checked_path, image_check, key, read_gamelist, resolve)


def discover(bases, consolemode, progress=None):
    systems = configured_systems(consolemode)
    libraries = {}
    for base in bases:
        if not base.is_dir():
            continue
        for root in sorted(base.iterdir()):
            if root.name.startswith(".") or not root.is_dir() or not (root / "gamelist.xml").is_file():
                continue
            item = dict(root=str(root.absolute()), roms=[], correctable_roms=[], excluded=0, issues=[])
            try:
                cache = {}
                root, _ = checked_path(root, entries_cache=cache)
                if root in libraries:
                    continue
                if progress:
                    progress("Finding artwork libraries: " + root.name)
                item["root"] = str(root)
                xml, _ = checked_path(root / "gamelist.xml", entries_cache=cache)
                document = read_gamelist(xml)
                corrections, _ = read_corrections(root)
                item["correction_roms"] = sorted(corrections)
                if document.findall("folder"):
                    raise ValueError("Folder artwork mappings are not supported")
                media, _ = checked_path(root / "media", entries_cache=cache)
                if not media.is_dir():
                    raise ValueError("No media directory")
                extensions = systems.get(key(root.name), set())
                if not extensions:
                    raise ValueError("Library is not configured in Console Mode")
                games = document.findall("game")
                parents = defaultdict(list)
                for game in games:
                    if game.get("id"):
                        parents[game.get("id")].append(game)
                if any(len(entries) > 1 for entries in parents.values()):
                    raise ValueError("Duplicate canonical IDs in gamelist")
                roms, images = set(), {}
                has_artwork = False
                for game in games:
                    value = game.findtext("path", "").strip()
                    if not value and game.get("id"):
                        continue
                    try:
                        if not value:
                            raise ValueError("Game has no ROM path")
                        rom, _ = resolve(value, root, [root], [], cache)
                        if not artwork_name(rom.name):
                            continue
                        problem = layout_problem(rom, extensions)
                        if problem:
                            raise ValueError(problem)
                        item["correctable_roms"].append(rom.relative_to(root).as_posix())
                        saved = corrections.get(rom.relative_to(root).as_posix(), {})
                        parent = None
                        if game.get("parentid"):
                            matches = parents[game.get("parentid")]
                            if len(matches) != 1 and not saved:
                                raise ValueError("Missing canonical parent in gamelist")
                            if len(matches) == 1:
                                parent = matches[0]
                        mapped = False
                        for role, preferred in ROLES.items():
                            if role in saved:
                                mapped = has_artwork = True
                                continue
                            for field, art, inherited, artwork_type in artwork_candidates(game, parent, role, preferred):
                                mapped = True
                                # Discovery needs one usable source per library;
                                # Preview/Apply validate every selected image fully.
                                if not has_artwork and art not in images:
                                    try:
                                        source, _ = resolve(art, root, [root], [], cache)
                                        image_check(source)
                                        images[art] = True
                                    except (OSError, ValueError):
                                        images[art] = False
                                has_artwork |= images.get(art, False)
                        if not mapped:
                            raise ValueError("No artwork mapping")
                        roms.add(rom.relative_to(root).as_posix())
                    except (OSError, ValueError) as error:
                        item["issues"].append(dict(path=value, reason=str(error)))
                if roms and not has_artwork:
                    raise ValueError("No usable mapped PNG/JPEG artwork in library")
                item["roms"] = sorted(roms)
            except (OSError, ValueError, ET.ParseError) as error:
                item["issues"].append(dict(path=str(root), reason=str(error)))
            item["excluded"] = len(item["issues"])
            libraries[root] = item
    return list(libraries.values())


def jobs_for(selection, available, consolemode):
    known = {item["root"]: set(item["roms"]) for item in available}
    arcades = {item["root"] for item in available if item.get("arcade") is True}
    jobs = []
    names = set()
    for item in selection:
        root = Path(item["root"])
        if str(root) not in known or root.name in names:
            raise ValueError("Library unavailable or selected twice: %s; restore its original mount to resume" % root)
        arcade = str(root) in arcades
        if item.get("arcade", False) is not arcade:
            raise ValueError("Saved library layout changed")
        if "corrections" in item and read_corrections(root)[1] != item["corrections"]:
            raise ValueError("Game corrections changed; start a new update instead of resuming: " + str(root))
        names.add(root.name)
        roms = item["roms"]
        if not isinstance(roms, list) or not roms or any(
                not isinstance(rom, str) or Path(rom).is_absolute() or ".." in Path(rom).parts
                or str(Path(rom)) not in known[str(root)] for rom in roms):
            raise ValueError("Invalid saved ROM selection")
        policy = item.get("policy", "replace")  # Earlier menu selections always replaced artwork.
        if policy not in ("fill-missing", "replace"):
            raise ValueError("Invalid saved artwork policy")
        args = ["--gamelist", str(root / "gamelist.xml"), "--rom-root", str(root),
                "--output", str((root.parent if arcade else root) / "media"), "--format", "json",
                "--consolemode-root", str(consolemode)]
        if arcade:
            args.append("--arcade-layout")
        if policy == "replace":
            args.append("--replace")
        for role, field in artwork_preferences(item.get("artwork")).items():
            if field != ROLES[role]:
                args.extend(["--" + role + "-source", field])
        for rom in roms:
            args.extend(["--rom", rom])
        jobs.append((root.name, args))
    if not jobs:
        raise ValueError("Select at least one system")
    return jobs


def dialog(kind, text, options=()):
    args = ["dialog", "--stdout", "--title", "MiSTer MediaPrep " + VERSION, "--" + kind, text, "0", "0"]
    if kind == "menu":
        args.append("0")
        for tag, label in options:
            args.extend([tag, label])
    result = subprocess.run(args, stdout=subprocess.PIPE, text=True)
    if result.returncode in {1, 255}:
        return None
    if result.returncode:
        raise RuntimeError("dialog exited %s" % result.returncode)
    return result.stdout.strip()


def choose(libraries):
    selected = set()
    while True:
        options = [("done", "Continue with selected systems"), ("all", "Use all listed systems")]
        if any(item.get("issues") for item in libraries):
            options.append(("skipped", "View skipped libraries / entries"))
        options += [(str(number), "%s %s (%d games)" %
                     ("[x]" if number in selected else "[ ]", item["root"], len(item["roms"])))
                    for number, item in enumerate(libraries) if item["roms"]]
        value = dialog("menu", "Select toggles a system. Back cancels.", options)
        if value is None:
            return []
        if value == "all":
            return [item for item in libraries if item["roms"]]
        if value == "skipped":
            skipped = [item for item in libraries if item.get("issues")]
            index = dialog("menu", "Choose a library to see why entries were skipped.",
                           [(str(i), "%s (%d skipped)" % (Path(item["root"]).name, item["excluded"]))
                            for i, item in enumerate(skipped)])
            if index is not None:
                counts = Counter(issue["reason"] for issue in skipped[int(index)]["issues"])
                dialog("msgbox", "\n".join("%d: %s" % (count, reason) for reason, count in counts.items()))
            continue
        if value == "done":
            if selected:
                return [libraries[index] for index in sorted(selected)]
            continue
        index = int(value)
        if not 0 <= index < len(libraries) or not libraries[index]["roms"]:
            raise ValueError("Choose a listed system")
        selected.symmetric_difference_update({index})


def choose_artwork(preferences):
    preferences = artwork_preferences(preferences)
    while True:
        options = [("done", "Save artwork choices")]
        options += [(role, "%s: %s" % (role.capitalize(), ARTWORK_SOURCES[role][field]))
                    for role, field in preferences.items()]
        value = dialog("menu", "Choose a preferred image type. If missing, use the other type.\n"
                       "Saved game corrections take priority over these choices.\n"
                       "Game-specific mappings take priority over shared parent artwork.\n"
                       "Choices apply to your next Preview / Apply. Back cancels.", options)
        if value is None:
            return None
        if value == "done":
            return preferences
        if value in ROLES:
            preferences[value] = next(field for field in ARTWORK_SOURCES[value] if field != preferences[value])


def choose_source(root):
    base, _ = checked_path(Path(root) / "media")
    folder = base
    while True:
        folder, _ = checked_path(folder)
        paths = sorted((p for p in folder.iterdir() if not p.name.startswith(".") and not p.is_symlink()
                        and not (folder == base and key(p.name) == "optimized")
                        and (p.is_dir() or p.is_file() and p.suffix.lower() in EXTENSIONS)),
                       key=lambda p: (not p.is_dir(), p.name))
        options = ([("up", "Parent folder")] if folder != base else []) + [
            (str(i), ("[folder] " if p.is_dir() else "") + p.name) for i, p in enumerate(paths)]
        if not options:
            dialog("msgbox", "No source images or folders in " + str(base))
            return None
        selected = dialog("menu", "Choose an existing source image. Back cancels.\n" + str(folder), options)
        if selected is None:
            return None
        if selected == "up" and folder != base:
            folder = folder.parent
            continue
        index = int(selected)
        if not 0 <= index < len(paths):
            raise ValueError("Choose a listed image or folder")
        path, _ = checked_path(paths[index])
        if path.is_dir():
            folder = path
        else:
            image_check(path)
            return path


def choose_correction(libraries):
    libraries = [item for item in libraries if item.get("correctable_roms", item["roms"]) or item.get("correction_roms")]
    if not libraries:
        dialog("msgbox", "No games available for corrections.")
        return
    value = dialog("menu", "Choose a system for a game correction. Back cancels.",
                   [(str(i), item["root"]) for i, item in enumerate(libraries)])
    if value is None:
        return
    item = libraries[int(value)]
    saved, _ = read_corrections(item["root"])
    correctable = item.get("correctable_roms", item["roms"])
    games = sorted(set(correctable) | set(saved))
    value = dialog("menu", "Choose a game. [kept] means it has saved artwork.",
                   [(str(i), ("[kept] " if game in saved else "") + game) for i, game in enumerate(games)])
    if value is None:
        return
    game = games[int(value)]
    options = ([("both", "Keep current main image and background"), ("box", "Keep current main image"),
                ("background", "Keep current background"), ("choose-box", "Choose source for main image"),
                ("choose-background", "Choose source for background")] if game in correctable else [])
    if game in saved:
        options.append(("remove", "Use scraped artwork again"))
    action = dialog("menu", game + "\n\nKeep current artwork or choose an existing source image.\n"
                    "Saved images take priority during future updates. Back cancels.", options)
    if action is None:
        return
    sources = None
    if action in {"choose-box", "choose-background"}:
        source = choose_source(item["root"])
        if source is None:
            return
        action = action.removeprefix("choose-")
        sources = {action: source}
    require_closed()
    kept = save_correction(item["root"], game, list(ROLES) if action == "both" else [] if action == "remove" else [action],
                           sources=sources)
    dialog("msgbox", ("Saved correction for " + game + ".\nIt will be used in future previews and updates." if kept else
                      "Correction removed. Use Replace existing artwork to load scraped images again.") +
           "\n\nCurrent artwork and caches are unchanged.")


def arcade_menu(bases, runs, preferences, consolemode=Path("/media/fat/ConsoleMode")):
    roots = sorted({base.parent / "_Arcade" for base in bases if (base.parent / "_Arcade").is_dir()})
    if not roots:
        dialog("msgbox", "No _Arcade folders found on the SD card or USB drives.")
        return
    value = dialog("menu", "Choose an Arcade folder. Review the preview before choosing Apply.",
                   [(str(i), str(root)) for i, root in enumerate(roots)])
    if value is None:
        return
    root = roots[int(value)]
    policy = dialog("menu", "Which Arcade update would you like to preview?",
                    [("fill-missing", "Fill missing artwork; keep existing images"),
                     ("replace", "Replace existing artwork with your choices")])
    if policy is None:
        return
    args = ["arcade-preview", "--arcade-root", str(root), "--format", "json"]
    if policy == "replace":
        args.append("--replace")
    for role, field in artwork_preferences(preferences).items():
        args.extend(["--" + role + "-source", field])
    result_dir = Path(tempfile.mkdtemp(prefix="arcade-preview-", dir=runs))
    report, seconds = run(args, result_dir / "arcade.json", allow_issues=True)
    print("Arcade preview: %s (%.1fs)" % (result_dir, seconds), flush=True)
    inventory, counts = report["arcade"], report["summary"]
    summary = ("Arcade preview; no artwork changed. Back cancels.\n"
               "%d MRAs, %d absent from gamelist, %d stale gamelist paths.\n%d new, %d replace, %d kept, %d missing.\n"
               "Proposed output: %s" % (len(inventory["mras"]), len(inventory["unmapped_mras"]), len(inventory["stale_mappings"]),
                counts["copy"], counts["replace"], counts["preserve"], counts["missing"], report["roots"]["output"]))
    if browse_report(report, root, summary, inventory["excluded"],
                     [("apply", "Apply: " + ("Fill missing artwork" if policy == "fill-missing" else "Replace existing artwork"))]) != "apply":
        return
    selection = [dict(root=str(root), roms=[str(Path(path).relative_to(root)) for path in inventory["mras"]],
                      arcade=True, artwork=artwork_preferences(preferences), policy=policy,
                      corrections=read_corrections(root)[1])]
    jobs = jobs_for(selection, selection, consolemode)
    require_closed()
    result_dir = Path(tempfile.mkdtemp(prefix="arcade-update-", dir=runs))
    (result_dir / "selection.json").write_text(json.dumps(selection) + "\n")
    finish_update(jobs, result_dir)


def health_menu(bases, consolemode, runs, preferences):
    systems = configured_systems(consolemode)
    roots = sorted({root for base in bases if base.is_dir() for root in base.iterdir()
                    if root.is_dir() and not root.name.startswith(".") and key(root.name) in systems}
                   | {base.parent / "_Arcade" for base in bases if (base.parent / "_Arcade").is_dir()})
    if not roots:
        dialog("msgbox", "No configured library directories found.")
        return
    selected = dialog("menu", "Choose a library health report. Missing gamelists and artwork are included.",
                      [(str(i), str(root)) for i, root in enumerate(roots)])
    if selected is None:
        return
    root = roots[int(selected)]
    args = ["health", "--rom-root", str(root), "--consolemode-root", str(consolemode), "--format", "json"]
    if root.name == "_Arcade":
        args.append("--arcade-layout")
    for role, field in artwork_preferences(preferences).items():
        args.extend(["--" + role + "-source", field])
    result_dir = Path(tempfile.mkdtemp(prefix="health-", dir=runs))
    report, seconds = run(args, result_dir / "health.json", allow_issues=True)
    inventory = report["health"]
    summary = ("Read-only library health\n%d games on disk; %d artwork-role rows (including stale XML entries).\n"
               "%d games absent from gamelist; %d stale XML paths.\nReport: %s" %
               (inventory["game_count"], inventory["artwork_role_count"], len(inventory["unmapped_games"]),
                len(inventory["stale_mappings"]), result_dir / "health.json"))
    print("Library health: %s (%.1fs)" % (result_dir, seconds), flush=True)
    browse_report(report, root, summary, inventory["excluded"])


def browse_report(report, root, summary, excluded, actions=()):
    issues = [row for row in report["rows"] if row["operation"] in {"missing", "ambiguous", "conflict", "unsupported", "error"}]
    issues += report["diagnostics"]
    sources = report.get("source_inventory")
    source_options = []
    if sources is not None:
        unused = [entry for entry in sources["entries"] if entry["category"] == "unreferenced"]
        source_options = [("sources", "Source inventory (%d paths)" % len(sources["entries"])),
                          ("unused", "Unreferenced source images (%d)" % len(unused))]
        summary += "\n%d unreferenced image candidates, %d bytes. No deletion.\n%d unresolved source references." % (
            len(unused), sum(entry["bytes"] for entry in unused), len(sources["unresolved_references"]))
        issues += sources["unresolved_references"]
    while True:
        value = dialog("menu", summary, [("issues", "Missing artwork / conflicts (%d)" % len(issues)),
                                         ("all", "All artwork entries"),
                                         ("excluded", "Excluded paths (%d)" % len(excluded))] + source_options + list(actions) + [("0", "Back")])
        if value in {None, "0"}:
            return
        if value in {tag for tag, _ in actions}:
            return value
        entries = (unused if value == "unused" else sources["entries"] if value == "sources" else
                   issues if value == "issues" else excluded if value == "excluded" else report["rows"])
        if not entries:
            dialog("msgbox", "No entries in this category.")
            continue
        labels = ["%s%s" % (os.path.relpath(entry.get("rom") or entry.get("path") or str(root), root),
                            " [" + entry["role"] + "]" if "role" in entry else "") for entry in entries]
        chosen = dialog("menu", "Choose an entry for details. Back returns to the preview.",
                        [(str(i), label) for i, label in enumerate(labels)])
        if chosen is not None:
            entry = entries[int(chosen)]
            dialog("msgbox", labels[int(chosen)] + "\n" + entry["reason"] +
                   "\nSource: %s\nProposed output: %s" % (entry.get("source") or "None", entry.get("destination") or "None"))


def finish_update(jobs, result_dir):
    print("Reports and resume journals: %s" % result_dir)
    update(jobs, result_dir)
    (result_dir / "complete").write_text("Verified\n")
    summary = json.loads((result_dir / "summary.json").read_text())
    dialog("msgbox", "Complete: %d systems, %d outputs verified.\n%d copied, %d reused, %d existing images kept, %d missing artwork roles skipped.\n\nRestart Console Mode." %
           (len(summary), sum(item["outputs_verified"] for item in summary),
            *[sum(item.get(field, 0) for item in summary) for field in ("copied", "unchanged", "preserved", "missing")]))


def main():
    if sys.platform != "linux" or sys.version_info < (3, 9):
        raise RuntimeError("Run this menu on MiSTer with Python 3.9 or newer")
    bases = [Path("/media/fat/games")] + [Path("/media/usb%d/games" % n) for n in range(6)]
    runs = APP / "runs"
    runs.mkdir(exist_ok=True)
    preferences_path = APP / "artwork.json"
    while True:
        try:
            action = dialog("menu", "D-pad to move; your mapped Select / Back buttons to choose or return.",
                            [("1", "Preview"), ("5", "Fill missing artwork"), ("2", "Replace existing artwork"),
                             ("3", "Resume interrupted update"), ("4", "Artwork choices"),
                             ("6", "Game corrections"), ("7", "Arcade artwork"),
                             ("8", "Library health (read-only)"), ("0", "Exit")])
            if action in {None, "0"}:
                return
            if action == "7":
                preferences = artwork_preferences(json.loads(preferences_path.read_text()) if preferences_path.exists() else None)
                arcade_menu(bases, runs, preferences)
                continue
            if action == "8":
                preferences = artwork_preferences(json.loads(preferences_path.read_text()) if preferences_path.exists() else None)
                health_menu(bases, Path("/media/fat/ConsoleMode"), runs, preferences)
                continue
            if action == "4":
                preferences = artwork_preferences(json.loads(preferences_path.read_text()) if preferences_path.exists() else None)
                preferences = choose_artwork(preferences)
                if preferences is not None:
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="w", dir=APP, prefix=".artwork-", delete=False) as stream:
                            temporary = Path(stream.name)
                            stream.write(json.dumps(preferences) + "\n")
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, preferences_path)
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                continue
            if action not in {"1", "2", "3", "5", "6"}:
                continue
            policy = "fill-missing" if action == "5" else "replace"
            if action == "1":
                policy = dialog("menu", "Which update would you like to preview? No artwork will change.",
                                [("fill-missing", "Fill missing artwork; keep existing images"),
                                 ("replace", "Replace existing artwork with your choices")])
                if policy is None:
                    continue
            libraries = discover(bases, Path("/media/fat/ConsoleMode"),
                                 progress=lambda message: print(message, flush=True))
            (runs / "discovery.json").write_text(json.dumps(libraries, indent=2) + "\n")
            if action == "6":
                choose_correction(libraries)
                continue
            if action == "3":
                pending = sorted(path.parent for path in runs.glob("*/selection.json")
                                 if not (path.parent / "complete").exists())
                if not pending:
                    dialog("msgbox", "No interrupted updates.")
                    continue
                value = dialog("menu", "Choose an interrupted update",
                               [(str(number), path.name) for number, path in enumerate(pending, 1)])
                if value is None:
                    continue
                number = int(value)
                if not 1 <= number <= len(pending):
                    raise ValueError("Choose a listed update")
                result_dir = pending[number - 1]
                selection = json.loads((result_dir / "selection.json").read_text())
                for item in selection:
                    if item.get("arcade") is True:
                        root = Path(item["root"])
                        if root not in {base.parent / "_Arcade" for base in bases}:
                            raise ValueError("Arcade library unavailable; restore its original mount to resume")
                        from mister_mediaprep import inventory_entries
                        root, _ = checked_path(root)
                        inventory = inventory_entries(read_gamelist(root / "gamelist.xml"), root, [])
                        libraries.append(dict(root=str(root), arcade=True,
                                              roms=[str(Path(path).relative_to(root)) for path in inventory["games"]]))
            else:
                if not libraries:
                    dialog("msgbox", "No populated libraries with gamelist.xml found.")
                    continue
                selection = choose(libraries)
                if not selection:
                    continue
                preferences = artwork_preferences(json.loads(preferences_path.read_text()) if preferences_path.exists() else None)
                selection = [dict(item, artwork=preferences, policy=policy,
                                  corrections=read_corrections(item["root"])[1]) for item in selection]
            jobs = jobs_for(selection, libraries, Path("/media/fat/ConsoleMode"))
            if action == "1":
                result_dir = Path(tempfile.mkdtemp(prefix="preview-", dir=runs))
                print("Preview reports: %s" % result_dir)
                summaries = []
                for name, args in jobs:
                    report, seconds = run(["scan"] + args, result_dir / (name + ".json"))
                    print("%s: %s (%.1fs)" % (name, report["summary"], seconds))
                    summaries.append("%s: %d new, %d replace, %d kept, %d missing, %d saved corrections, %d fallbacks" %
                                     (name, report["summary"]["copy"], report["summary"]["replace"],
                                      report["summary"].get("preserve", 0), report["summary"]["missing"],
                                      sum(row.get("artwork_field") == "correction" for row in report.get("rows", [])),
                                      sum(bool(row.get("artwork_fallback")) for row in report.get("rows", []))))
                label = "Fill missing artwork" if policy == "fill-missing" else "Replace existing artwork"
                dialog("msgbox", label + " preview complete. No artwork changed.\n\n" + "\n".join(summaries))
                continue
            require_closed()
            if action in {"2", "5"}:
                result_dir = Path(tempfile.mkdtemp(prefix="update-", dir=runs))
                (result_dir / "selection.json").write_text(json.dumps(selection) + "\n")
            finish_update(jobs, result_dir)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, ET.ParseError) as error:
            dialog("msgbox", "Stopped: %s\n\nFor an interrupted apply, use Resume after resolving the problem." % error)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        print("\nExited. Any interrupted update can be resumed.")
    except (OSError, RuntimeError) as error:
        print("Cannot open MiSTer menu: %s" % error, file=sys.stderr)
        sys.exit(1)
