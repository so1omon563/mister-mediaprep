"""Filesystem application for verified Console Mode 1.1.3 artwork layouts."""

from contextlib import contextmanager
from collections import defaultdict
import configparser
import ctypes
import errno
from fnmatch import fnmatchcase
import hashlib
import os
from pathlib import Path
import shutil
import stat
import sys
import uuid

from mister_mediaprep import EXTENSIONS, checked_path, digest, image_check, key

FRONTEND_SHA256 = "c228525325c1e5d708d621ba086fb49a732cf864dbd2bba98040fcfbbf5e5ccc"
SPECIAL_LAYOUTS = {".m3u", ".m3u8", ".cue", ".ccd", ".mds", ".mra", ".mgl"}


def configured_systems(consolemode_root):
    """Read the frontend's game folders and extension patterns, including aliases."""
    definitions, _ = checked_path(Path(consolemode_root) / "themeconfig/section_groups")
    systems = defaultdict(set)
    for path in sorted(definitions.glob("*.ini")):
        if path.name.startswith("."):
            continue
        path, _ = checked_path(path)
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ValueError("Console Mode definition exceeds 1 MiB: %s" % path)
        config = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            config.read_string(data.decode("utf-8-sig"))
        except (UnicodeError, configparser.Error) as error:
            raise ValueError("Cannot read Console Mode definition %s: %s" % (path, error)) from error
        for section in config.values():
            extensions = {value.strip().lower() for value in section.get("romExts", "").split(",")
                          if value.strip().startswith(".") and not any(c in value for c in "/\\")}
            for value in section.get("romDirs", "").split(","):
                folder = Path(value.strip())
                # MiSTer mirrors games/<system> from the SD card onto USB drives.
                if folder.is_absolute() and ".." not in folder.parts and folder.parent.name == "games":
                    systems[key(folder.name)].update(extensions)
    return dict(systems)


def layout_problem(rom, extensions=None):
    if rom.suffix.lower() in SPECIAL_LAYOUTS:
        return "Playlist, disc descriptor, or arcade/launch definition is not supported"
    if extensions is not None and not any(fnmatchcase(rom.suffix.lower(), pattern) for pattern in extensions):
        return "File type is not displayed by Console Mode"
    return None


def state(path, directory=None):
    info = os.stat(path, dir_fd=directory, follow_symlinks=False)
    return signature(info)


def signature(info):
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Expected a regular file")
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def candidates(directory, stem):
    wanted = key(stem)
    found = []
    for name in os.listdir(directory):
        base, _, extension = name.rpartition(".")
        if name.startswith("."):
            path = Path(name)
            base, extension = path.stem, path.suffix[1:]
        if base and "." + extension.lower() in EXTENSIONS and key(base) == wanted:
            found.append(name)
    return sorted(found)


@contextmanager
def directory(path):
    """Pin directories without following symlinks, including intermediate ones."""
    path = Path(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if part in {".", ".."}:
                raise ValueError("Unsafe directory component")
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def prepare(report, consolemode_root, experimental=False):
    """Read-only preflight shared by scan and apply. Never accept a saved report."""
    if report.get("read_only"):
        raise ValueError("Read-only health report cannot be deployed" if "health" in report else
                         "Arcade preview cannot be deployed; use apply with --arcade-layout")
    root = Path(report["roots"]["rom"])
    from corrections import read as read_corrections
    if read_corrections(root)[1] != report.get("corrections"):
        raise ValueError("Game corrections changed since scan; preview again")
    output = Path(report["roots"]["output"])
    arcade = "arcade" in report
    expected_output = (root.parent if arcade else root) / "media"
    if output != expected_output or not output.is_dir():
        raise ValueError("Deployment requires an existing media output %s --rom-root" % ("beside" if arcade else "under"))
    if experimental and not report.get("selection"):
        raise ValueError("Experimental layouts require explicit --rom selections")
    cm, _ = checked_path(consolemode_root)
    binary, _ = checked_path(cm / "ConsoleMode_arm")
    if digest(binary).hex() != FRONTEND_SHA256:
        raise ValueError("Unsupported Console Mode binary; this writer was verified on 1.1.3")
    extensions = None if experimental or arcade else configured_systems(cm).get(key(root.name), set())
    for row in report["rows"]:
        rom = Path(row["rom"])
        if not rom.is_relative_to(root):
            raise ValueError("ROM path escapes library: %s" % rom)
        problem = (None if str(rom) in report["arcade"]["mras"] and rom.suffix.lower() == ".mra"
                   else "Choose a discovered MRA outside excluded folders") if arcade else layout_problem(rom, extensions)
        if problem:
            raise ValueError("%s: %s; use an explicit --rom device test for unconfigured file types" % (problem, rom))
    cache_map, _ = checked_path(cm / "caches" / "gamelist_art.tsv", missing=True)
    if cache_map.exists():
        raise ValueError("Imported gamelist artwork mappings are not supported by deployment")
    if report["diagnostics"] or any(r["operation"] in {"conflict", "ambiguous", "unsupported", "error"} for r in report["rows"]):
        raise ValueError("Resolve scan diagnostics and conflicts before applying artwork")
    cache, _ = checked_path(output / "optimized", missing=True)
    if cache.exists() and not cache.is_dir():
        raise ValueError("optimized must be a directory")
    protected = {tuple(state(p)[:2]) for p in report["protected_inputs"]}
    cache_names = defaultdict(list)
    if cache.exists():
        for name in os.listdir(cache):
            if Path(name).suffix.lower() in EXTENSIONS:
                cache_names[key(Path(name).stem)].append(name)
    for row in report["rows"]:
        row["cache_candidates"] = []
        if row["operation"] not in {"copy", "replace"}:
            continue
        dest = Path(row["destination"])
        if image_check(Path(row["source"])) != dest.suffix:
            raise ValueError("Source format changed since scan")
        row["source_state"] = state(row["source"])
        row["existing_states"] = {Path(p).name: state(p) for p in row["existing_candidates"]}
        for name in row["existing_states"]:
            if Path(name).stem != dest.stem or (key(name) == key(dest.name) and name != dest.name):
                raise ValueError(f"Case/Unicode output alias requires review: {name}")
        row["cache_states"] = {name: state(cache / name) for name in cache_names[key(dest.stem)]}
        for name, info in row["cache_states"].items():
            if tuple(info[:2]) in protected:
                raise ValueError(f"Cache candidate aliases a protected input: {cache / name}")
            if Path(name).stem != dest.stem:
                raise ValueError(f"Case/Unicode cache alias requires review: {cache / name}")
        row["cache_candidates"] = [str(cache / name) for name in row["cache_states"]]
        row["cache_action"] = "invalidate after verified raw copy" if row["cache_states"] else "none"
    report["compatibility"] = f"Console Mode 1.1.3; {root.name} file-stem artwork in " + ("media beside the Arcade root" if arcade else "system media") + ("; experimental device test" if experimental else "")
    report["cache_action"] = "Only changed artwork's same-stem optimized PNG/JPEG files; restart Console Mode afterward"


def publish(fd, stage, name, replace):
    if replace:
        os.replace(stage, name, src_dir_fd=fd, dst_dir_fd=fd)
        return
    # Prefer no-clobber rename; some filesystems do not support its flags.
    libc = ctypes.CDLL(None, use_errno=True)
    function_name, flag = ("renameatx_np", 4) if sys.platform == "darwin" else ("renameat2", 1)
    function = getattr(libc, function_name, None)
    if function is not None:
        function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        function.restype = ctypes.c_int
        if not function(fd, os.fsencode(stage), fd, os.fsencode(name), flag):
            return
        error = ctypes.get_errno()
        unsupported = {errno.ENOTSUP, errno.EOPNOTSUPP, errno.ENOSYS}
        if sys.platform == "linux":
            unsupported.add(errno.EINVAL)  # Linux filesystems can reject RENAME_NOREPLACE.
        if error not in unsupported:
            raise OSError(error, os.strerror(error), name)
    # Exclusive creation prevents clobbering another file.
    # Unlike rename, a crash during this copy can leave a partial new output.
    source_fd = os.open(stage, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(source_fd, "rb") as source:
        signature(os.fstat(source.fileno()))
        target_fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
        with os.fdopen(target_fd, "w+b") as target:
            identity = signature(os.fstat(target.fileno()))[:2]
            try:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
                if stream_digest(source) != stream_digest(target):
                    raise ValueError("Published copy verification failed")
            except BaseException:
                try:
                    if state(name, fd)[:2] == identity:
                        os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass
                raise
    os.unlink(stage, dir_fd=fd)


def check_candidates(fd, stem, expected):
    actual = {name: state(name, fd) for name in candidates(fd, stem)}
    if actual != expected:
        raise ValueError(f"Artwork changed since preflight: {stem}")


def stream_digest(stream):
    stream.seek(0)
    result = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(chunk)
    return result.digest()


def apply_row(row, output_fd):
    dest = Path(row["destination"])
    source = Path(row["source"])
    stage = ".mister-mediaprep-" + uuid.uuid4().hex + ".tmp"
    row["result"] = "error"
    row["removed_raw"] = []
    row["removed_cache"] = []
    cache_fd = None
    stage_created = False
    try:
        try:
            cache_fd = os.open("optimized", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=output_fd)
        except FileNotFoundError:
            if row["cache_states"]:
                raise ValueError("Optimized directory disappeared since preflight")
        if cache_fd is not None:
            check_candidates(cache_fd, dest.stem, row["cache_states"])
        check_candidates(output_fd, dest.stem, row["existing_states"])
        with directory(source.parent) as source_dir:
            src_fd = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=source_dir)
            with os.fdopen(src_fd, "rb") as src:
                if signature(os.fstat(src.fileno())) != row["source_state"] or state(source.name, source_dir) != row["source_state"]:
                    raise ValueError("Source changed since preflight")
                wanted = stream_digest(src)
                if state(source.name, source_dir) != row["source_state"] or signature(os.fstat(src.fileno())) != row["source_state"]:
                    raise ValueError("Source changed while hashing")
                identical = False
                if dest.name in row["existing_states"]:
                    old_fd = os.open(dest.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=output_fd)
                    with os.fdopen(old_fd, "rb") as old:
                        if signature(os.fstat(old.fileno())) != row["existing_states"][dest.name]:
                            raise ValueError("Destination changed while checking content")
                        identical = stream_digest(old) == wanted
                if row.get("resume_cleanup") and not identical:
                    raise ValueError("Resumed fill-missing artwork changed; cleanup stopped without overwriting it")
                row["raw_action"] = "unchanged" if identical else "copied"
                if not identical:
                    src.seek(0)
                    stage_fd = os.open(stage, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=output_fd)
                    stage_created = True
                    with os.fdopen(stage_fd, "w+b") as target:
                        shutil.copyfileobj(src, target)
                        target.flush()
                        os.fsync(target.fileno())
                        if (stream_digest(target) != wanted or state(source.name, source_dir) != row["source_state"]
                                or signature(os.fstat(src.fileno())) != row["source_state"]):
                            raise ValueError("Staged copy verification failed or source changed")
        check_candidates(output_fd, dest.stem, row["existing_states"])
        if not identical:
            publish(output_fd, stage, dest.name, dest.name in row["existing_states"])
            stage_created = False
        row["result"] = "raw_applied_cleanup_failed"
        if not identical:
            fd = os.open(dest.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=output_fd)
            with os.fdopen(fd, "rb") as copied:
                signature(os.fstat(copied.fileno()))
                if stream_digest(copied) != wanted:
                    raise ValueError("Published output verification failed; cleanup stopped")
        for name, expected in row["existing_states"].items():
            if name != dest.name:
                if state(name, output_fd) != expected:
                    raise ValueError("Superseded raw file changed; cleanup stopped")
                os.unlink(name, dir_fd=output_fd)
                row["removed_raw"].append(name)
        row["result"] = "raw_applied_cache_failed"
        if cache_fd is not None:
            check_candidates(cache_fd, dest.stem, row["cache_states"])
            for name, expected in row["cache_states"].items():
                if state(name, cache_fd) != expected:
                    raise ValueError("Cache file changed; invalidation stopped")
                os.unlink(name, dir_fd=cache_fd)
                row["removed_cache"].append(name)
        row["result"] = "unchanged" if identical and not row["removed_raw"] and not row["removed_cache"] else "applied"
        row["reason"] = ("Raw already byte-identical" if identical else "Raw copy verified") + "; superseded raw extensions and affected cache files removed"
        row["destination_state"] = state(dest)
    except (OSError, ValueError) as exc:
        row["reason"] = str(exc)
    finally:
        if cache_fd is not None:
            os.close(cache_fd)
        if stage_created:
            try:
                os.unlink(stage, dir_fd=output_fd)
            except OSError as exc:
                row["reason"] += f"; temporary-file cleanup failed ({stage}): {exc}"


def apply(report, progress=None, record=None):
    # ponytail: per-file commits, not a whole-library transaction; stop at first
    # failure and retain exact partial results rather than attempting global rollback.
    if report.get("read_only"):
        raise ValueError("Arcade preview cannot be deployed; use apply with --arcade-layout")
    from corrections import read as read_corrections
    if read_corrections(report["roots"]["rom"])[1] != report.get("corrections"):
        raise ValueError("Game corrections changed since preflight; preview again")
    completed = set()
    failed = False
    with directory(report["roots"]["output"]) as output_fd:
        for index, row in enumerate(report["rows"], 1):
            if row["operation"] not in {"copy", "replace"}:
                row["result"] = "preserved" if row["operation"] == "preserve" else "skipped"
            elif failed:
                row["result"] = "not_applied"
            elif row["destination"] in completed:
                row["result"] = "shared"
            else:
                apply_row(row, output_fd)
                failed = row["result"] not in {"applied", "unchanged"}
                if not failed:
                    completed.add(row["destination"])
            if record:
                record(row)
            if progress:
                progress(f"Apply {index}/{len(report['rows'])}: {row['result']} {Path(row['rom']).name} [{row['role']}]")
    report["copied_files"] = len({row["destination"] for row in report["rows"] if row.get("raw_action") == "copied" and row["result"] == "applied"})
    report["unchanged_files"] = len({row["destination"] for row in report["rows"] if row.get("raw_action") == "unchanged" and row["result"] in {"applied", "unchanged"}})
    report["applied_files"] = len(completed)
    report["apply_failed"] = failed
