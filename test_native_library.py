"""The batch must preflight all systems before allowing writes."""
from pathlib import Path
from contextlib import redirect_stdout
from io import StringIO
import json
import hashlib
import tempfile
import unittest
from unittest.mock import patch

import native_library
import native_menu
import deployment
from mister_mediaprep import main as cli, scan
from argparse import Namespace


class NativeLibraryTests(unittest.TestCase):
    def test_frontend_guard_checks_process_names_and_disappearing_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            comm = Path(temporary) / "comm"
            missing = Path(temporary) / "exited"
            with patch("native_library.Path") as paths:
                paths.return_value.glob.return_value = [missing, comm]
                comm.write_text("unrelated\n")
                native_library.require_closed()
                comm.write_text("ConsoleMode_arm\n")
                with self.assertRaisesRegex(RuntimeError, "Close Console Mode"):
                    native_library.require_closed()

    def test_controller_selection_toggles_and_back_cancels(self):
        libraries = [dict(root="NES", roms=["Game.nes"]), dict(root="SNES", roms=["Game.sfc"])]
        with patch("native_menu.dialog", side_effect=["0", "0", "1", "done"]):
            self.assertEqual(native_menu.choose(libraries), [libraries[1]])
        with patch("native_menu.dialog", side_effect=["0", None]):
            self.assertEqual(native_menu.choose(libraries), [])

    def test_dialog_passes_menu_as_arguments_and_handles_back(self):
        with patch("native_menu.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "1\n"
            self.assertEqual(native_menu.dialog("menu", "Choose", [("1", "Preview")]), "1")
            self.assertEqual(run.call_args.args[0][-2:], ["1", "Preview"])
            run.return_value.returncode = 255
            self.assertIsNone(native_menu.dialog("menu", "Choose"))

    def test_artwork_menu_saves_preferences_for_later_previews_and_back_cancels(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary).resolve()
            libraries = [dict(root=str(app / "NES"), roms=["Game.nes"], excluded=0)]
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.discover", return_value=libraries), patch("native_menu.dialog", side_effect=["4", "box", "background", "done", "0"]), patch("native_menu.update") as apply:
                native_menu.main()
                apply.assert_not_called()
            preferences = {"box": "boxart3d", "background": "titlescreen"}
            self.assertEqual(json.loads((app / "artwork.json").read_text()), preferences)
            with patch("native_menu.dialog", side_effect=["box", None]):
                self.assertIsNone(native_menu.choose_artwork(preferences))
            self.assertEqual(preferences["box"], "boxart3d")
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.discover", return_value=libraries), patch("native_menu.dialog", side_effect=["1", "fill-missing", "all", "", "0"]), patch("native_menu.run", return_value=({"summary": {"copy": 2, "replace": 0, "missing": 0}}, 0)) as scan, patch("native_menu.update") as apply, redirect_stdout(StringIO()):
                native_menu.main()
                args = scan.call_args.args[0]
                self.assertEqual(args[args.index("--box-source") + 1], "boxart3d")
                self.assertEqual(args[args.index("--background-source") + 1], "titlescreen")
                self.assertNotIn("--replace", args)
                apply.assert_not_called()
            self.assertFalse(list(app.glob("runs/*/selection.json")))
            with self.assertRaisesRegex(ValueError, "Invalid artwork"):
                native_menu.jobs_for([dict(libraries[0], artwork={"box": [], "background": "titlescreen"})], libraries, app)

    def test_discovery_filters_formats_and_resume_rejects_unavailable_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "NES"
            root.mkdir()
            (root / "media").mkdir()
            (root / "media/source.png").write_bytes(b"\x89PNG\r\n\x1a\nsource")
            (root / "Game.nes").write_bytes(b"ROM")
            (root / "Other.unf").write_bytes(b"ROM")
            (root / "gamelist.xml").write_text('<gameList><game><path>Game.nes</path><boxart2d>media/source.png</boxart2d></game><game><path>Other.unf</path></game></gameList>')
            cm = base / "ConsoleMode"
            definitions = cm / "themeconfig/section_groups/Console.ini"
            definitions.parent.mkdir(parents=True)
            definitions.write_text("[NES]\nromDirs=/media/fat/games/NES/\nromExts=.nes\n")
            libraries = native_menu.discover([base, base], cm)
            self.assertEqual(len(libraries), 1)
            self.assertEqual(libraries[0]["roms"], ["Game.nes"])
            self.assertEqual(libraries[0]["excluded"], 1)
            self.assertIn("Game.nes", native_menu.jobs_for(libraries, libraries, base)[0][1])
            (root / "media/box2d").mkdir()
            (root / "media/box2d/imported.png").write_bytes(b"\x89PNG\r\n\x1a\nimport")
            (root / "gamelist.xml").write_text('<gameList><game><path>Game.nes</path><image>media/box2d/imported.png</image></game></gameList>')
            self.assertEqual(native_menu.discover([base], cm)[0]["roms"], ["Game.nes"])
            with self.assertRaisesRegex(ValueError, "unavailable"):
                native_menu.jobs_for(libraries, [], base)
            libraries[0]["roms"] = ["../escape.nes"]
            with self.assertRaisesRegex(ValueError, "Invalid"):
                native_menu.jobs_for(libraries, libraries, base)

    def test_future_system_uses_frontend_alias_and_wildcard_without_code_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            games = base / "games"
            root = games / "FutureAlias"
            (root / "media/box").mkdir(parents=True)
            (root / "nested").mkdir()
            source = root / "media/box/source.png"
            source.write_bytes(b"\x89PNG\r\n\x1a\nsource artwork")
            rom = root / "nested/Game.ROMX"
            rom.write_bytes(b"future ROM")
            entries = ['<game id="parent"><boxart3d>media/box/source.png</boxart3d></game>',
                       '<game parentid="parent"><path>nested/Game.ROMX</path></game>']
            for name in ("Other.m3u", "Other.cue", "Other.mra", "Other.mgl", "Hidden.bin", "NoArt.romx", "._Game.romx"):
                (root / name).write_bytes(b"input")
                entries.append('<game%s><path>%s</path></game>' %
                               (' parentid="parent"' if name != "NoArt.romx" else '', name))
            (root / "Linked.romx").symlink_to(rom)
            entries += ['<game parentid="parent"><path>Linked.romx</path></game>',
                        '<game parentid="parent"><path>../Escape.romx</path></game>']
            (root / "gamelist.xml").write_text('<gameList>' + ''.join(entries) + '</gameList>')
            (games / "EmptyCore").mkdir()
            (games / "LinkedCore").symlink_to(root, target_is_directory=True)
            cm = base / "ConsoleMode"
            definitions = cm / "themeconfig/section_groups/Future.ini"
            definitions.parent.mkdir(parents=True)
            text = ('\ufeff[Future Console]\nromDirs=/media/fat/games/FutureCore/,/media/fat/games/FutureAlias/,/media/fat/_Computer/Boot.mgl\n'
                    'romExts=.r??x,.zip,.m3u,.cue,.mra,.mgl\n')
            definitions.write_text(text)
            (definitions.parent / "._Future.ini").write_bytes(b"not an INI file")
            (cm / "ConsoleMode_arm").write_bytes(b"frontend")
            before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in root.rglob("*") if p.is_file() and not p.is_symlink()}
            available = native_menu.discover([games, games], cm)
            ready = [item for item in available if item["roms"]]
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0]["roms"], ["nested/Game.ROMX"])
            reasons = '\n'.join(issue['reason'] for item in available for issue in item['issues'])
            for message in ("not supported", "not displayed", "No artwork", "Symlink", "traversal"):
                self.assertIn(message, reasons)
            self.assertEqual(before, {str(p): (p.read_bytes(), p.stat().st_mtime_ns)
                                     for p in root.rglob("*") if p.is_file() and not p.is_symlink()})
            with patch("native_menu.dialog", side_effect=["skipped", "0", "", "all"]):
                self.assertEqual(native_menu.choose(available), ready)
            selection = [dict(root=str(root), roms=["./nested/Game.ROMX"])]
            jobs = native_menu.jobs_for(selection, available, cm)
            with patch.object(deployment, "FRONTEND_SHA256", hashlib.sha256(b"frontend").hexdigest()):
                report = scan(Namespace(gamelist=str(root / "gamelist.xml"), rom_root=str(root), output=str(root / "media"),
                                        map_root=[], box_dir=None, background_dir=None, rom=["nested/Game.ROMX"], replace=True))
                definitions.write_text(text.replace('.r??x', '.different'))
                with self.assertRaisesRegex(ValueError, "not displayed"):
                    deployment.prepare(report, cm)
                definitions.write_text(text)
                with redirect_stdout(StringIO()):
                    self.assertEqual(cli(["apply"] + jobs[0][1] + ["--quiet"]), 0)
            self.assertEqual((root / "media/Game.png").read_bytes(), source.read_bytes())
            self.assertEqual(before, {name: (Path(name).read_bytes(), Path(name).stat().st_mtime_ns) for name in before})
            for extension in deployment.SPECIAL_LAYOUTS:
                with self.subTest(extension=extension):
                    self.assertIsNotNone(deployment.layout_problem(Path('Game' + extension)))

    def test_menu_preview_never_applies_or_creates_resume_selection(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary).resolve()
            libraries = [dict(root=str(app / "NES"), roms=["Game.nes"], excluded=0)]
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.discover", return_value=libraries), patch("native_menu.dialog", side_effect=["1", "replace", "0", "done", "", "0"]), patch("native_menu.run", return_value=({"summary": {"copy": 2, "replace": 0, "missing": 0}}, 0)) as scan, patch("native_menu.update") as apply, redirect_stdout(StringIO()):
                native_menu.main()
            self.assertEqual(scan.call_args.args[0][0], "scan")
            self.assertIn("--replace", scan.call_args.args[0])
            apply.assert_not_called()
            self.assertFalse(list(app.glob("runs/*/selection.json")))

    def test_menu_resumes_original_selection_after_apply_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary).resolve()
            libraries = [dict(root=str(app / "NES"), roms=["Game.nes"], excluded=0)]
            (app / "artwork.json").write_text(json.dumps({"box": "boxart3d", "background": "titlescreen"}))
            calls = []
            def apply(jobs, results):
                calls.append((jobs, results))
                if len(calls) == 1:
                    (app / "artwork.json").write_text(json.dumps({"box": "boxart2d", "background": "screenshot"}))
                    raise RuntimeError("interrupted")
                (results / "summary.json").write_text(json.dumps([{"outputs_verified": 2}]))
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.discover", return_value=libraries), patch("native_menu.require_closed"), patch("native_menu.dialog", side_effect=["2", "0", "done", "", "3", "1", "", "0"]), patch("native_menu.update", side_effect=apply), redirect_stdout(StringIO()):
                native_menu.main()
            self.assertEqual(calls[0], calls[1])
            self.assertIn("boxart3d", calls[1][0][0][1])
            self.assertTrue((calls[1][1] / "complete").exists())

    def test_fill_missing_menu_freezes_policy_and_preview_back_does_not_scan(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary).resolve()
            libraries = [dict(root=str(app / "NES"), roms=["Game.nes"], excluded=0)]
            calls = []
            def apply(jobs, results):
                calls.append((jobs, results))
                saved = json.loads((results / "selection.json").read_text())
                self.assertEqual(saved[0]["policy"], "fill-missing")
                if len(calls) == 1:
                    raise RuntimeError("interrupted")
                (results / "summary.json").write_text(json.dumps([{"outputs_verified": 0, "preserved": 1}]))
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.discover", return_value=libraries), patch("native_menu.require_closed"), patch("native_menu.dialog", side_effect=["5", "all", "", "3", "1", "", "0"]), patch("native_menu.update", side_effect=apply), redirect_stdout(StringIO()):
                native_menu.main()
            self.assertEqual(calls[0], calls[1])
            self.assertNotIn("--replace", calls[0][0][0][1])
            self.assertIn("--replace", native_menu.jobs_for(libraries, libraries, app)[0][1])
            with self.assertRaisesRegex(ValueError, "Invalid saved artwork policy"):
                native_menu.jobs_for([dict(libraries[0], policy="unknown")], libraries, app)
            with patch("native_menu.APP", app), patch("native_menu.sys.platform", "linux"), patch("native_menu.dialog", side_effect=["1", None, "0"]), patch("native_menu.discover") as discover:
                native_menu.main()
                discover.assert_not_called()

    def test_fill_missing_batch_preserves_existing_raw_and_cache_after_interruption(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Path(temporary).resolve()
            root = app / "NES"
            media = root / "media"
            cache = media / "optimized"
            cache.mkdir(parents=True)
            source = media / "box3d/Source.png"
            source.parent.mkdir()
            source.write_bytes(b"\x89PNG\r\n\x1a\nsource artwork")
            names = ("Existing", "New", "Zed")
            for name in names:
                (root / (name + ".nes")).write_bytes(b"ROM")
            xml = root / "gamelist.xml"
            xml.write_text('<gameList>' + ''.join('<game><path>%s.nes</path><boxart3d>media/box3d/Source.png</boxart3d></game>' % name for name in names) + '</gameList>')
            existing = media / "Existing.jpg"
            existing.write_bytes(b"keep original art")
            existing_cache = cache / "Existing.jpg"
            existing_cache.write_bytes(b"keep optimized art")
            new_cache = cache / "New.png"
            new_cache.write_bytes(b"stale cache without raw image")
            protected = [source, xml, existing, existing_cache] + [root / (name + ".nes") for name in names]
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected}
            cm = app / "ConsoleMode"
            definition = cm / "themeconfig/section_groups/Console.ini"
            definition.parent.mkdir(parents=True)
            definition.write_text('[NES]\nromDirs=/media/fat/games/NES/\nromExts=.nes\n')
            (cm / "ConsoleMode_arm").write_bytes(b"fixture frontend")
            libraries = [dict(root=str(root), roms=[name + ".nes" for name in names])]
            selected = [dict(libraries[0], policy="fill-missing", artwork={"box": "boxart3d", "background": "titlescreen"})]
            jobs = native_menu.jobs_for(selected, libraries, cm)
            results = app / "results"
            results.mkdir()
            original = deployment.os.unlink
            def interrupt(path, *positional, **kwargs):
                if str(path) == "New.png" and kwargs.get("dir_fd") is not None:
                    raise OSError("injected cache interruption")
                return original(path, *positional, **kwargs)
            with patch.object(deployment, "FRONTEND_SHA256", hashlib.sha256(b"fixture frontend").hexdigest()), patch("native_library.require_closed"), redirect_stdout(StringIO()):
                with patch("deployment.os.unlink", side_effect=interrupt), self.assertRaisesRegex(RuntimeError, "CLI exited 1"):
                    native_library.update(jobs, results)
                self.assertTrue(new_cache.exists())
                new_time = (media / "New.png").stat().st_mtime_ns
                native_library.update(jobs, results)
                self.assertFalse(new_cache.exists())
                self.assertEqual((media / "New.png").stat().st_mtime_ns, new_time)
                self.assertEqual((media / "Zed.png").read_bytes(), source.read_bytes())
                resumed = json.loads((results / "summary.json").read_text())[0]
                self.assertEqual((resumed["outputs_verified"], resumed["preserved"]), (2, 1))
                again = app / "again"
                again.mkdir()
                new_cache.write_bytes(b"newly optimized art")
                native_library.update(jobs, again)
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in protected})
            self.assertEqual(new_cache.read_bytes(), b"newly optimized art")
            summary = json.loads((again / "summary.json").read_text())[0]
            self.assertEqual((summary["copied"], summary["unchanged"], summary["preserved"]), (0, 0, 3))

    def test_failed_second_preflight_prevents_all_apply_calls(self):
        calls = []
        def run(args, output):
            calls.append(args[0])
            if len(calls) == 2:
                raise RuntimeError("conflicting artwork")
            return {"summary": {"copy": 2}}, 0
        with tempfile.TemporaryDirectory() as temporary, patch("native_library.run", side_effect=run):
            with self.assertRaisesRegex(RuntimeError, "conflicting artwork"):
                native_library.update([("NES", []), ("SNES", [])], Path(temporary))
        self.assertEqual(calls, ["scan", "scan"])


if __name__ == "__main__":
    unittest.main()
