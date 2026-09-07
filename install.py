"""Embedded by build.py in a self-contained MiSTer installer."""
import base64
import hashlib
import io
import os
from pathlib import Path
import sys
import tempfile
import zipfile

LAUNCHER = ("#!/bin/bash\nexport PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1\n"
            'exec python3 -B /media/fat/Scripts/.config/mister-mediaprep/mister-mediaprep.pyz "$@"\n').encode()

def install(payload, expected, scripts):
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError("Installer payload checksum failed")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        if archive.testzip() or not {"__main__.py", "native_menu.py", "native_library.py"}.issubset(archive.namelist()):
            raise ValueError("Invalid MediaPrep package")
    scripts = Path(scripts).absolute()
    if scripts.resolve() != scripts or not scripts.is_dir():
        raise ValueError("Scripts must be an existing directory without symlinks")
    app = scripts / ".config" / "mister-mediaprep"
    for path in (app.parent, app):
        if path.is_symlink():
            raise ValueError("Application directory must not be a symlink")
        path.mkdir(exist_ok=True)
    for destination, data in ((app / "mister-mediaprep.pyz", payload), (scripts / "mister-mediaprep.sh", LAUNCHER)):
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise ValueError("Unsafe installed file: %s" % destination)
    # Publish the complete package first; the old launcher stays usable until replaced.
    for destination, data in ((app / "mister-mediaprep.pyz", payload), (scripts / "mister-mediaprep.sh", LAUNCHER)):
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".mediaprep-install-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.read_bytes() != data:
                raise ValueError("Staged installation verification failed")
            temporary.chmod(0o755)
            os.replace(temporary, destination)
            if destination.read_bytes() != data:
                raise ValueError("Installed file verification failed")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        if sys.platform != "linux" or sys.version_info < (3, 9):
            raise RuntimeError("Run this installer on MiSTer with Python 3.9 or newer")
        encoded = Path(sys.argv[1]).read_bytes().split(b"\n__PAYLOAD__\n", 1)[1]
        payload = base64.b64decode(encoded, validate=False)
        install(payload, "@SHA256@", Path("/media/fat/Scripts"))
        print("MiSTer MediaPrep @VERSION@ installed. Open Scripts > mister-mediaprep.sh.")
    except (OSError, ValueError, RuntimeError, IndexError, zipfile.BadZipFile) as error:
        print("Install failed: %s" % error, file=sys.stderr)
        sys.exit(1)
