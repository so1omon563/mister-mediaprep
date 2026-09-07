"""Persistent artwork survives scraper rewrites without mutating scraper inputs."""
from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import corrections as c
import deployment as d
import native_menu
from mister_mediaprep import main

PNG = b"\x89PNG\r\n\x1a\n"
JAPAN = "Super Mario Bros. 2 (Japan) (En).fds"
USA = "Super Mario Bros. 2 (USA).nes"


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        # Synthetic libraries must not depend on the host frontend process.
        for target in ("native_library.require_closed", "native_menu.require_closed"):
            guard = patch(target)
            guard.start()
            self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "games/NES"
        self.media = self.root / "media"
        (self.media / "source").mkdir(parents=True)
        (self.media / "optimized").mkdir()
        for rom in (JAPAN, USA):
            (self.root / rom).write_bytes(b"ROM")
        self.raw = self.media / (Path(JAPAN).stem + ".png")
        self.raw.write_bytes(PNG + b"correct Japanese box")
        self.background = self.media / (Path(JAPAN).stem + "-BG.png")
        self.background.write_bytes(PNG + b"correct Japanese background")
        self.source = self.media / "source/US.png"
        self.source.write_bytes(PNG + b"US scrape")
        self.xml = self.root / "gamelist.xml"
        self.scrape()
        self.cm = self.base / "ConsoleMode"
        definitions = self.cm / "themeconfig/section_groups/Console.ini"
        definitions.parent.mkdir(parents=True)
        definitions.write_text("[NES]\nromDirs=/media/fat/games/NES/\nromExts=.fds,.nes\n")
        (self.cm / "ConsoleMode_arm").write_bytes(b"fixture frontend")
        frontend = patch.object(d, "FRONTEND_SHA256", hashlib.sha256(b"fixture frontend").hexdigest())
        frontend.start()
        self.addCleanup(frontend.stop)

    def scrape(self):
        self.xml.write_text('<gameList><game id="1248"><boxart2d>media/source/US.png</boxart2d>'
                            '<boxart3d>media/source/US.png</boxart3d><screenshot>media/source/US.png</screenshot>'
                            '<titlescreen>media/source/US.png</titlescreen></game>' +
                            ''.join('<game parentid="1248"><path>./%s</path></game>' % rom for rom in (JAPAN, USA)) + '</gameList>')

    def cli(self, mode="scan", *extra):
        out, err = StringIO(), StringIO()
        args = [mode, "--rom-root", str(self.root)]
        if mode != "correction":
            args += ["--gamelist", str(self.xml), "--output", str(self.media), "--format", "json",
                     "--consolemode-root", str(self.cm), "--quiet"]
        with redirect_stdout(out), redirect_stderr(err):
            code = main(args + list(extra))
        return code, json.loads(out.getvalue()) if out.getvalue() else err.getvalue()

    def snapshot(self):
        return {str(path): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in self.root.rglob("*") if path.is_file() and c.STORE not in path.parts}

    def test_kept_copies_survive_rescrape_and_have_priority_without_changing_inputs(self):
        original = self.snapshot()
        code, _ = self.cli("correction", "--rom", JAPAN, "--keep", "both")
        self.assertEqual(code, 0)
        self.assertEqual(original, self.snapshot())
        saved, _ = c.read(self.root)
        for filename in saved[JAPAN].values():
            self.assertNotEqual((self.root / c.STORE / filename).stat().st_ino, self.raw.stat().st_ino)
        # Companion recreates the shared US parent and overwrites its own assets.
        self.scrape()
        self.source.write_bytes(PNG + b"later US scrape")
        wanted = self.raw.read_bytes(), self.background.read_bytes()
        self.raw.write_bytes(PNG + b"wrong raw output")
        self.background.write_bytes(PNG + b"wrong background output")
        cache = self.media / "optimized" / self.raw.name
        cache.write_bytes(b"stale cache")
        unrelated = self.media / "optimized/Other.png"
        unrelated.write_bytes(b"keep")
        options = ("--replace", "--box-source", "boxart3d", "--background-source", "titlescreen",
                   "--box-dir", str(self.source.parent))
        code, report = self.cli("scan", *options)
        self.assertEqual(code, 0, report)
        japanese = [row for row in report["rows"] if Path(row["rom"]).name == JAPAN]
        self.assertTrue(all(row["artwork_field"] == "correction" and not row["artwork_fallback"] for row in japanese))
        self.assertTrue(all(row["source"] == str(self.source) for row in report["rows"] if Path(row["rom"]).name == USA))
        manifest = self.root / c.STORE / c.MANIFEST
        self.assertIn(str(manifest), report["protected_inputs"])
        before = {p: Path(p).read_bytes() for p in report["protected_inputs"]}
        code, outcome = self.cli("apply", *options, "--rom", JAPAN)
        self.assertEqual(code, 0, outcome)
        self.assertEqual((self.raw.read_bytes(), self.background.read_bytes()), wanted)
        self.assertEqual(before, {p: Path(p).read_bytes() for p in before})
        self.assertFalse(cache.exists())
        self.assertEqual(unrelated.read_bytes(), b"keep")
        # Even an ambiguous generic replacement cannot override a saved correction.
        self.xml.write_text('<gameList><game><path>%s</path><image>untyped.png</image></game></gameList>' % JAPAN)
        code, report = self.cli("scan", "--rom", JAPAN)
        self.assertEqual(code, 0, report)
        self.assertTrue(all(row["artwork_field"] == "correction" for row in report["rows"]))

    def test_one_role_exact_paths_discovery_and_return_to_scraped_art(self):
        c.save(self.root, JAPAN, ["box"])
        code, report = self.cli("scan", "--rom", JAPAN, "--background-source", "titlescreen")
        self.assertEqual(code, 0)
        self.assertEqual({row["role"]: row["artwork_field"] for row in report["rows"]},
                         {"box": "correction", "background": "titlescreen"})
        # Corrected artwork alone keeps a library discoverable even without XML media.
        self.xml.write_text('<gameList><game><path>%s</path></game></gameList>' % JAPAN)
        discovered = native_menu.discover([self.root.parent], self.cm)
        self.assertEqual(discovered[0]["roms"], [JAPAN])
        # Renamed ROMs are not guessed from their title or former spelling.
        renamed = "Renamed Japanese game.fds"
        (self.root / JAPAN).rename(self.root / renamed)
        self.scrape()
        self.xml.write_text(self.xml.read_text().replace(JAPAN, renamed))
        code, report = self.cli("scan", "--rom", renamed)
        self.assertEqual(code, 0, report)
        self.assertTrue(all(row["source"] == str(self.source) for row in report["rows"]))
        before = self.snapshot()
        c.save(self.root, JAPAN, [])  # Removing an orphaned correction is allowed.
        self.assertEqual(c.read(self.root)[0], {})
        self.assertEqual(before, self.snapshot())

    def test_bad_saved_images_and_malformed_manifests_never_fall_back(self):
        saved = c.save(self.root, JAPAN, ["box"])
        image = self.root / c.STORE / saved["box"]
        original = image.read_bytes()
        for mutation in ("changed", "missing", "symlink"):
            with self.subTest(mutation=mutation):
                if mutation == "changed":
                    image.write_bytes(PNG + b"corrupt correction")
                elif mutation == "missing":
                    image.unlink()
                else:
                    image.unlink()
                    image.symlink_to(self.source)
                code, _ = self.cli("apply", "--replace")
                self.assertEqual(code, 2)
                if image.is_symlink():
                    image.unlink()
                image.write_bytes(original)
        manifest = self.root / c.STORE / c.MANIFEST
        contents = manifest.read_bytes()
        for bad in ('{"schema":1,"games":{},"games":{}}',
                    '{"schema":1,"games":{"../escape.fds":{"box":"a.png"}}}',
                    '{"schema":1,"games":{"Game.fds":{"box":"../outside.png"}}}',
                    '{"schema":true,"games":{}}'):
            manifest.write_text(bad)
            self.assertEqual(self.cli("scan")[0], 2)
        manifest.write_bytes(contents)
        manifest.unlink()
        manifest.symlink_to(self.xml)
        self.assertEqual(self.cli("scan")[0], 2)

    def test_save_failure_and_ambiguous_raw_art_keep_old_manifest_and_library(self):
        c.save(self.root, JAPAN, ["box"])
        manifest = self.root / c.STORE / c.MANIFEST
        old = manifest.read_bytes()
        before = self.snapshot()
        publish = c.publish
        def fail_manifest(fd, stage, name, replace):
            if name == c.MANIFEST:
                raise OSError("injected publication failure")
            publish(fd, stage, name, replace)
        with patch.object(c, "publish", side_effect=fail_manifest), self.assertRaises(OSError):
            c.save(self.root, JAPAN, ["background"])
        self.assertEqual(manifest.read_bytes(), old)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(list((self.root / c.STORE).glob(".pending-*")))
        self.background.with_suffix(".jpg").write_bytes(b"\xff\xd8\xffambiguous background")
        c.save(self.root, JAPAN, ["box"])  # An unselected role cannot block saving the box.
        self.raw.with_suffix(".jpg").write_bytes(b"\xff\xd8\xffanother")
        with self.assertRaisesRegex(ValueError, "Ambiguous"):
            c.save(self.root, JAPAN, ["box"])
        self.assertEqual(manifest.read_bytes(), old)

    def test_correction_changes_block_resume_and_stale_preflight(self):
        c.save(self.root, JAPAN, ["box"])
        manifest = self.root / c.STORE / c.MANIFEST
        original = manifest.read_bytes()
        journal = self.base / "job.jsonl"
        code, result = self.cli("apply", "--replace", "--journal", str(journal))
        self.assertEqual(code, 0, result)
        selection = native_menu.discover([self.root.parent], self.cm)
        frozen = [dict(selection[0], corrections=c.read(self.root)[1])]
        code, plan = self.cli("scan", "--replace")
        self.assertEqual(code, 0)
        c.save(self.root, JAPAN, [])
        before = self.snapshot()
        code, message = self.cli("apply", "--replace", "--resume", str(journal))
        self.assertEqual(code, 2)
        self.assertIn("game corrections", message)
        with self.assertRaisesRegex(ValueError, "corrections changed"):
            native_menu.jobs_for(frozen, selection, self.cm)
        with self.assertRaisesRegex(ValueError, "corrections changed"):
            d.apply(plan)
        self.assertEqual(before, self.snapshot())
        manifest.write_bytes(original)
        code, result = self.cli("apply", "--replace", "--resume", str(journal))
        self.assertEqual(code, 0, result)
        self.assertTrue(result["resume_complete"])

    def test_chosen_source_is_independent_survives_rescrape_and_waits_for_replace(self):
        chosen = self.media / "source/Japan.png"
        chosen.write_bytes(b"\xff\xd8\xffchosen JPEG with PNG filename")
        before = self.snapshot()
        code, result = self.cli("correction", "--rom", JAPAN, "--source", "box", str(chosen))
        self.assertEqual(code, 0, result)
        self.assertEqual(before, self.snapshot())
        saved = c.read(self.root)[0][JAPAN]
        self.assertEqual(set(saved), {"box"})
        snapshot = self.root / c.STORE / saved["box"]
        self.assertEqual(snapshot.suffix, ".jpg")
        wanted = snapshot.read_bytes()
        self.assertNotEqual(snapshot.stat().st_ino, chosen.stat().st_ino)
        c.save(self.root, JAPAN, ["background"], sources={"background": self.background})
        self.assertEqual(set(c.read(self.root)[0][JAPAN]), {"box", "background"})
        chosen.unlink()
        self.scrape()
        self.source.write_bytes(PNG + b"new wrong US scrape")
        before = self.snapshot()
        code, report = self.cli("apply", "--rom", JAPAN)
        self.assertEqual(code, 0, report)
        self.assertEqual(before, self.snapshot())  # Fill missing preserves the displayed artwork.
        code, report = self.cli("apply", "--rom", JAPAN, "--replace")
        self.assertEqual(code, 0, report)
        self.assertEqual(self.raw.with_suffix(".jpg").read_bytes(), wanted)
        self.assertFalse(self.raw.exists())
        self.assertTrue(all(row["artwork_field"] == "correction" for row in report["rows"]))
        code, result = self.cli("correction", "--rom", JAPAN, "--remove")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.raw.with_suffix(".jpg").read_bytes(), wanted)

    def test_explicit_source_rejects_unsafe_paths_and_keeps_previous_correction(self):
        c.save(self.root, JAPAN, ["box"])
        old = c.read(self.root)
        outside = self.base / "outside.png"
        outside.write_bytes(PNG + b"outside")
        link = self.media / "source/link.png"
        link.symlink_to(outside)
        (self.media / "optimized").rename(self.media / "Optimized")
        cache = self.media / "Optimized/cache.png"
        cache.write_bytes(PNG + b"cache")
        invalid = self.media / "source/bad.png"
        invalid.write_bytes(b"not an image")
        before = self.snapshot()
        for source in (outside, link, cache, invalid, "../outside.png"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                c.save(self.root, JAPAN, ["box"], sources={"box": source})
            self.assertEqual(c.read(self.root), old)
        for roles, sources in ((["unknown"], {"unknown": self.source}), (["box"], {}), ([], {})):
            with self.assertRaises(ValueError):
                c.save(self.root, JAPAN, roles, sources=sources)
        self.assertEqual(before, self.snapshot())

    def test_controller_source_picker_cancellation_navigation_and_missing_mapping(self):
        self.xml.write_text('<gameList><game><path>' + JAPAN + '</path></game></gameList>')
        chosen = self.media / "source/nested/Japan.png"
        chosen.parent.mkdir()
        chosen.write_bytes(PNG + b"chosen Japanese source")
        hidden = self.media / "source/.hidden.png"
        hidden.write_bytes(PNG)
        (self.media / "source/link.png").symlink_to(chosen)
        libraries = native_menu.discover([self.root.parent], self.cm)
        self.assertFalse(libraries[0]["roms"])
        self.assertIn(JAPAN, libraries[0]["correctable_roms"])
        with patch("native_menu.dialog", side_effect=["0", "0", "choose-box", None]), \
                patch("native_menu.save_correction") as save, patch("native_menu.require_closed") as closed:
            native_menu.choose_correction(libraries)
            save.assert_not_called()
            closed.assert_not_called()
        wanted = iter(["0", "0", "choose-box", "source", "nested", "up", "nested", "Japan.png"])
        def navigate(kind, text, options=()):
            if kind == "msgbox":
                return ""
            value = next(wanted)
            if value in {"0", "choose-box", "up"}:
                return value
            labels = [label for _, label in options]
            self.assertFalse(any(label in {"[folder] optimized", "link.png", ".hidden.png"} for label in labels))
            return next(tag for tag, label in options if label == value or label == "[folder] " + value)
        before = self.snapshot()
        with patch("native_menu.dialog", side_effect=navigate), patch("native_menu.require_closed") as closed, \
                patch("native_menu.update") as update:
            native_menu.choose_correction(libraries)
            closed.assert_called_once()
            update.assert_not_called()
        self.assertEqual(before, self.snapshot())
        self.assertEqual((self.root / c.STORE / c.read(self.root)[0][JAPAN]["box"]).read_bytes(), chosen.read_bytes())
        self.assertIn(JAPAN, native_menu.discover([self.root.parent], self.cm)[0]["roms"])
        empty = self.base / "empty"
        (empty / "media").mkdir(parents=True)
        with patch("native_menu.dialog", return_value="") as dialog:
            self.assertIsNone(native_menu.choose_source(empty))
            self.assertEqual(dialog.call_args.args[0], "msgbox")

    def test_controller_keep_remove_and_back_need_no_text_entry_or_apply(self):
        libraries = native_menu.discover([self.root.parent], self.cm)
        for replies in ([None], ["0", None], ["0", "0", None]):
            with patch("native_menu.dialog", side_effect=replies), patch("native_menu.save_correction") as save:
                native_menu.choose_correction(libraries)
                save.assert_not_called()
        before = self.snapshot()
        with patch("native_menu.dialog", side_effect=["6", "0", "0", "both", "", "0"]), \
                patch("native_menu.APP", self.base), patch("native_menu.sys.platform", "linux"), \
                patch("native_menu.discover", return_value=libraries), patch("native_menu.require_closed") as closed, \
                patch("native_menu.update") as apply:
            native_menu.main()
            closed.assert_called_once()
            apply.assert_not_called()
        self.assertEqual(set(c.read(self.root)[0][JAPAN]), {"box", "background"})
        self.assertEqual(before, self.snapshot())
        with patch("native_menu.dialog", side_effect=["0", "0", "remove", ""]), patch("native_menu.require_closed"):
            native_menu.choose_correction(libraries)
        self.assertEqual(c.read(self.root)[0], {})
        self.assertEqual(before, self.snapshot())


if __name__ == "__main__":
    unittest.main()
