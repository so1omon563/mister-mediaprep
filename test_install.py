import hashlib
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from install import install


class InstallTests(unittest.TestCase):
    def package(self, value):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for name in ("__main__.py", "native_menu.py", "native_library.py"):
                archive.writestr(name, value)
        return stream.getvalue()

    def test_install_and_upgrade_preserve_journals_and_reject_corruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            scripts = Path(temporary).resolve()
            payload = self.package("old")
            install(payload, hashlib.sha256(payload).hexdigest(), scripts)
            app = scripts / ".config/mister-mediaprep"
            journal = app / "run.jsonl"
            journal.write_bytes(b"saved progress")
            updated = self.package("new")
            with self.assertRaisesRegex(ValueError, "checksum"):
                install(updated, hashlib.sha256(payload).hexdigest(), scripts)
            self.assertEqual((app / "mister-mediaprep.pyz").read_bytes(), payload)
            install(updated, hashlib.sha256(updated).hexdigest(), scripts)
            self.assertEqual((app / "mister-mediaprep.pyz").read_bytes(), updated)
            self.assertEqual(journal.read_bytes(), b"saved progress")
            self.assertTrue((scripts / "mister-mediaprep.sh").is_file())
            self.assertFalse(list(app.glob(".mediaprep-install-*")))

    def test_symlink_launcher_blocks_install_before_package_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            scripts = Path(temporary).resolve()
            protected = scripts / "protected"
            protected.write_bytes(b"keep")
            (scripts / "mister-mediaprep.sh").symlink_to(protected)
            payload = self.package("new")
            with self.assertRaisesRegex(ValueError, "Unsafe"):
                install(payload, hashlib.sha256(payload).hexdigest(), scripts)
            self.assertEqual(protected.read_bytes(), b"keep")
            self.assertFalse((scripts / ".config/mister-mediaprep/mister-mediaprep.pyz").exists())


if __name__ == "__main__":
    unittest.main()
