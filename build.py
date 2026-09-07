"""Build the Python 3.9+ runtime and optional self-contained MiSTer installer."""
import argparse
import base64
import hashlib
import io
from pathlib import Path
import re
import zipfile
from mister_mediaprep import VERSION
from install import LAUNCHER

MODULES = ("mister_mediaprep.py", "deployment.py", "journal.py", "native_menu.py", "native_library.py", "corrections.py")
PAYLOAD_FILES = (*MODULES, "LICENSE")
ENTRYPOINT = b"from mister_mediaprep import entrypoint\nentrypoint()\n"


def build(output, installer=None):
    source = Path(__file__).resolve().parent
    stream = io.BytesIO()
    stream.write(b"#!/usr/bin/env python3\n")
    # Fixed ZIP metadata and stored entries make bytes independent of mtimes and zlib versions.
    with zipfile.ZipFile(stream, "w") as archive:
        for name in (*PAYLOAD_FILES, "__main__.py"):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, ENTRYPOINT if name == "__main__.py" else (source / name).read_bytes())
    payload = stream.getvalue()
    output.write_bytes(payload)
    output.chmod(0o755)
    if installer:
        script = (source / "install.py").read_text().replace("@SHA256@", hashlib.sha256(payload).hexdigest()).replace("@VERSION@", VERSION)
        installer.write_text('#!/bin/bash\nexport PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1\npython3 - "$0" <<\'PYTHON_INSTALLER\'\n' + script +
                             '\nPYTHON_INSTALLER\nresult=$?\necho "Installer exit: $result"\nsleep 5\nexit "$result"\n__PAYLOAD__\n' + base64.encodebytes(payload).decode())
        installer.chmod(0o755)


def release(directory, tag=None):
    if tag is not None and (not re.fullmatch(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag) or tag != "v" + VERSION):
        raise ValueError("Release tag must match the staged stable VERSION: " + VERSION)
    directory.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parent
    runtime, installer = directory / "mister-mediaprep.pyz", directory / "mister-mediaprep-install.sh"
    build(runtime, installer)
    launcher = directory / "mister-mediaprep.sh"
    launcher.write_bytes((source / "mister-mediaprep.sh").read_bytes())
    launcher.chmod(0o755)
    if launcher.read_bytes() != LAUNCHER:
        raise ValueError("Release and installed launchers differ")
    with zipfile.ZipFile(runtime) as archive:
        if (set(archive.namelist()) != {*PAYLOAD_FILES, "__main__.py"}
                or any(archive.read(name) != (source / name).read_bytes() for name in PAYLOAD_FILES)
                or archive.read("__main__.py") != ENTRYPOINT):
            raise ValueError("Runtime archive differs from source")
    if base64.b64decode(installer.read_bytes().split(b"\n__PAYLOAD__\n", 1)[1]) != runtime.read_bytes():
        raise ValueError("Embedded installer payload differs from runtime")
    (directory / "SHA256SUMS").write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.name + "\n"
                                                for p in (runtime, launcher, installer)))
    return runtime, launcher, installer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path, nargs="?")
    parser.add_argument("--installer", type=Path)
    parser.add_argument("--release-dir", type=Path)
    parser.add_argument("--tag", help="Require a matching stable version for release assets")
    args = parser.parse_args()
    if args.release_dir and not args.output and not args.installer:
        release(args.release_dir, args.tag)
    elif args.output and not args.release_dir and not args.tag:
        build(args.output, args.installer)
    else:
        parser.error("Choose OUTPUT [--installer PATH] or --release-dir DIR [--tag vX.Y.Z]")


if __name__ == "__main__":
    main()
