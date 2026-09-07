"""Artwork preparation for MiSTer Console Mode (Python 3.9+)."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
import stat as filemode
from pathlib import Path, PureWindowsPath
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET


VERSION = "0.1.3-dev"

ARTWORK_SOURCES = {"box": {"boxart2d": "2D box art", "boxart3d": "3D box art"},
                   "background": {"screenshot": "Screenshot", "titlescreen": "Title screen"}}
ROLES = {role: next(iter(fields)) for role, fields in ARTWORK_SOURCES.items()}
GENERIC_FIELDS = ("image", "thumbnail")
ARTWORK_FOLDERS = {"box2d": "boxart2d", "boxart2d": "boxart2d", "box3d": "boxart3d",
                   "boxart3d": "boxart3d", "screenshot": "screenshot", "titlescreen": "titlescreen"}
EXTENSIONS = {".png", ".jpg", ".jpeg"}
STATUSES = ("copy", "replace", "preserve", "missing", "ambiguous", "conflict", "unsupported", "error")


class PathProblem(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def artwork_preferences(value=None):
    if value is None:
        return dict(ROLES)
    if (not isinstance(value, dict) or set(value) != set(ROLES)
            or any(not isinstance(field, str) or field not in ARTWORK_SOURCES[role] for role, field in value.items())):
        raise ValueError("Invalid artwork choices; choose a listed box and background type")
    return dict(value)


def artwork_candidates(game, parent, role, preferred):
    fields = [preferred] + [field for field in ARTWORK_SOURCES[role] if field != preferred]
    for entry, inherited in ((game, False), (parent, True)):
        if entry is None:
            continue
        explicit = {field: entry.findtext(field, "").strip() for field in fields}
        generic = defaultdict(list)
        for tag in GENERIC_FIELDS:
            for node in entry.findall(tag):
                value = (node.text or "").strip()
                if not value:
                    continue
                kinds = {ARTWORK_FOLDERS[key(part)] for part in value.replace("\\", "/").split("/")[:-1]
                         if key(part) in ARTWORK_FOLDERS}
                if len(kinds) != 1:
                    if not any(explicit.values()):
                        raise PathProblem("ambiguous", f"Cannot determine artwork type from {tag}: {value}")
                    continue  # Explicit role tags take precedence over untyped artwork.
                generic[next(iter(kinds))].append((tag, value, inherited, next(iter(kinds))))
        values = []
        for field in fields:
            if explicit[field]:
                values.append((field, explicit[field], inherited, field))
                continue
            candidates = generic[field]
            if len({value.replace("\\", "/") for _, value, _, _ in candidates}) > 1:
                raise PathProblem("ambiguous", f"Conflicting generic mappings for {field}: " +
                                  ", ".join(value for _, value, _, _ in candidates))
            values.extend(candidates[:1])
        if values:
            # A game's own mapping is a correction, even when it uses the fallback type.
            return values
    return []


def read_gamelist(gamelist):
    # ponytail: bounded UTF-8 XML only; add other encodings when a real set needs them.
    with gamelist.open("rb") as stream:
        data = stream.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Gamelist exceeds the 16 MiB scan limit")
    xml = data.decode("utf-8-sig")
    if "<!DOCTYPE" in xml.upper() or "<!ENTITY" in xml.upper() or "\x00" in xml:
        raise ValueError("DTD, entity declarations and non-UTF-8 XML are not supported")
    root = ET.fromstring(xml)
    if root.tag != "gameList":
        raise ValueError("Expected a gameList XML root")
    return root


def key(name):
    return unicodedata.normalize("NFC", name).casefold()


def artwork_name(name):
    return not name.startswith("._") and name != ".DS_Store"


def checked_path(path, *, missing=False, entries_cache=None):
    """Walk actual directory entries: host case folding must not hide ambiguity."""
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    fallback = False
    for part in path.parts[1:]:
        cached_directory = entries_cache is not None and current in entries_cache
        if not cached_directory and not current.is_dir():
            if missing and not current.exists():
                current /= part
                continue
            raise PathProblem("error", f"Not a directory: {current}")
        if cached_directory:
            entries = entries_cache[current]
        else:
            entries = list(current.iterdir())
            if entries_cache is not None:
                entries_cache[current] = entries
        if entries_cache is None:
            exact = [p for p in entries if p.name == part]
            candidates = exact or [p for p in entries if key(p.name) == key(part)]
        else:
            lookup_key = ("lookup", current)
            if lookup_key not in entries_cache:
                names, normalized = {}, defaultdict(list)
                for entry in entries:
                    names[entry.name] = entry
                    normalized[key(entry.name)].append(entry)
                entries_cache[lookup_key] = names, normalized
            names, normalized = entries_cache[lookup_key]
            exact = [names[part]] if part in names else []
            candidates = exact or normalized.get(key(part), [])
        if len(candidates) > 1:
            raise PathProblem("ambiguous", f"Ambiguous path component: {current / part}")
        if not candidates:
            if missing:
                current /= part
                continue
            raise PathProblem("missing", f"Path does not exist: {current / part}")
        current = candidates[0]
        marker = ("checked", current)
        if entries_cache is None or marker not in entries_cache:
            if current.is_symlink():
                raise PathProblem("error", f"Symlink is not allowed: {current}")
            if entries_cache is not None:
                entries_cache[marker] = True
        fallback |= not bool(exact)
    return current, "case-unicode" if fallback else "exact"


def mapped_path(value, base, mappings):
    if "\x00" in value:
        raise PathProblem("error", "NUL in path")
    value = value.replace("\\", "/")
    if ".." in value.split("/"):
        raise PathProblem("error", f"Parent traversal is not allowed: {value}")
    # Longest explicit prefix wins, with path-component boundaries.
    for old, new in sorted(mappings, key=lambda pair: -len(pair[0])):
        if value == old or value.startswith(old + "/"):
            return new / value[len(old):].lstrip("/"), "remapped"
    if PureWindowsPath(value).drive:
        raise PathProblem("error", f"Foreign absolute path requires --map-root: {value}")
    p = Path(value)
    return p if p.is_absolute() else base / p, "xml"


def resolve(value, base, roots, mappings, entries_cache):
    path, origin = mapped_path(value, base, mappings)
    actual, basis = checked_path(path, entries_cache=entries_cache)
    if not any(actual.is_relative_to(root) for root in roots):
        raise PathProblem("error", f"Path escapes input roots: {actual}")
    if not actual.is_file():
        raise PathProblem("unsupported", f"Expected a regular file: {actual}")
    return actual, f"{origin}:{basis}"


def image_check(path):
    if not artwork_name(path.name) or path.suffix.lower() not in EXTENSIONS:
        raise PathProblem("unsupported", f"Not a supported artwork candidate: {path}")
    with path.open("rb") as stream:
        header = stream.read(8)
    if header == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    raise PathProblem("unsupported", f"Unrecognized PNG/JPEG image signature: {path}")


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.digest()


def artwork_references(document):
    """Keep full XML context before game inventory filters excluded entries."""
    fields = set(GENERIC_FIELDS) | {field for types in ARTWORK_SOURCES.values() for field in types}
    fields |= {"logo", "marquee", "wheel", "fanart", "video", "manual"}
    children = defaultdict(list)
    for game in document.findall("game"):
        if game.get("parentid"):
            children[game.get("parentid")].append(game.findtext("path", ""))
    references = []
    for entry in document:
        label = entry.findtext("path", "") or "canonical:" + entry.get("id", "unknown")
        for node in entry:
            value = (node.text or "").strip()
            if value and node.tag != "path" and (node.tag in fields or Path(value).suffix.lower() in EXTENSIONS):
                references.append(dict(value=value, field=node.tag, entry=label,
                                       used_by=sorted(children.get(entry.get("id"), []))))
    return references


def source_inventory(references, gamelist, rom_root, output, input_roots, mappings, overrides, corrections):
    """Inventory paths and logical sizes; no image decoding or cleanup decisions."""
    from corrections import STORE
    roots = {rom_root / "media", output, rom_root / STORE, *overrides.values()}
    referenced, identities = defaultdict(list), defaultdict(list)
    entries, unresolved, cache = {}, [], {}
    uncertain = not gamelist.exists()
    for reference in references:
        try:
            path, _ = resolve(reference["value"], gamelist.parent, input_roots, mappings, cache)
            referenced[path].append(reference)
            roots.add(path.parent)
        except (PathProblem, OSError) as error:
            unresolved.append(dict(path=reference["value"], reason=str(error)))
            uncertain |= not isinstance(error, PathProblem) or error.status != "missing"
        # Folder overrides use the XML basename, including currently unchosen types.
        for folder in overrides.values():
            try:
                path, _ = resolve(Path(reference["value"].replace("\\", "/")).name,
                                  folder, [folder], [], cache)
                referenced[path].append(dict(reference, override=str(folder)))
            except (PathProblem, OSError) as error:
                if not isinstance(error, PathProblem) or error.status != "missing":
                    unresolved.append(dict(path=str(folder / Path(reference["value"]).name), reason=str(error)))
                    uncertain = True
    saved = defaultdict(list)
    for rom, roles in corrections.items():
        for role, name in roles.items():
            saved[rom_root / STORE / name].append(dict(entry=rom, field=role))
    for path in set(referenced) | set(saved):
        try:
            info = path.stat()
            identities[info.st_dev, info.st_ino].append(str(path))
        except OSError as error:
            unresolved.append(dict(path=str(path), reason=str(error)))
            uncertain = True
    def excluded(path, reason):
        entries[str(path)] = dict(path=str(path), category="excluded", bytes=0, reason=reason)
    checked_roots = []
    for root in sorted(roots, key=lambda p: (len(p.parts), str(p))):
        try:
            root, _ = checked_path(root, missing=True)
            if not root.exists() or any(root.is_relative_to(parent) for parent in checked_roots):
                continue
            if not root.is_dir():
                raise ValueError("Inventory root is not a directory")
            checked_roots.append(root)
        except (OSError, ValueError) as error:
            excluded(root, str(error))
    def walk_error(error):
        excluded(error.filename, str(error))
    paths = set(referenced) | set(saved)
    for root in checked_roots:
        for folder, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
            folder = Path(folder)
            for name in list(directories):
                path = folder / name
                if path.is_symlink() or (name.startswith(".") and name != STORE):
                    directories.remove(name)
                    excluded(path, "Symlink or hidden directory not traversed")
            directories.sort()
            paths.update(folder / name for name in files)
    for path in sorted(paths):
        name = path.name
        try:
            info = path.lstat()
            if not filemode.S_ISREG(info.st_mode) or not artwork_name(name):
                excluded(path, "Not a regular source file, or operating-system metadata")
                continue
            aliases = identities.get((info.st_dev, info.st_ino), [])
            category = ("saved-correction" if path in saved else "referenced" if path in referenced else
                        "reference-alias" if aliases else "other" if path.suffix.lower() not in EXTENSIONS else
                        "retained-snapshot" if path.is_relative_to(rom_root / STORE) else
                        "optimized" if any(path.is_relative_to(p / "optimized") for p in (output, rom_root / "media")) else
                        "prepared" if path.parent == output else "unverified" if uncertain else "unreferenced")
            refs = saved.get(path, []) + referenced.get(path, [])
            reason = category + "; %d bytes" % info.st_size
            if refs:
                reason += "; " + "; ".join(sorted({r["field"] + ": " + r["entry"] for r in refs}))
            if category == "unreferenced":
                reason += "; PNG/JPEG filename absent from resolved XML references; not a deletion recommendation"
            if category == "unverified":
                reason += "; unresolved references or absent gamelist prevent an unused-file conclusion"
            entries[str(path)] = dict(path=str(path), bytes=info.st_size, category=category,
                                      references=refs, aliases=aliases, reason=reason)
        except OSError as error:
            excluded(path, str(error))
    totals = defaultdict(lambda: dict(paths=0, bytes=0))
    for entry in entries.values():
        totals[entry["category"]]["paths"] += 1
        totals[entry["category"]]["bytes"] += entry["bytes"]
    return dict(scope="Full library XML, media, correction snapshots, referenced folders and explicit overrides",
                roots=[str(p) for p in checked_roots], entries=[entries[p] for p in sorted(entries)],
                summary=dict(sorted(totals.items())), unresolved_references=unresolved,
                note="Logical sizes count each path, including hard links. Filename inventory is not image validation. Excluded sizes are unmeasured. Nothing is deleted.")


def inventory_entries(document, root, mappings, progress=None, extensions=None):
    """Add eligible on-disk games missing from an in-memory gamelist."""
    from deployment import layout_problem
    arcade = extensions is None
    def problem(path):
        return (None if path.suffix.lower() == ".mra" else "Gamelist path is not an MRA") if arcade else layout_problem(path, extensions)
    found, mapped, excluded, errors, stale = set(), set(), {}, [], []
    cache = {}
    def excluded_part(path):
        return next((part for part in path.relative_to(root).parts
                     if part.startswith(".") or key(part) in {"_organized", "media", "cores"}), None)
    def walk_error(error):
        errors.append(dict(status="error", path=error.filename, reason=str(error)))
    for folder, directories, files in os.walk(root, followlinks=False, onerror=walk_error):
        folder = Path(folder)
        cache[folder] = [folder / name for name in directories + files]
        for name in list(directories):
            path = folder / name
            part = excluded_part(path)
            if part or path.is_symlink():
                directories.remove(name)
                excluded[str(path)] = "Excluded folder: " + part if part else "Symlink directory not followed"
        directories.sort()
        for name in sorted(files):
            path = folder / name
            if arcade and path.suffix.lower() != ".mra":
                continue
            part = excluded_part(path)
            if part or path.is_symlink() or not path.is_file():
                excluded[str(path)] = "Excluded metadata: " + part if part else "Not a regular, non-symlink game"
                continue
            if problem(path):
                excluded[str(path)] = problem(path)
                continue
            found.add(str(path))
            if progress and len(found) % 100 == 0:
                progress("Discovered %d games" % len(found))
    for game in list(document.findall("game")):
        value = game.findtext("path", "").strip()
        if not value:
            continue  # Keep canonical parents and let scan report malformed entries.
        try:
            path, _ = mapped_path(value, root, mappings)
            if not path.is_relative_to(root):
                raise PathProblem("error", "Game path escapes library root")
            part = excluded_part(path)
            if part or problem(path):
                excluded[str(path)] = "Excluded folder/metadata: " + part if part else problem(path)
                if game.get("id"):
                    game.remove(game.find("path"))  # Its canonical media may still serve an included MRA.
                else:
                    document.remove(game)
                continue
            actual, _ = resolve(value, root, [root], mappings, cache)
            if str(actual) in found:
                mapped.add(str(actual))
        except PathProblem as error:
            if error.status == "missing":
                stale.append(dict(path=value, reason=str(error)))
            # Keep bad paths: scan reports their errors without guessing a replacement.
        except OSError:
            pass  # Scan reports filesystem failures per entry.
    for path in sorted(found - mapped):
        game = ET.SubElement(document, "game")
        ET.SubElement(game, "path").text = path
    return dict(games=sorted(found), mapped_games=len(mapped), unmapped_games=sorted(found - mapped),
                stale_mappings=stale, excluded=[dict(path=path, reason=reason) for path, reason in sorted(excluded.items())],
                errors=errors)


def scan(args):
    from corrections import STORE, MANIFEST, read as read_corrections
    health_mode = getattr(args, "command", None) == "health"
    preferences = artwork_preferences({role: getattr(args, role + "_source", default)
                                       for role, default in ROLES.items()})
    gamelist, _ = checked_path(args.gamelist, missing=health_mode)
    rom_root, _ = checked_path(args.rom_root)
    output, _ = checked_path(args.output, missing=True)
    if not rom_root.is_dir() or (not gamelist.is_file() and not (health_mode and not gamelist.exists())):
        raise ValueError("--rom-root must be a directory and --gamelist a file")
    if output.exists() and not output.is_dir():
        raise ValueError("--output must be a directory or an absent directory path")
    corrections, corrections_stamp = read_corrections(rom_root)
    mappings = []
    for old, new in args.map_root:
        old = old.replace("\\", "/").rstrip("/")
        if not old or ".." in old.split("/") or any(old == item[0] for item in mappings):
            raise ValueError("--map-root requires distinct nonempty prefixes without parent traversal")
        root, _ = checked_path(new)
        if not root.is_dir():
            raise ValueError("--map-root destination must be a directory")
        mappings.append((old, root))
    overrides = {}
    for role in ROLES:
        value = getattr(args, role + "_dir")
        if value:
            root, _ = checked_path(value)
            if not root.is_dir():
                raise ValueError(f"--{role}-dir must be a directory")
            overrides[role] = root

    root = read_gamelist(gamelist) if gamelist.exists() else ET.Element("gameList")
    references = artwork_references(root) if health_mode or getattr(args, "source_inventory", False) else None
    source_games = root.findall("game")
    arcade_mode = getattr(args, "command", None) == "arcade-preview" or getattr(args, "arcade_layout", False)
    inventory = None
    if health_mode or arcade_mode:
        extensions = None
        if health_mode and not arcade_mode:
            from deployment import configured_systems
            extensions = configured_systems(args.consolemode_root).get(key(rom_root.name))
            if not extensions:
                raise ValueError("Library is not configured in Console Mode")
        inventory = inventory_entries(root, rom_root, mappings, getattr(args, "progress", None), extensions)
    arcade = None
    if arcade_mode:
        arcade = dict(inventory)
        for old, new in (("games", "mras"), ("mapped_games", "mapped_mras"), ("unmapped_games", "unmapped_mras")):
            arcade[new] = arcade.pop(old)
    games = root.findall("game")
    parents = defaultdict(list)
    for game in games:
        if game.get("id"):
            parents[game.get("id")].append(game)
    diagnostics = list(inventory["errors"]) if inventory is not None else []
    if not gamelist.exists():
        diagnostics.append(dict(status="missing", path=str(gamelist), reason="No gamelist.xml; on-disk games have no XML mappings"))
    for identifier, entries in sorted(parents.items()):
        if len(entries) > 1:
            diagnostics.append({"status": "error", "reason": f"Duplicate canonical ID: {identifier}"})
    for folder in root.findall("folder"):
        diagnostics.append({"status": "unsupported", "reason": "Folder artwork inheritance is not supported", "path": folder.findtext("path", "")})
    existing = list(output.iterdir()) if output.exists() else []
    existing_by_stem = defaultdict(list)
    for path in existing:
        if path.suffix.lower() in EXTENSIONS:
            existing_by_stem[key(path.stem)].append(path)
    rows = []
    entries_cache = {}  # A scan snapshot, never reusable for deployment checks.
    input_roots = [gamelist.parent, rom_root] + [new for _, new in mappings]
    selected = set()
    for value in args.rom:
        path, _ = resolve(value, rom_root, [rom_root], [], entries_cache)
        if arcade is not None and str(path) not in arcade["mras"]:
            raise ValueError("Choose a discovered MRA outside excluded folders: " + str(path))
        selected.add(str(path))
    selected_stems = {key(Path(p).stem + suffix) for p in selected for suffix in ("", "-BG")}
    formats, file_stats, hashes = {}, {}, {}
    def info(path):
        path = Path(path)
        if path not in file_stats:
            file_stats[path] = path.stat()
        return file_stats[path]
    progress = getattr(args, "progress", None)
    for index, game in enumerate(games, 1):
        if progress and index % 100 == 0:
            progress(f"Scan {index}/{len(games)} XML entries")
        rom_value = game.findtext("path", "").strip()
        if not rom_value and game.get("id"):
            continue
        parent_id = game.get("parentid")
        parent = None
        problem = None
        if parent_id:
            matches = parents[parent_id]
            if len(matches) != 1:
                problem = f"Expected one canonical parent for {parent_id}; found {len(matches)}"
            else:
                parent = matches[0]
        if game.get("id") and len(parents[game.get("id")]) > 1:
            problem = f"Duplicate canonical ID: {game.get('id')}"
        for role in ROLES:
            row = dict(rom=rom_value, canonical_id=parent_id or game.get("id"), role=role,
                       source=None, destination=None, match_basis={}, existing_candidates=[],
                       operation="error", reason="", cache_action="none")
            rows.append(row)
            try:
                if not rom_value:
                    raise PathProblem("error", "Game has no ROM path")
                rom, basis = resolve(rom_value, gamelist.parent, [rom_root], mappings, entries_cache)
                if not artwork_name(rom.name):
                    raise PathProblem("unsupported", "ROM path is metadata")
                row["rom"] = str(rom)
                row["match_basis"]["rom"] = basis
                if inventory is not None:
                    row["target_stem"] = rom.stem + ("-BG" if role == "background" else "")
                saved = corrections.get(rom.relative_to(rom_root).as_posix(), {}).get(role)
                if problem and not saved:
                    raise PathProblem("error", problem)
                missing = PathProblem("missing", f"No {role} artwork mapping")
                choices = ([("correction", str(rom_root / STORE / saved), False, None)] if saved else
                           artwork_candidates(game, parent, role, preferences[role]))
                for field, value, inherited, artwork_type in choices:
                    try:
                        if role in overrides and not saved:
                            # Validate original syntax before selecting only its filename.
                            original, _ = mapped_path(value, gamelist.parent, mappings)
                            source, basis = resolve(original.name, overrides[role], [overrides[role]], [], entries_cache)
                            basis = "folder-override:" + basis
                        else:
                            source, basis = resolve(value, gamelist.parent, input_roots, mappings, entries_cache)
                    except PathProblem as exc:
                        if exc.status != "missing":
                            raise
                        missing = exc
                        continue
                    break
                else:
                    raise missing
                row["source"] = str(source)
                row["artwork_field"] = field
                row["artwork_type"] = artwork_type
                row["artwork_fallback"] = not saved and artwork_type != preferences[role]
                row["match_basis"]["artwork"] = ("correction:" if saved else "parent:" if inherited else "direct:") + basis
                row["match_basis"]["artwork_field"] = field
                stem = rom.stem + ("-BG" if role == "background" else "")
                # Resolve every input for namespace and alias protection, but do
                # not open unrelated images during a selected-game operation.
                if selected and str(rom) not in selected and key(stem) not in selected_stems:
                    row["operation"] = "unselected"
                    continue
                if source not in formats:
                    formats[source] = image_check(source)
                extension = formats[source]
                destination = output / (stem + extension)
                row["destination"] = str(destination)
                row["image_format"] = extension[1:]
                row["extension_corrected"] = (source.suffix.lower() not in {".jpg", ".jpeg"}
                                               if extension == ".jpg" else source.suffix.lower() != extension)
                occupied = existing_by_stem[key(stem)]
                row["existing_candidates"] = sorted(str(p) for p in occupied)
                for p in occupied:
                    if p.is_symlink() or not p.is_file():
                        raise PathProblem("conflict", f"Output is not a regular, non-symlink file: {p}")
                    if arcade is not None and args.replace and (p.stem != stem or (key(p.name) == key(destination.name) and p.name != destination.name)):
                        raise PathProblem("conflict", f"Case/Unicode output alias requires review: {p}")
                    if (info(source).st_dev, info(source).st_ino) == (info(p).st_dev, info(p).st_ino):
                        raise PathProblem("conflict", "Source and output refer to the same file")
                if len(occupied) > 1 and (not args.replace or any(p.stem != stem for p in occupied)):
                    raise PathProblem("conflict", "Multiple existing artwork variants; frontend precedence unknown")
                row["operation"] = ("replace" if args.replace else "preserve") if occupied else "copy"
                row["reason"] = ("Would replace existing artwork; deployment preflight required" if occupied and args.replace
                                 else "Existing artwork preserved" if occupied else "Would copy; deployment preflight required")
                if row["extension_corrected"]:
                    row["reason"] += "; destination extension follows image bytes; no conversion"
                if row["artwork_fallback"]:
                    row["reason"] += f"; using {artwork_type} fallback for {preferences[role]}"
                if saved:
                    row["reason"] += "; using saved game correction"
                if args.replace:
                    row["cache_action"] = "unknown; replacement cannot be applied yet"
            except PathProblem as exc:
                row["operation"], row["reason"] = exc.status, str(exc)
            except OSError as exc:
                row["operation"], row["reason"] = "error", str(exc)

    # Unchosen types are still source artwork; switching preferences must not
    # delete them as superseded raw outputs or cache aliases. No image reads needed.
    referenced_sources = {str(rom_root / STORE / name) for roles in corrections.values() for name in roles.values()}
    source_fields = set(GENERIC_FIELDS) | {field for fields in ARTWORK_SOURCES.values() for field in fields}
    values = {(node.text or "").strip() for game in source_games
              for field in source_fields for node in game.findall(field)} - {""}
    for value in values:
        try:
            source, _ = resolve(value, gamelist.parent, input_roots, mappings, entries_cache)
        except (PathProblem, OSError):
            continue  # Selected candidates already report errors; unused mappings need not exist.
        referenced_sources.add(str(source))
    # Protect sources referenced by other games too, including hard-link aliases.
    source_ids = {(info(source).st_dev, info(source).st_ino) for source in referenced_sources}
    for row in rows:
        if row["source"]:
            try:
                stat = info(row["source"])
                source_ids.add((stat.st_dev, stat.st_ino))
            except OSError as exc:
                row["operation"], row["reason"] = "error", str(exc)
    for row in rows:
        if row["operation"] in {"copy", "preserve", "replace"}:
            try:
                for candidate in row["existing_candidates"]:
                    stat = info(candidate)
                    if (stat.st_dev, stat.st_ino) in source_ids:
                        row["operation"], row["reason"] = "conflict", "Output aliases an input artwork file"
            except OSError as exc:
                row["operation"], row["reason"] = "error", str(exc)
    groups = defaultdict(list)
    for row in rows:
        if row["destination"]:
            groups[key(Path(row["destination"]).stem)].append(row)
        elif row.get("target_stem") and row["operation"] != "unselected":
            # Unmapped MRAs still reserve their frontend filename namespace.
            groups[key(row["target_stem"])].append(row)
    for group in groups.values():
        if len(group) < 2:
            continue
        try:
            for row in group:
                if row["source"] and row["source"] not in hashes:
                    hashes[row["source"]] = digest(row["source"])
            compatible = (len({row["destination"] for row in group}) == 1
                          and all(row["operation"] in {"copy", "preserve", "replace"} for row in group)
                          and len({hashes[row["source"]] for row in group}) == 1)
            for row in group:
                if compatible:
                    row["reason"] += "; shared destination with byte-identical artwork"
                elif row["operation"] in {"copy", "preserve", "replace"}:
                    row["operation"], row["reason"] = "conflict", "Incompatible shared output namespace"
        except OSError as exc:
            for row in group:
                row["operation"], row["reason"] = "error", str(exc)
    protected_inputs = {str(gamelist)} | referenced_sources
    if corrections_stamp is not None:
        protected_inputs.add(str(rom_root / STORE / MANIFEST))
    for row in rows:
        if row["source"]:
            protected_inputs.add(row["source"])
        if "rom" in row["match_basis"]:
            protected_inputs.add(row["rom"])
    if args.rom:
        if selected - {row["rom"] for row in rows}:
            raise ValueError("A --rom selection has no gamelist entry")
        rows = [row for row in rows if row["rom"] in selected]
    rows.sort(key=lambda row: (row["rom"], row["role"], row["source"] or ""))
    counts = Counter(row["operation"] for row in rows)
    report = dict(schema_version=1, selection=sorted(selected), artwork_preferences=preferences, corrections=corrections_stamp,
                roots=dict(gamelist=str(gamelist), rom=str(rom_root), output=str(output),
                artwork_overrides={role: str(path) for role, path in overrides.items()},
                mappings=[dict(old=old, new=str(new)) for old, new in mappings]),
                protected_inputs=sorted(protected_inputs),
                policy="replace" if args.replace else "fill-missing", compatibility="File-stem naming; supply --consolemode-root for deployment preflight",
                cache_action="unknown; no cache inspected or changed", rows=rows, diagnostics=diagnostics,
                summary={status: counts[status] for status in STATUSES})
    if arcade is not None:
        report.update(arcade=arcade, read_only=getattr(args, "command", None) == "arcade-preview",
                      compatibility="Arcade preview only; MRA-stem artwork beside the Arcade root. Use apply with --arcade-layout to deploy.",
                      cache_action="none; Arcade caches are not inspected or changed")
        if not report["read_only"]:
            report["compatibility"] = "Arcade MRA-stem artwork beside the Arcade root; deployment preflight required"
        unmapped = set(arcade["unmapped_mras"])
        for row in rows:
            row["cache_action"] = "none; preview only"
            if row["rom"] in unmapped:
                row["reason"] = "No gamelist entry for this MRA; " + row["reason"]
            row["gamelist_mapping"] = "absent" if row["rom"] in unmapped else "present"
    if health_mode:
        report.update(health=inventory, read_only=True,
                      compatibility="Library health report; no artwork or caches changed",
                      cache_action="none; read-only health report")
        unmapped = set(inventory["unmapped_games"])
        for row in rows:
            row["cache_action"] = "none; read-only health report"
            row["gamelist_mapping"] = "absent" if row["rom"] in unmapped else "present"
            if row["rom"] in unmapped and not arcade_mode:
                row["reason"] = "No gamelist entry for this game; " + row["reason"]
        inventory["game_count"] = len(inventory["games"])
        inventory["artwork_role_count"] = len(rows)
    if references is not None:
        if progress:
            progress("Inventorying source artwork across the full library")
        report["source_inventory"] = source_inventory(references, gamelist, rom_root, output,
                                                     input_roots, mappings, overrides, corrections)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(prog="mister-mediaprep", description="Prepare your artwork for MiSTer Console Mode.")
    parser.add_argument("--version", action="version", version="MiSTer MediaPrep " + VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    correction = sub.add_parser("correction", help="Save current or chosen game artwork across future scrapes")
    correction.add_argument("--rom-root", required=True)
    correction.add_argument("--rom", required=True, help="Exact ROM path relative to its library")
    action = correction.add_mutually_exclusive_group(required=True)
    action.add_argument("--keep", choices=("both", *ROLES))
    action.add_argument("--remove", action="store_true", help="Use scraped artwork again on the next Replace update")
    action.add_argument("--source", nargs=2, metavar=("ROLE", "PATH"),
                        help="Save an existing box/background source inside this library for a future update")
    for mode in ("scan", "apply", "arcade-preview", "health"):
        command = sub.add_parser(mode, help="Preview MRA artwork without writes" if mode == "arcade-preview" else
                                 "Inspect library coverage without writes" if mode == "health" else
                                 "Plan without writes" if mode == "scan" else "Deploy to verified Console Mode artwork layouts")
        if mode == "arcade-preview":
            command.add_argument("--arcade-root", required=True, help="The Arcade folder containing MRAs and gamelist.xml")
            command.set_defaults(consolemode_root=None)
        elif mode == "health":
            command.add_argument("--rom-root", required=True, help="Library directory, including libraries without gamelist.xml")
            command.set_defaults(rom=[])
        else:
            for name in ("gamelist", "rom-root", "output"):
                command.add_argument("--" + name, required=True)
        for role in ROLES:
            command.add_argument("--" + role + "-dir", help="Override the artwork folder for this role")
            command.add_argument("--" + role + "-source", choices=ARTWORK_SOURCES[role], default=ROLES[role],
                                 help="Preferred XML artwork type; falls back to the other type if missing")
        command.add_argument("--map-root", nargs=2, action="append", default=[], metavar=("OLD", "NEW"))
        command.add_argument("--replace", action="store_true", help="Replace existing artwork instead of preserving it")
        if mode != "health":
            command.add_argument("--rom", action="append", default=[], help="MRA path relative to --arcade-root; repeat to select more" if mode == "arcade-preview" else
                                 "ROM path relative to --rom-root; repeat to select more")
        if mode != "arcade-preview":
            command.add_argument("--consolemode-root", required=mode in {"apply", "health"}, help="Installed ConsoleMode folder; enables version/layout/cache preflight")
            command.add_argument("--experimental-layout", action="store_true", help="Allow an explicit selected-game device test")
            command.add_argument("--arcade-layout", action="store_true", help="Inventory MRAs and use the verified media folder beside --rom-root")
        command.add_argument("--quiet", action="store_true", help="Suppress progress on stderr")
        if mode in {"scan", "arcade-preview"}:
            command.add_argument("--source-inventory", action="store_true",
                                 help="Include full-library source paths and sizes, including unreferenced candidates")
        if mode == "apply":
            checkpoint = command.add_mutually_exclusive_group()
            checkpoint.add_argument("--journal", help="Create a durable progress journal outside the library")
            checkpoint.add_argument("--resume", help="Resume unfinished games from a journal; rescan before writes")
        command.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    last_progress = [0.0]
    def progress(message, force=False):
        now = time.monotonic()
        if not args.quiet and (force or now - last_progress[0] >= 1):
            print(message, file=sys.stderr, flush=True)
            last_progress[0] = now
    args.progress = progress
    try:
        if args.command == "health":
            root, _ = checked_path(args.rom_root)
            args.gamelist = str(root / "gamelist.xml")
            args.output = str((root.parent if args.arcade_layout else root) / "media")
        if args.command == "arcade-preview":
            root, _ = checked_path(args.arcade_root)
            if not root.is_dir():
                raise ValueError("--arcade-root must be a directory")
            args.rom_root, args.gamelist, args.output = str(root), str(root / "gamelist.xml"), str(root.parent / "media")
        if args.command == "correction":
            from corrections import save
            if sys.platform == "linux":
                from native_library import require_closed
                require_closed()
            roles = [args.source[0]] if args.source else list(ROLES) if args.keep == "both" else [args.keep] if args.keep else []
            kept = save(args.rom_root, args.rom, roles,
                        sources={args.source[0]: args.source[1]} if args.source else None)
            print(json.dumps({"rom": args.rom, "saved_roles": sorted(kept)}))
            return 0
        if args.command == "apply" and args.resume:
            from journal import resume
            if not resume(args):
                print(json.dumps({"resume_complete": True, "pending_games": 0}) if args.format == "json" else "Journal complete; no unfinished games")
                return 0
        progress("Scanning gamelist and artwork mappings", True)
        report = scan(args)
        if args.command == "apply" and args.resume:
            from journal import recover_fill_missing
            recover_fill_missing(args, report)
        if args.consolemode_root and args.command != "health":
            from deployment import prepare, apply
            progress("Checking frontend, output paths, and caches", True)
            prepare(report, args.consolemode_root, experimental=args.experimental_layout)
            if args.command == "apply":
                if args.arcade_layout and sys.platform == "linux":
                    from native_library import require_closed
                    require_closed()
                from journal import recording
                with recording(args, report) as record:
                    apply(report, progress=progress, record=record)
                progress(f"Complete: {report['copied_files']} copied, {report['unchanged_files']} byte-identical; failure: {report['apply_failed']}", True)
    except (ValueError, OSError, RuntimeError, ET.ParseError) as exc:
        print(f"mister-mediaprep: {exc}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["compatibility"])
        if "source_inventory" in report:
            inventory = report["source_inventory"]
            print(inventory["scope"] + "; " + inventory["note"])
            for category, totals in inventory["summary"].items():
                print(f"{category}: {totals['paths']} paths, {totals['bytes']} bytes")
            for entry in inventory["entries"]:
                print(f"{entry['path']}: {entry['reason']}")
            for entry in inventory["unresolved_references"]:
                print(f"Unresolved reference {entry['path']}: {entry['reason']}")
        if "health" in report:
            inventory = report["health"]
            print(f"Games on disk: {inventory['game_count']}; artwork-role rows: {inventory['artwork_role_count']}; absent from gamelist: {len(inventory['unmapped_games'])}; stale XML paths: {len(inventory['stale_mappings'])}")
            for item in inventory["excluded"]:
                print(f"excluded: {item['path']}: {item['reason']}")
        if "arcade" in report:
            inventory = report["arcade"]
            print(f"MRAs: {len(inventory['mras'])}; mapped: {inventory['mapped_mras']}; absent from gamelist: {len(inventory['unmapped_mras'])}; excluded paths: {len(inventory['excluded'])}")
            for item in inventory["excluded"]:
                print(f"excluded: {item['path']}: {item['reason']}")
        print(" | ".join(f"{name}: {count}" for name, count in report["summary"].items()))
        for row in report["rows"]:
            print(f"{row['operation']}: {row['rom']} [{row['role']}]\n"
                  f"  {row['source']} -> {row['destination']}\n"
                  f"  {row['reason']}; match={row['match_basis']}; existing={row['existing_candidates']}; cache={row['cache_action']}")
        for item in report["diagnostics"]:
            print(f"{item['status']}: {item['reason']}")
        if args.command == "apply":
            for row in report["rows"]:
                print(f"{row['result']}: {row['destination'] or row['rom']}")
            print(f"Applied files: {report['applied_files']}; failure: {report['apply_failed']}")
    return int(report.get("apply_failed", False) or bool(report["diagnostics"]) or any(report["summary"][s] for s in ("ambiguous", "conflict", "unsupported", "error")))


def entrypoint():
    if len(sys.argv) > 1:
        sys.exit(main())
    from native_menu import main as menu
    try:
        menu()
    except (EOFError, KeyboardInterrupt):
        print("\nExited. Any interrupted update can be resumed.")
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
