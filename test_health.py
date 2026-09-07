"""Read-only coverage includes ROMs absent from scraper XML."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mister_mediaprep import main
import native_menu
import deployment


class HealthTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "games/FUTURE"
        self.media = self.root / "media"
        self.media.mkdir(parents=True)
        self.console = self.base / "ConsoleMode"
        definitions = self.console / "themeconfig/section_groups"
        definitions.mkdir(parents=True)
        (definitions / "future.ini").write_text("[Future]\nromDirs=/media/fat/games/FUTURE,/media/fat/games/ALIAS\nromExts=.ne?,.cue\n")
        self.xml = self.root / "gamelist.xml"

    def put(self, name, data=b"ROM"):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def snapshot(self):
        return {str(p): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
                for root in (self.root, self.console) for p in [root, *root.rglob("*")] if not p.is_symlink()}

    def health(self, *extra):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["health", "--rom-root", str(self.root), "--consolemode-root", str(self.console), "--format", "json", "--quiet"] + list(extra))
        return code, json.loads(out.getvalue()) if out.getvalue() else err.getvalue()

    def test_inventory_mapping_failures_and_namespace_are_read_only(self):
        for name in ("Good.nes", "Unlisted.nes", "Missing.nes", "Invalid.nes", "one/Shared.nes", "two/Shared.nes", "Unsupported.cue", "._Metadata.nes", "media/Ignored.nes"):
            self.put(name)
        (self.root / "Linked").symlink_to(self.media, target_is_directory=True)
        self.put("media/box.png", b"\x89PNG\r\n\x1a\nbox")
        self.put("media/background.png", b"\x89PNG\r\n\x1a\nbackground")
        self.put("media/invalid.png", b"not an image")
        self.xml.write_text('<gameList><game id="1"><boxart2d>media/box.png</boxart2d><screenshot>media/background.png</screenshot></game>'
                            '<game parentid="1"><path>Good.nes</path></game>'
                            '<game><path>Missing.nes</path><boxart2d>media/absent.png</boxart2d></game>'
                            '<game><path>Invalid.nes</path><boxart2d>media/invalid.png</boxart2d></game>'
                            '<game><path>Gone.nes</path></game>'
                            '<game parentid="1"><path>one/Shared.nes</path></game>'
                            '<game><path>Unsupported.cue</path></game></gameList>')
        before = self.snapshot()
        open_file = Path.open
        def no_rom_reads(path, *args, **kwargs):
            if path.suffix.lower() in {".nes", ".cue"}:
                raise AssertionError("Health read a ROM: " + str(path))
            return open_file(path, *args, **kwargs)
        with patch.object(Path, "open", no_rom_reads), patch("deployment.prepare", side_effect=AssertionError("Health inspected caches")):
            code, report = self.health()
        self.assertEqual(code, 1, report)
        self.assertEqual(self.snapshot(), before)
        inventory = report["health"]
        self.assertEqual(inventory["game_count"], 6)
        self.assertEqual(inventory["artwork_role_count"], 14)  # Six on-disk games and one stale XML entry, two roles each.
        self.assertEqual(inventory["mapped_games"], 4)
        self.assertEqual({Path(p).name for p in inventory["unmapped_games"]}, {"Unlisted.nes", "Shared.nes"})
        self.assertEqual(len(inventory["stale_mappings"]), 1)
        by_game = {(Path(r["rom"]).name, r["role"]): r for r in report["rows"]}
        self.assertIn("No gamelist entry", by_game["Unlisted.nes", "box"]["reason"])
        self.assertIn("Path does not exist", by_game["Missing.nes", "box"]["reason"])
        self.assertEqual(by_game["Invalid.nes", "box"]["operation"], "unsupported")
        self.assertTrue(any(r["operation"] == "conflict" for r in report["rows"] if r["rom"].endswith("one/Shared.nes")))
        exclusions = " ".join(x["path"] + x["reason"] for x in inventory["excluded"])
        for label in ("Unsupported.cue", "media", "._Metadata.nes", "Linked"):
            self.assertIn(label, exclusions)
        self.assertTrue(report["read_only"])
        with self.assertRaisesRegex(ValueError, "Read-only health report"):
            deployment.prepare(report, self.console)
        self.assertTrue(all(r["cache_action"] == "none; read-only health report" for r in report["rows"]))

    def test_source_inventory_keeps_all_references_and_snapshots_and_never_writes(self):
        from corrections import save, STORE
        png = b"\x89PNG\r\n\x1a\nfixture"
        for name in ("First.nes", "Second.nes", "Excluded.cue"):
            self.put(name)
        source = self.put("media/box2d/Shared.png", png)
        for name in ("box3d/Shared.png", "screenshot/Shared.png", "titlescreen/Shared.png", "logo/Shared.png", "source/Excluded.png"):
            self.put("media/" + name, png)
        unused = self.put("media/unused/Shared.png", png + b"unused")
        alias = self.media / "unused/Linked.png"
        os.link(source, alias)
        self.put("media/First.png", png + b"prepared")
        self.put("media/optimized/First.png", b"cache")
        self.put("media/box2d/readme.txt", b"notes")
        self.xml.write_text('<gameList><game id="parent">'
                            '<boxart2d>media/box2d/Shared.png</boxart2d><boxart3d>media/box3d/Shared.png</boxart3d>'
                            '<screenshot>media/screenshot/Shared.png</screenshot><titlescreen>media/titlescreen/Shared.png</titlescreen>'
                            '<logo>media/logo/Shared.png</logo></game>'
                            '<game parentid="parent"><path>First.nes</path></game>'
                            '<game parentid="parent"><path>Second.nes</path></game>'
                            '<game><path>Excluded.cue</path><image>media/source/Excluded.png</image></game></gameList>')
        save(self.root, "First.nes", ["box"])
        retained = self.put(STORE + "/old.png", png + b"retained")
        before = self.snapshot()
        code, report = self.health()
        self.assertEqual(code, 0, report)
        inventory = report["source_inventory"]
        rows = {entry["path"]: entry for entry in inventory["entries"]}
        self.assertEqual(rows[str(unused)]["category"], "unreferenced")
        self.assertEqual(inventory["summary"]["unreferenced"], {"paths": 1, "bytes": unused.stat().st_size})
        self.assertEqual(rows[str(alias)]["category"], "reference-alias")
        self.assertIn(str(source), rows[str(alias)]["aliases"])
        for relative in ("box2d/Shared.png", "box3d/Shared.png", "screenshot/Shared.png", "titlescreen/Shared.png", "logo/Shared.png", "source/Excluded.png"):
            self.assertEqual(rows[str(self.media / relative)]["category"], "referenced")
        self.assertEqual(rows[str(source)]["references"][0]["used_by"], ["First.nes", "Second.nes"])
        self.assertEqual(rows[str(retained)]["category"], "retained-snapshot")
        self.assertEqual(inventory["summary"]["saved-correction"]["paths"], 1)
        self.assertEqual(rows[str(self.media / "First.png")]["category"], "prepared")
        self.assertEqual(rows[str(self.media / "optimized/First.png")]["category"], "optimized")
        self.assertEqual(rows[str(self.media / "box2d/readme.txt")]["category"], "other")
        self.assertEqual(sum(x["paths"] for x in inventory["summary"].values()), len(rows))
        self.assertEqual(sum(x["bytes"] for x in inventory["summary"].values()), sum(Path(p).stat().st_size for p in rows))
        # Subset scans still account for the excluded game and every canonical alternative.
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            code = main(["scan", "--rom-root", str(self.root), "--gamelist", str(self.xml),
                         "--output", str(self.media), "--rom", "First.nes", "--source-inventory", "--format", "json"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(json.loads(out.getvalue())["source_inventory"], inventory)
        with patch("native_menu.dialog", side_effect=["unused", "0", "", "0"]) as dialog:
            native_menu.browse_report(report, self.root, "Health", [])
        self.assertTrue(any(str(unused.stat().st_size) + " bytes" in call.args[1] for call in dialog.call_args_list))
        self.assertEqual(self.snapshot(), before)

    def test_inventory_remaps_overrides_and_flags_unresolved_links(self):
        png = b"\x89PNG\r\n\x1a\nfixture"
        self.put("First.nes")
        source = self.put("media/box2d/Shared.png", png)
        override = self.put("override/Shared.png", png + b"override")
        unused = self.put("override/Unused.png", png)
        self.xml.write_text('<gameList><game><path>First.nes</path>'
                            '<boxart2d>/old-art/box2d/Shared.png</boxart2d></game></gameList>')
        options = ("--map-root", "/old-art", str(self.media), "--box-dir", str(override.parent))
        before = self.snapshot()
        code, report = self.health(*options)
        self.assertEqual(code, 0, report)
        rows = {row["path"]: row for row in report["source_inventory"]["entries"]}
        self.assertEqual(rows[str(source)]["category"], "referenced")
        self.assertEqual(rows[str(override)]["category"], "referenced")
        self.assertEqual(rows[str(unused)]["category"], "unreferenced")
        self.assertEqual(before, self.snapshot())
        link = self.media / "linked.png"
        link.symlink_to(source)
        self.xml.write_text('<gameList><game><path>First.nes</path><logo>media/linked.png</logo></game></gameList>')
        before = self.snapshot()
        code, report = self.health(*options)
        rows = {row["path"]: row for row in report["source_inventory"]["entries"]}
        self.assertEqual(rows[str(link)]["category"], "excluded")
        self.assertEqual(rows[str(unused)]["category"], "unverified")
        self.assertTrue(report["source_inventory"]["unresolved_references"])
        self.assertEqual(before, self.snapshot())
        hidden = self.put("media/.hidden/Referenced.png", png)
        self.xml.write_text('<gameList><game><path>First.nes</path><logo>media/.hidden/Referenced.png</logo></game></gameList>')
        code, report = self.health(*options)
        rows = {row["path"]: row for row in report["source_inventory"]["entries"]}
        self.assertEqual(rows[str(hidden)]["category"], "referenced")
        self.xml.unlink()
        code, report = self.health(*options)
        rows = {row["path"]: row for row in report["source_inventory"]["entries"]}
        self.assertEqual(rows[str(unused)]["category"], "unverified")

    def test_missing_xml_and_media_remain_visible_and_controller_browses_report(self):
        self.media.rmdir()
        self.put("Unlisted.nes")
        before = self.snapshot()
        code, report = self.health()
        self.assertEqual(code, 1, report)
        self.assertEqual(report["health"]["game_count"], 1)
        self.assertEqual(report["summary"]["missing"], 2)
        self.assertIn("No gamelist.xml", report["diagnostics"][0]["reason"])
        runs = self.base / "runs"
        runs.mkdir()
        with patch("native_menu.dialog", return_value=None), patch("native_menu.run") as run:
            native_menu.health_menu([self.root.parent], self.console, runs, None)
            run.assert_not_called()
        with patch("native_menu.dialog", side_effect=["0", "issues", "0", "", "0"]) as dialog, \
                patch("native_menu.require_closed", side_effect=AssertionError("Read-only report required closing the frontend")), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            native_menu.health_menu([self.root.parent], self.console, runs, None)
        self.assertTrue(any("No gamelist entry" in call.args[1] for call in dialog.call_args_list))
        self.assertEqual(len(list(runs.glob("health-*/health.json"))), 1)
        self.assertFalse(list(runs.glob("*/selection.json")))
        self.assertEqual(self.snapshot(), before)
        with patch("native_menu.APP", self.base), patch("native_menu.sys.platform", "linux"), \
                patch("native_menu.dialog", side_effect=["8", "0"]), patch("native_menu.health_menu") as menu, patch("native_menu.discover") as discover:
            native_menu.main()
            menu.assert_called_once()
            discover.assert_not_called()


if __name__ == "__main__":
    unittest.main()
