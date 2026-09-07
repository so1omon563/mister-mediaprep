"""Keep verified game artwork independently of a scraper's XML and image files."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import uuid

from deployment import candidates, directory, publish, signature, state, stream_digest
from mister_mediaprep import ROLES, checked_path, digest, image_check, key, resolve

STORE = ".mediaprep-corrections"
MANIFEST = "corrections.json"


def relative(value):
    if (not isinstance(value, str) or not value or "\x00" in value or "\\" in value
            or PureWindowsPath(value).drive or Path(value).is_absolute()
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError("Corrections require an exact ROM path relative to its library")
    return value


def unique(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate correction key: " + name)
        result[name] = value
    return result


def read(root):
    """Return validated mappings and a content fingerprint; absence creates nothing."""
    folder, _ = checked_path(Path(root) / STORE, missing=True)
    manifest, _ = checked_path(folder / MANIFEST, missing=True)
    if folder.name != STORE or manifest.name != MANIFEST:
        raise ValueError("Correction store spelling differs; use " + STORE + "/" + MANIFEST)
    if not manifest.exists():
        return {}, None
    with manifest.open("rb") as stream:
        data = stream.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError("Corrections exceed the 1 MiB limit")
    document = json.loads(data, object_pairs_hook=unique)
    if (not isinstance(document, dict) or set(document) != {"schema", "games"}
            or type(document["schema"]) is not int or document["schema"] != 1
            or not isinstance(document["games"], dict)):
        raise ValueError("Invalid corrections file")
    images = set()
    for rom, roles in document["games"].items():
        relative(rom)
        if not isinstance(roles, dict) or not roles or not set(roles) <= set(ROLES):
            raise ValueError("Invalid correction roles for " + rom)
        for filename in roles.values():
            if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{64}\.(png|jpg)", filename):
                raise ValueError("Invalid saved correction image")
            images.add(filename)
    # ponytail: hash the small manual correction set on each read; batch/cache only if it grows large.
    for filename in images:
        path, _ = checked_path(folder / filename)
        if (path.name != filename or path.stat().st_nlink != 1 or image_check(path) != path.suffix
                or digest(path).hex() != path.stem):
            raise ValueError("Saved correction image changed: " + str(path))
    return document["games"], hashlib.sha256(data).hexdigest()


def game_path(root, rom):
    path, _ = resolve(relative(rom), root, [root], [], {})
    if path.relative_to(root).as_posix() != rom:
        raise ValueError("Choose the ROM's exact spelling for its correction")
    return path


def current(root, rom, roles):
    """Only unambiguous prepared raw images, never optimized caches or fuzzy titles."""
    root, _ = checked_path(root)
    path = game_path(root, rom)
    result = {}
    with directory(root / "media") as media:
        for role in roles:
            stem = path.stem + ("-BG" if role == "background" else "")
            names = candidates(media, stem)
            if len(names) > 1 or any(Path(name).stem != stem for name in names):
                raise ValueError("Ambiguous current artwork: " + stem)
            if names:
                source, _ = checked_path(root / "media" / names[0])
                image_check(source)
                result[role] = source
    return result


@contextmanager
def staging(fd):
    name = ".pending-" + uuid.uuid4().hex
    handle = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644, dir_fd=fd)
    try:
        with os.fdopen(handle, "w+b") as stream:
            yield name, stream
    finally:
        try:
            os.unlink(name, dir_fd=fd)
        except FileNotFoundError:
            pass


def save(root, rom, roles, *, sources=None):
    """Snapshot current or explicitly chosen images; empty roles remove a correction."""
    root, _ = checked_path(root)
    relative(rom)
    if not set(roles) <= set(ROLES):
        raise ValueError("Choose box and/or background artwork")
    games, before = read(root)
    if sources is None:
        sources = current(root, rom, roles) if roles else {}
    else:
        game_path(root, rom)
        if not isinstance(sources, dict) or not roles or set(sources) != set(roles):
            raise ValueError("Choose one source for every selected correction role")
        chosen = {}
        for role, value in sources.items():
            if not isinstance(value, (str, Path)):
                raise ValueError("Source artwork must be a path inside the game library")
            source, _ = resolve(str(value), root, [root], [], {})
            if tuple(key(part) for part in source.relative_to(root).parts[:2]) == ("media", "optimized"):
                raise ValueError("Choose original artwork, not an optimized cache")
            image_check(source)
            chosen[role] = source
        sources = chosen
    if any(role not in sources for role in roles):
        raise ValueError("No current raw image for a selected correction role")
    if not roles and rom not in games:
        return {}
    with directory(root) as parent:
        try:
            os.mkdir(STORE, dir_fd=parent)
        except FileExistsError:
            pass
    with directory(root / STORE) as folder:
        kept = dict(games.get(rom, {}))
        for role in roles:
            source = sources[role]
            extension = image_check(source)
            with directory(source.parent) as parent:
                handle = os.open(source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                with os.fdopen(handle, "rb") as src, staging(folder) as (stage, dst):
                    original = signature(os.fstat(src.fileno()))
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                    wanted = stream_digest(dst).hex()
                    if (state(source.name, parent) != original or signature(os.fstat(src.fileno())) != original
                            or stream_digest(src).hex() != wanted):
                        raise ValueError("Source artwork changed while saving its correction")
                    filename = wanted + extension
                    existing, _ = checked_path(root / STORE / filename, missing=True)
                    if existing.exists():
                        if existing.stat().st_nlink != 1 or digest(existing).hex() != wanted:
                            raise ValueError("Saved correction image changed: " + str(existing))
                    else:
                        publish(folder, stage, filename, False)
                    if image_check(root / STORE / filename) != extension:
                        raise ValueError("Source artwork format changed while saving")
                    kept[role] = filename
        if roles:
            games[rom] = kept
        else:
            del games[rom]
        payload = (json.dumps({"schema": 1, "games": games}, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if len(payload) > 1024 * 1024:
            raise ValueError("Corrections exceed the 1 MiB limit")
        with staging(folder) as (stage, stream):
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
            if read(root)[1] != before:
                raise ValueError("Corrections changed during this operation; retry")
            publish(folder, stage, MANIFEST, before is not None)
    read(root)  # Verify the published manifest and its image snapshots.
    return games.get(rom, {})
