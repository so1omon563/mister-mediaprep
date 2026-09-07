from argparse import Namespace
from contextlib import redirect_stdout, redirect_stderr
import hashlib
import errno
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import deployment as d
from mister_mediaprep import main, scan


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.root = self.base / "PSX"
        self.media = self.root / "media"
        self.cache = self.media / "optimized"
        self.cache.mkdir(parents=True)
        self.source = self.media / "box2d" / "Game.png"
        self.source.parent.mkdir()
        self.source.write_bytes(b"\x89PNG\r\n\x1a\nnew artwork")
        (self.root / "Game.chd").write_bytes(b"rom")
        (self.root / "gamelist.xml").write_text('<gameList><game><path>Game.chd</path><boxart2d>media/box2d/Game.png</boxart2d></game></gameList>')
        self.cm = self.base / "ConsoleMode"
        self.cm.mkdir()
        (self.cm / "ConsoleMode_arm").write_bytes(b"fixture frontend")
        self.definitions = self.cm / "themeconfig/section_groups/Console.ini"
        self.definitions.parent.mkdir(parents=True)
        self.definitions.write_text("[PlayStation]\nromDirs=/media/fat/games/PSX/\nromExts=.chd,.zip\n")
        self.frontend_patch = patch.object(d, "FRONTEND_SHA256", hashlib.sha256(b"fixture frontend").hexdigest())
        self.frontend_patch.start()
        self.addCleanup(self.frontend_patch.stop)

    def report(self, replace=True):
        r = scan(Namespace(gamelist=str(self.root / "gamelist.xml"), rom_root=str(self.root), output=str(self.media),
                           map_root=[], box_dir=None, background_dir=None, rom=[], replace=replace))
        d.prepare(r, self.cm)
        return r

    def old_files(self):
        (self.media / "Game.jpg").write_bytes(b"old raw")
        (self.cache / "Game.jpg").write_bytes(b"old cache")
        (self.cache / "Unrelated.jpg").write_bytes(b"unrelated")

    def box(self, report):
        return next(row for row in report["rows"] if row["role"] == "box")

    def test_replace_then_only_affected_cache_removed(self):
        self.old_files()
        r = self.report()
        d.apply(r)
        self.assertEqual(r["applied_files"], 1)
        self.assertFalse(r["apply_failed"])
        self.assertEqual((self.media / "Game.png").read_bytes(), self.source.read_bytes())
        self.assertFalse((self.media / "Game.jpg").exists())
        self.assertFalse((self.cache / "Game.jpg").exists())
        self.assertEqual((self.cache / "Unrelated.jpg").read_bytes(), b"unrelated")
        self.assertEqual((self.root / "Game.chd").read_bytes(), b"rom")
        self.assertEqual(self.box(r)["result"], "applied")

    def test_candidate_names_keep_path_suffix_and_unicode_matching(self):
        names = ["Game.png", "GAME.JPG", "Game.jpeg", "Game.png.tmp", "Game.",
                 "Game", ".png", "..png", "...jpg", ".Game.PNG", "Caf\u00e9.png", "Cafe\u0301.jpg"]
        with patch("deployment.os.listdir", return_value=names):
            for stem in ("Game", "game", ".", "..", ".Game", "Caf\u00e9", "missing"):
                expected = sorted(name for name in names if Path(name).suffix.lower() in d.EXTENSIONS
                                  and d.key(Path(name).stem) == d.key(stem))
                self.assertEqual(d.candidates(42, stem), expected)

    def test_copy_and_publish_failure_keep_existing_art(self):
        self.old_files()
        for target in ("shutil.copyfileobj", "publish"):
            with self.subTest(target=target), patch("deployment." + target, side_effect=OSError("injected failure")):
                r = self.report()
                d.apply(r)
                self.assertTrue(r["apply_failed"])
                self.assertEqual(self.box(r)["result"], "error")
                self.assertEqual((self.media / "Game.jpg").read_bytes(), b"old raw")
                self.assertEqual((self.cache / "Game.jpg").read_bytes(), b"old cache")
                self.assertFalse((self.media / "Game.png").exists())
                self.assertFalse(list(self.media.glob(".mister-mediaprep-*.tmp")))

    def test_first_failure_stops_remaining_writes(self):
        xml = self.root / "gamelist.xml"
        xml.write_text(xml.read_text().replace("</game>", "<screenshot>media/box2d/Game.png</screenshot></game>"))
        r = self.report()
        with patch("deployment.shutil.copyfileobj", side_effect=OSError("disk full")):
            d.apply(r)
        self.assertEqual([row["result"] for row in r["rows"]], ["error", "not_applied"])
        self.assertEqual(r["applied_files"], 0)
        self.assertFalse((self.media / "Game.png").exists())
        self.assertFalse((self.media / "Game-BG.png").exists())

    def test_deployment_preflight_is_read_only(self):
        self.old_files()
        def snapshot():
            return {str(p): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
                    for p in [self.base, *self.base.rglob("*")]}
        before = snapshot()
        self.report()
        self.assertEqual(before, snapshot())

    def test_same_extension_replacement_failure_keeps_old(self):
        (self.media / "Game.png").write_bytes(b"old PNG")
        r = self.report()
        with patch("deployment.os.replace", side_effect=OSError("publish failed")):
            d.apply(r)
        self.assertEqual((self.media / "Game.png").read_bytes(), b"old PNG")
        self.assertTrue(r["apply_failed"])

    def test_corrupt_stage_is_not_published(self):
        self.old_files()
        r = self.report()
        with patch("deployment.shutil.copyfileobj", side_effect=lambda src, target: target.write(b"corrupt")):
            d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertEqual((self.media / "Game.jpg").read_bytes(), b"old raw")
        self.assertFalse((self.media / "Game.png").exists())

    def test_output_directory_swapped_for_symlink(self):
        r = self.report()
        self.media.rename(self.root / "original-media")
        self.media.symlink_to(self.root / "original-media", target_is_directory=True)
        with self.assertRaises(OSError):
            d.apply(r)
        self.assertFalse((self.media / "Game.png").exists())

    def test_cache_failure_is_distinct_and_retryable(self):
        self.old_files()
        unlink = os.unlink
        def fail_cache(name, **kwargs):
            fd = kwargs.get("dir_fd")
            if fd is not None and os.fstat(fd).st_ino == self.cache.stat().st_ino:
                raise PermissionError("cache busy")
            return unlink(name, **kwargs)
        r = self.report()
        with patch("deployment.os.unlink", side_effect=fail_cache):
            d.apply(r)
        self.assertEqual(self.box(r)["result"], "raw_applied_cache_failed")
        self.assertEqual((self.media / "Game.png").read_bytes(), self.source.read_bytes())
        self.assertTrue((self.cache / "Game.jpg").exists())
        r = self.report()
        d.apply(r)
        self.assertFalse(r["apply_failed"])
        self.assertFalse((self.cache / "Game.jpg").exists())

    def test_raw_alias_cleanup_failure_can_be_retried(self):
        self.old_files()
        unlink = os.unlink
        def fail_raw(name, **kwargs):
            fd = kwargs.get("dir_fd")
            if name == "Game.jpg" and fd is not None and os.fstat(fd).st_ino == self.media.stat().st_ino:
                raise PermissionError("raw alias busy")
            return unlink(name, **kwargs)
        r = self.report()
        with patch("deployment.os.unlink", side_effect=fail_raw):
            d.apply(r)
        self.assertEqual(self.box(r)["result"], "raw_applied_cleanup_failed")
        self.assertTrue((self.media / "Game.png").exists())
        self.assertTrue((self.media / "Game.jpg").exists())
        r = self.report()
        d.apply(r)
        self.assertFalse(r["apply_failed"])
        self.assertFalse((self.media / "Game.jpg").exists())

    def test_fill_missing_idempotent_and_preserves_cache(self):
        r = self.report(replace=False)
        d.apply(r)
        (self.cache / "Game.png").write_bytes(b"generated")
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()}
        r = self.report(replace=False)
        d.apply(r)
        self.assertEqual(r["applied_files"], 0)
        self.assertEqual(self.box(r)["result"], "preserved")
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.root.rglob("*") if p.is_file()})

    def test_fill_missing_resume_finishes_its_failed_cache_cleanup_without_overwriting(self):
        cache = self.cache / "Game.png"
        cache.write_bytes(b"stale cache without raw artwork")
        journal = self.base / "fill-missing.jsonl"
        args = ["apply", "--gamelist", str(self.root / "gamelist.xml"), "--rom-root", str(self.root),
                "--output", str(self.media), "--consolemode-root", str(self.cm), "--quiet", "--format", "json"]
        original = os.unlink
        def fail_cache(path, *positional, **kwargs):
            if str(path) == "Game.png" and kwargs.get("dir_fd") is not None:
                raise OSError("injected cache cleanup failure")
            return original(path, *positional, **kwargs)
        with patch("deployment.os.unlink", side_effect=fail_cache), redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--journal", str(journal)]), 1)
        raw = self.media / "Game.png"
        self.assertEqual(raw.read_bytes(), self.source.read_bytes())
        raw.write_bytes(b"unexpected different artwork")
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(main(args + ["--resume", str(journal)]), 2)
        self.assertEqual(raw.read_bytes(), b"unexpected different artwork")
        self.assertTrue(cache.exists())
        raw.write_bytes(self.source.read_bytes())
        original_prepare = d.prepare
        def change_after_recovery_check(report, *positional, **kwargs):
            raw.write_bytes(b"changed before preflight")
            return original_prepare(report, *positional, **kwargs)
        with patch("deployment.prepare", side_effect=change_after_recovery_check), redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--resume", str(journal)]), 1)
        self.assertEqual(raw.read_bytes(), b"changed before preflight")
        self.assertTrue(cache.exists())
        raw.write_bytes(self.source.read_bytes())
        before = raw.stat().st_mtime_ns
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--resume", str(journal)]), 0)
        self.assertFalse(cache.exists())
        self.assertEqual(raw.stat().st_mtime_ns, before)

    def test_source_changes_stop_before_writes(self):
        self.old_files()
        r = self.report()
        self.source.write_bytes(b"changed")
        d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertEqual((self.media / "Game.jpg").read_bytes(), b"old raw")
        self.assertEqual((self.cache / "Game.jpg").read_bytes(), b"old cache")

    def test_new_destination_not_clobbered_even_at_publish(self):
        r = self.report(replace=False)
        original = d.publish
        def race(fd, stage, name, replace):
            (self.media / "Game.png").write_bytes(b"arrived during copy")
            original(fd, stage, name, replace)
        with patch("deployment.publish", side_effect=race):
            d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertEqual((self.media / "Game.png").read_bytes(), b"arrived during copy")

    def test_new_alternate_extension_stops_copy(self):
        r = self.report(replace=False)
        (self.media / "Game.jpg").write_bytes(b"arrived")
        d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertFalse((self.media / "Game.png").exists())

    def test_unsupported_rename_uses_verified_exclusive_copy(self):
        self.old_files()
        r = self.report()
        with patch("deployment.ctypes.CDLL") as libc, patch("deployment.ctypes.get_errno", return_value=errno.ENOTSUP):
            libc.return_value.renameatx_np.return_value = -1
            libc.return_value.renameat2.return_value = -1
            d.apply(r)
        self.assertFalse(r["apply_failed"])
        self.assertEqual((self.media / "Game.png").read_bytes(), self.source.read_bytes())
        self.assertFalse((self.media / "Game.jpg").exists())
        self.assertFalse((self.cache / "Game.jpg").exists())
        self.assertFalse(list(self.media.glob(".mister-mediaprep-*.tmp")))

    def test_linux_invalid_rename_flag_uses_exclusive_copy(self):
        with d.directory(self.media) as fd:
            stage = self.media / "stage.tmp"
            stage.write_bytes(b"staged")
            with patch("deployment.sys.platform", "linux"), patch("deployment.ctypes.CDLL") as libc, patch("deployment.ctypes.get_errno", return_value=errno.EINVAL):
                libc.return_value.renameat2.return_value = -1
                d.publish(fd, stage.name, "New.png", False)
                stage.write_bytes(b"replacement")
                with self.assertRaises(FileExistsError):
                    d.publish(fd, stage.name, "New.png", False)
            self.assertEqual((self.media / "New.png").read_bytes(), b"staged")
            self.assertEqual(stage.read_bytes(), b"replacement")

    def test_fallback_copy_failure_keeps_old_art_and_removes_partial_output(self):
        self.old_files()
        r = self.report()
        copy = d.shutil.copyfileobj
        def fail_publication(source, target):
            fail_publication.calls += 1
            if fail_publication.calls == 2:
                target.write(b"partial")
                raise OSError("share disconnected")
            copy(source, target)
        fail_publication.calls = 0
        with patch("deployment.ctypes.CDLL", return_value=object()), patch("deployment.shutil.copyfileobj", side_effect=fail_publication):
            d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertFalse((self.media / "Game.png").exists())
        self.assertEqual((self.media / "Game.jpg").read_bytes(), b"old raw")
        self.assertEqual((self.cache / "Game.jpg").read_bytes(), b"old cache")

    def test_fallback_does_not_clobber_new_file_or_hide_other_errors(self):
        with d.directory(self.media) as fd:
            stage = self.media / "stage.tmp"
            stage.write_bytes(b"staged")
            (self.media / "Game.png").write_bytes(b"another writer")
            with patch("deployment.ctypes.CDLL", return_value=object()), self.assertRaises(FileExistsError):
                d.publish(fd, stage.name, "Game.png", False)
            self.assertEqual((self.media / "Game.png").read_bytes(), b"another writer")
            with patch("deployment.ctypes.CDLL") as libc, patch("deployment.ctypes.get_errno", return_value=errno.EACCES):
                libc.return_value.renameatx_np.return_value = -1
                libc.return_value.renameat2.return_value = -1
                with self.assertRaises(PermissionError):
                    d.publish(fd, stage.name, "New.png", False)
            self.assertFalse((self.media / "New.png").exists())
            self.assertEqual(stage.read_bytes(), b"staged")

    def test_cache_symlink_and_protected_input(self):
        (self.cache / "Game.jpg").symlink_to(self.source)
        with self.assertRaises(ValueError):
            self.report()
        (self.cache / "Game.jpg").unlink()
        os.link(self.source, self.cache / "Game.jpg")
        with self.assertRaisesRegex(ValueError, "protected input"):
            self.report()

    def test_cache_directory_symlink_after_preflight(self):
        r = self.report()
        self.cache.rmdir()
        self.cache.symlink_to(self.source.parent, target_is_directory=True)
        d.apply(r)
        self.assertTrue(r["apply_failed"])
        self.assertFalse((self.media / "Game.png").exists())

    def test_version_imported_map_and_layout_gates(self):
        with patch.object(d, "FRONTEND_SHA256", "unverified"):
            with self.assertRaisesRegex(ValueError, "Unsupported Console Mode"):
                self.report()
        (self.cm / "caches").mkdir()
        (self.cm / "caches/gamelist_art.tsv").write_text("mapped")
        with self.assertRaisesRegex(ValueError, "Imported gamelist"):
            self.report()
        (self.cm / "caches/gamelist_art.tsv").unlink()
        (self.root / "nested").mkdir()
        (self.root / "Game.chd").rename(self.root / "nested/Game.chd")
        p = self.root / "gamelist.xml"
        p.write_text(p.read_text().replace("<path>Game.chd", "<path>nested/Game.chd"))
        self.old_files()
        r = self.report()
        d.apply(r)
        self.assertFalse(r["apply_failed"])
        self.assertEqual((self.media / "Game.png").read_bytes(), self.source.read_bytes())
        self.assertEqual((self.root / "nested/Game.chd").read_bytes(), b"rom")
        self.assertFalse((self.root / "nested/media").exists())
        for extension in ("m3u", "cue", "mra", "mgl"):
            with self.subTest(extension=extension):
                (self.root / ("Other." + extension)).write_bytes(b"unsupported entry")
                p.write_text('<gameList><game><path>Other.' + extension + '</path></game></gameList>')
                with self.assertRaisesRegex(ValueError, "not supported"):
                    self.report()

    def test_cli_reports_planned_and_actual_results(self):
        stdout = StringIO()
        with redirect_stdout(stdout), redirect_stderr(StringIO()):
            code = main(["apply", "--gamelist", str(self.root / "gamelist.xml"), "--rom-root", str(self.root),
                         "--output", str(self.media), "--consolemode-root", str(self.cm), "--replace", "--format", "json"])
        self.assertEqual(code, 0)
        r = json.loads(stdout.getvalue())
        self.assertEqual(r["summary"]["copy"], 1)
        self.assertEqual(r["applied_files"], 1)
        self.assertEqual(self.box(r)["result"], "applied")

    def test_identical_raw_is_reused_but_stale_cache_is_cleaned(self):
        raw = self.media / "Game.png"
        raw.write_bytes(self.source.read_bytes())
        (self.cache / "Game.png").write_bytes(b"possibly stale")
        before = raw.stat().st_mtime_ns
        for expected_result in ("applied", "unchanged"):
            r = self.report()
            with patch("deployment.shutil.copyfileobj", side_effect=AssertionError("unnecessary copy")):
                d.apply(r)
            self.assertFalse(r["apply_failed"])
            self.assertEqual(self.box(r)["result"], expected_result)
            self.assertEqual(r["copied_files"], 0)
            self.assertEqual(r["unchanged_files"], 1)
            self.assertEqual(raw.stat().st_mtime_ns, before)
            self.assertFalse((self.cache / "Game.png").exists())

    def test_selected_cartridge_and_zip_experiments_use_file_stems(self):
        from mister_mediaprep import scan
        for system, extension in (("NES", ".nes"), ("SNES", ".sfc"), ("MegaDrive", ".md"),
                                  ("NEOGEO", ".zip"), ("MegaCD", ".chd")):
            with self.subTest(system=system):
                root = self.base / system
                (root / "media").mkdir(parents=True)
                (root / ("Game" + extension)).write_bytes(b"opaque ROM, not opened by artwork writer")
                (root / "media/source.png").write_bytes(self.source.read_bytes())
                xml = root / "gamelist.xml"
                xml.write_text('<gameList><game><path>Game' + extension + '</path><boxart2d>media/source.png</boxart2d></game></gameList>')
                with self.definitions.open("a") as stream:
                    stream.write("[%s]\nromDirs=/media/fat/games/%s/\nromExts=%s\n" % (system, system, extension))
                args = Namespace(gamelist=str(xml), rom_root=str(root), output=str(root / "media"),
                                 map_root=[], box_dir=None, background_dir=None, rom=["Game" + extension], replace=True)
                r = scan(args)
                with patch("deployment.configured_systems", return_value={}):
                    with self.assertRaisesRegex(ValueError, "not displayed"):
                        d.prepare(r, self.cm)
                d.prepare(r, self.cm)
                d.prepare(r, self.cm, experimental=True)
                d.apply(r)
                self.assertFalse(r["apply_failed"])
                self.assertEqual((root / "media/Game.png").read_bytes(), self.source.read_bytes())
                r["selection"] = []
                with self.assertRaisesRegex(ValueError, "explicit --rom"):
                    d.prepare(r, self.cm, experimental=True)

    def test_verified_fds_smc_and_saturn_formats(self):
        from mister_mediaprep import scan
        for system, extension in (("NES", ".fds"), ("SNES", ".smc"), ("Saturn", ".chd")):
            with self.subTest(system=system):
                root = self.base / system
                (root / "media").mkdir(parents=True)
                (root / ("Game" + extension)).write_bytes(b"opaque ROM, not opened by artwork writer")
                (root / "media/source.png").write_bytes(self.source.read_bytes())
                xml = root / "gamelist.xml"
                xml.write_text('<gameList><game><path>Game' + extension + '</path><boxart2d>media/source.png</boxart2d></game></gameList>')
                with self.definitions.open("a") as stream:
                    stream.write("[%s]\nromDirs=/media/fat/games/%s/\nromExts=%s\n" % (system, system, extension))
                args = Namespace(gamelist=str(xml), rom_root=str(root), output=str(root / "media"),
                                 map_root=[], box_dir=None, background_dir=None, rom=["Game" + extension], replace=True)
                r = scan(args)
                with patch("deployment.configured_systems", return_value={}):
                    with self.assertRaisesRegex(ValueError, "not displayed"):
                        d.prepare(r, self.cm)
                d.prepare(r, self.cm)
                d.prepare(r, self.cm, experimental=True)
                d.apply(r)
                self.assertFalse(r["apply_failed"])
                self.assertEqual((root / "media/Game.png").read_bytes(), self.source.read_bytes())
                r["selection"] = []
                with self.assertRaisesRegex(ValueError, "explicit --rom"):
                    d.prepare(r, self.cm, experimental=True)

    def test_journal_resumes_only_unfinished_games_and_handles_partial_line(self):
        (self.root / "Other.chd").write_bytes(b"other ROM")
        xml = self.root / "gamelist.xml"
        xml.write_text(xml.read_text().replace('</gameList>', '<game><path>Other.chd</path><boxart2d>media/box2d/Game.png</boxart2d></game></gameList>'))
        journal = self.base / "progress.jsonl"
        args = ["apply", "--gamelist", str(xml), "--rom-root", str(self.root), "--output", str(self.media),
                "--consolemode-root", str(self.cm), "--replace", "--quiet", "--format", "json"]
        original = d.publish
        def fail_second(fd, stage, name, replace):
            if name == "Other.png":
                raise OSError("injected disconnect")
            original(fd, stage, name, replace)
        with patch("deployment.publish", side_effect=fail_second), redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--journal", str(journal)]), 1)
        (self.cache / "Game.png").write_bytes(b"completed game's cache")
        before = (self.media / "Game.png").stat().st_mtime_ns
        with journal.open("ab") as stream:
            stream.write(b'{"interrupted')
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(args + ["--resume", str(journal)]), 0)
        r = json.loads(output.getvalue())
        self.assertEqual({Path(row["rom"]).name for row in r["rows"]}, {"Other.chd"})
        self.assertEqual((self.media / "Game.png").stat().st_mtime_ns, before)
        self.assertTrue((self.cache / "Game.png").exists())
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--resume", str(journal)]), 0)
        self.source.write_bytes(self.source.read_bytes() + b"updated")
        output = StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(args + ["--resume", str(journal)]), 0)
        self.assertEqual(json.loads(output.getvalue())["copied_files"], 2)

    def test_journal_cannot_overwrite_inputs_or_existing_reports(self):
        args = ["apply", "--gamelist", str(self.root / "gamelist.xml"), "--rom-root", str(self.root),
                "--output", str(self.media), "--consolemode-root", str(self.cm), "--quiet"]
        existing = self.base / "existing.jsonl"
        existing.write_text("keep me")
        for path in (self.source, existing):
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(args + ["--journal", str(path)]), 2)
        self.assertEqual(existing.read_text(), "keep me")
        self.assertFalse((self.media / "Game.png").exists())

    def test_alternate_artwork_apply_and_resume_keep_original_choices(self):
        self.old_files()
        box3d = self.media / "box3d/Artwork.jpg"
        title = self.media / "titlescreen/Title.png"
        box3d.parent.mkdir()
        title.parent.mkdir()
        box3d.write_bytes(b"\xff\xd8\xff\xe0chosen 3D box")
        title.write_bytes(b"\x89PNG\r\n\x1a\nchosen title screen")
        xml = self.root / "gamelist.xml"
        xml.write_text(xml.read_text().replace("</game>",
            '<boxart3d>media/box3d/Artwork.jpg</boxart3d><titlescreen>media/titlescreen/Title.png</titlescreen></game>'))
        protected = [xml, self.root / "Game.chd", self.source, box3d, title, self.cache / "Unrelated.jpg"]
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
        journal = self.base / "choices.jsonl"
        args = ["apply", "--gamelist", str(xml), "--rom-root", str(self.root), "--output", str(self.media),
                "--consolemode-root", str(self.cm), "--replace", "--quiet", "--format", "json"]
        choices = ["--box-source", "boxart3d", "--background-source", "titlescreen"]
        original = d.publish
        def fail_box(fd, stage, name, replace):
            if name == "Game.jpg":
                raise OSError("interrupted before box")
            original(fd, stage, name, replace)
        with patch("deployment.publish", side_effect=fail_box), redirect_stdout(StringIO()):
            self.assertEqual(main(args + choices + ["--journal", str(journal)]), 1)
        self.assertEqual((self.media / "Game-BG.png").read_bytes(), title.read_bytes())
        bg_time = (self.media / "Game-BG.png").stat().st_mtime_ns
        errors = StringIO()
        with redirect_stderr(errors), redirect_stdout(StringIO()):
            self.assertEqual(main(args + ["--resume", str(journal)]), 2)
        self.assertIn("original paths, policy, overrides", errors.getvalue())
        with redirect_stdout(StringIO()):
            self.assertEqual(main(args + choices + ["--resume", str(journal)]), 0)
        self.assertEqual((self.media / "Game.jpg").read_bytes(), box3d.read_bytes())
        self.assertEqual((self.media / "Game-BG.png").stat().st_mtime_ns, bg_time)
        self.assertFalse((self.cache / "Game.jpg").exists())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})


if __name__ == "__main__":
    unittest.main()
