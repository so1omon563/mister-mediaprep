"""Run with: python3 -B -m unittest -v"""

from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from xml.sax.saxutils import escape

from mister_mediaprep import main


PNG = b"\x89PNG\r\n\x1a\nfixture"  # Signature test, deliberately not a decoded image.


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.put("Aces of the Air (USA).chd", b"rom")
        self.put("media/box2d/Aces.png", PNG)
        self.put("media/screenshot/Aces.png", PNG + b"background")

    def put(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def game(self, rom="Aces of the Air (USA).chd", box="media/box2d/Aces.png", background="media/screenshot/Aces.png"):
        return f"<game><path>{escape(rom)}</path><boxart2d>{escape(box)}</boxart2d><screenshot>{escape(background)}</screenshot></game>"

    def run_scan(self, xml, *extra, output="media"):
        self.put("gamelist.xml", ("<gameList>" + xml + "</gameList>").encode())
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["scan", "--gamelist", str(self.root / "gamelist.xml"),
                         "--rom-root", str(self.root), "--output", str(self.root / output),
                         "--format", "json", *extra])
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else stderr.getvalue()

    def test_parent_after_child_and_read_only(self):
        xml = ('<game parentid="102812"><path>./Aces of the Air (USA).chd</path></game>'
               '<game id="102812"><boxart2d>./media/box2d/Aces.png</boxart2d>'
               '<screenshot>./media/screenshot/Aces.png</screenshot></game>')
        self.put("gamelist.xml", ("<gameList>" + xml + "</gameList>").encode())
        def snapshot():
            return {str(p.relative_to(self.root)): (p.read_bytes() if p.is_file() else None, p.stat().st_mtime_ns)
                    for p in [self.root, *self.root.rglob("*")]}
        before = snapshot()
        stdout = StringIO()
        with redirect_stdout(stdout):
            code = main(["scan", "--gamelist", str(self.root / "gamelist.xml"), "--rom-root", str(self.root),
                         "--output", str(self.root / "absent/output"), "--format", "json"])
        self.assertEqual(before, snapshot())
        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["copy"], 2)
        self.assertEqual({Path(r["destination"]).name for r in report["rows"]},
                         {"Aces of the Air (USA).png", "Aces of the Air (USA)-BG.png"})
        self.assertTrue(all(r["match_basis"]["artwork"].startswith("parent:") for r in report["rows"]))

    def test_direct_override_and_preservation(self):
        self.put("alternative/Aces.png", PNG + b"alternative")
        self.put("media/Aces of the Air (USA).jpg", b"existing")
        code, report = self.run_scan(self.game(), "--box-dir", str(self.root / "alternative"))
        self.assertEqual(code, 0)
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertEqual(box["operation"], "preserve")
        self.assertEqual(box["source"], str(self.root / "alternative/Aces.png"))
        self.assertEqual(report["summary"]["copy"], 1)

    def test_replace_and_mislabeled_jpeg(self):
        jpeg = b"\xff\xd8\xff\xe0JFIF fixture"
        source = self.put("media/screenshot/Aces.png", jpeg)
        existing = self.put("media/Aces of the Air (USA).jpg", b"old")
        _, report = self.run_scan(self.game(), "--replace")
        self.assertEqual(report["policy"], "replace")
        self.assertEqual(report["summary"]["replace"], 1)
        bg = next(row for row in report["rows"] if row["role"] == "background")
        self.assertTrue(bg["destination"].endswith("-BG.jpg"))
        self.assertTrue(bg["extension_corrected"])
        self.assertEqual(bg["image_format"], "jpg")
        self.assertEqual(source.read_bytes(), jpeg)
        self.assertEqual(existing.read_bytes(), b"old")

    def test_selection_retains_other_game_collision(self):
        self.put("Other.chd", b"rom")
        xml = self.game() + self.game(rom="Other.chd")
        _, report = self.run_scan(xml, "--rom", "Other.chd", "--replace")
        self.assertEqual(len(report["rows"]), 2)
        self.assertTrue(all(row["rom"].endswith("Other.chd") for row in report["rows"]))
        self.put("one/Game.chd", b"rom")
        self.put("two/Game.chd", b"rom")
        self.put("different.png", PNG + b"different")
        xml = self.game(rom="one/Game.chd") + self.game(rom="two/Game.chd", box="different.png")
        _, report = self.run_scan(xml, "--rom", "one/Game.chd", "--replace")
        self.assertEqual(report["summary"]["conflict"], 1)
        code, _ = self.run_scan(xml, "--rom", "Other.chd")
        self.assertEqual(code, 2)

    def test_child_overrides_parent(self):
        xml = self.game().replace("<game>", '<game parentid="1">')
        code, report = self.run_scan(xml + '<game id="1"><boxart2d>missing.png</boxart2d></game>')
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["copy"], 2)

    def test_artwork_choices_use_exact_tags_and_missing_file_fallback(self):
        box3d = self.put("media/box3d/Different.jpg", b"\xff\xd8\xff\xe0three dimensional")
        title = self.put("media/titlescreen/Title.png", PNG + b"title")
        xml = self.game().replace("</game>",
            '<boxart3d>media/box3d/Different.jpg</boxart3d><titlescreen>media/titlescreen/Title.png</titlescreen></game>')
        options = ("--box-source", "boxart3d", "--background-source", "titlescreen")
        code, report = self.run_scan(xml, *options)
        self.assertEqual(code, 0)
        self.assertEqual({r["source"] for r in report["rows"]}, {str(box3d), str(title)})
        self.assertEqual({r["artwork_field"] for r in report["rows"]}, {"boxart3d", "titlescreen"})
        self.assertFalse(any(r["artwork_fallback"] for r in report["rows"]))
        self.assertTrue(next(r for r in report["rows"] if r["role"] == "box")["destination"].endswith(".jpg"))
        box3d.unlink()
        code, report = self.run_scan(xml, *options)
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertEqual(code, 0)
        self.assertEqual(box["source"], str(self.root / "media/box2d/Aces.png"))
        self.assertTrue(box["artwork_fallback"])
        self.assertIn("fallback", box["reason"])
        # A library with only the alternate types still works with default preferences.
        xml = xml.replace("<boxart2d>media/box2d/Aces.png</boxart2d>", "").replace(
            "<screenshot>media/screenshot/Aces.png</screenshot>", "")
        self.put("media/box3d/Different.jpg", b"\xff\xd8\xff\xe0three dimensional")
        code, report = self.run_scan(xml)
        self.assertEqual(code, 0)
        self.assertTrue(all(r["artwork_fallback"] for r in report["rows"]))

    def test_game_specific_fallback_precedes_parent_preferred_art(self):
        self.put("media/box3d/Parent.png", PNG + b"wrong shared edition")
        xml = self.game().replace("<game>", '<game parentid="1">')
        xml += '<game id="1"><boxart3d>media/box3d/Parent.png</boxart3d></game>'
        code, report = self.run_scan(xml, "--box-source", "boxart3d")
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertEqual(code, 0)
        self.assertEqual(box["source"], str(self.root / "media/box2d/Aces.png"))
        self.assertTrue(box["match_basis"]["artwork"].startswith("direct:"))
        (self.root / "media/box2d/Aces.png").unlink()
        _, report = self.run_scan(xml, "--box-source", "boxart3d")
        self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["operation"], "missing")
        # With no per-game box mappings, inheritance does use the parent's 3D art.
        _, report = self.run_scan(xml.replace("<boxart2d>media/box2d/Aces.png</boxart2d>", ""), "--box-source", "boxart3d")
        self.assertTrue(next(r for r in report["rows"] if r["role"] == "box")["match_basis"]["artwork"].startswith("parent:"))

    def test_generic_import_preserves_preference_inheritance_and_explicit_precedence(self):
        generic = self.game().replace("boxart2d", "thumbnail").replace("screenshot>", "image>")
        code, report = self.run_scan(generic)
        self.assertEqual(code, 0)
        self.assertEqual({r["artwork_field"] for r in report["rows"]}, {"thumbnail", "image"})
        self.assertEqual({r["artwork_type"] for r in report["rows"]}, {"boxart2d", "screenshot"})
        self.assertFalse(any(r["artwork_fallback"] for r in report["rows"]))
        box3d = self.put("media/box3d/Parent.png", PNG + b"parent")
        parent = '<game id="1"><image>media/box3d/Parent.png</image></game>'
        child = generic.replace("<game>", '<game parentid="1">')
        _, report = self.run_scan(child + parent, "--box-source", "boxart3d")
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertTrue(box["artwork_fallback"])
        self.assertTrue(box["match_basis"]["artwork"].startswith("direct:"))
        child = child.replace("<thumbnail>media/box2d/Aces.png</thumbnail>", "")
        _, report = self.run_scan(child + parent, "--box-source", "boxart3d")
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertEqual(box["source"], str(box3d))
        self.assertTrue(box["match_basis"]["artwork"].startswith("parent:"))
        explicit = self.game().replace("</game>", '<thumbnail>media/box2d/Other.png</thumbnail></game>')
        _, report = self.run_scan(explicit)
        self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["artwork_field"], "boxart2d")
        self.put("media/box3d/Preferred.jpg", b"\xff\xd8\xffJPEG")
        xml = generic.replace("</game>", '<image>media/box3d/Preferred.jpg</image></game>')
        _, report = self.run_scan(xml, "--box-source", "boxart3d")
        self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["artwork_type"], "boxart3d")

    def test_generic_conflicts_and_invalid_sources_remain_visible(self):
        for mappings, status in (
            ('<image>arbitrary.png</image>', "ambiguous"),
            ('<image>media/box2d/screenshot/Aces.png</image>', "ambiguous"),
            ('<image>media/box2d/Aces.png</image><thumbnail>media/box2d/Other.png</thumbnail>', "ambiguous"),
            ('<image>../box2d/outside.png</image>', "error"),
            ('<image>media/box2d/Bad.png</image>', "unsupported"),
            ('<image>media/box2d/Link.png</image>', "error"),
        ):
            self.put("media/box2d/Bad.png", b"invalid")
            link = self.root / "media/box2d/Link.png"
            if not link.is_symlink():
                link.symlink_to(self.root / "media/box2d/Aces.png")
            with self.subTest(mappings=mappings):
                xml = '<game><path>Aces of the Air (USA).chd</path>' + mappings + '</game>'
                _, report = self.run_scan(xml)
                self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["operation"], status)
        xml = '<game><path>Aces of the Air (USA).chd</path><image>media/BOX2D/Aces.png</image></game>'
        _, report = self.run_scan(xml)
        self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["operation"], "copy")

    def test_unselected_generic_mapping_protects_output_alias(self):
        source = self.put("media/Aces of the Air (USA).png", PNG)
        self.put("Other.chd", b"rom")
        xml = self.game() + '<game><path>Other.chd</path><image>media/Aces of the Air (USA).png</image></game>'
        _, report = self.run_scan(xml, "--replace", "--rom", "Aces of the Air (USA).chd")
        self.assertIn(str(source), report["protected_inputs"])
        self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["operation"], "conflict")

    def test_invalid_preferred_artwork_is_not_hidden_by_fallback(self):
        self.put("bad.png", b"invalid image")
        (self.root / "linked.png").symlink_to(self.root / "media/box2d/Aces.png")
        for path, status in (("../escape.png", "error"), ("linked.png", "error"), ("bad.png", "unsupported")):
            with self.subTest(path=path):
                xml = self.game().replace("</game>", f"<boxart3d>{path}</boxart3d></game>")
                code, report = self.run_scan(xml, "--box-source", "boxart3d")
                self.assertEqual(code, 1)
                self.assertEqual(next(r for r in report["rows"] if r["role"] == "box")["operation"], status)

    def test_unchosen_artwork_type_remains_protected_from_replacement(self):
        other = self.put("media/box3d/Other.jpg", b"\xff\xd8\xff\xe0three dimensional")
        mapped = self.put("media/Aces of the Air (USA).png", PNG + b"mapped 2D source")
        xml = self.game(box=mapped.relative_to(self.root).as_posix()).replace("</game>",
            f"<boxart3d>{other.relative_to(self.root).as_posix()}</boxart3d></game>")
        code, report = self.run_scan(xml, "--box-source", "boxart3d", "--replace")
        self.assertEqual(code, 1)
        self.assertIn(str(mapped), report["protected_inputs"])
        box = next(r for r in report["rows"] if r["role"] == "box")
        self.assertEqual(box["operation"], "conflict")
        self.assertEqual(box["reason"], "Output aliases an input artwork file")

    def test_duplicate_missing_parents_and_folder(self):
        for parents in ('', '<game id="1"/><game id="1"/>'):
            with self.subTest(parents=parents):
                code, report = self.run_scan(self.game().replace("<game>", '<game parentid="1">') + parents + '<folder><path>folder</path></folder>')
                self.assertEqual(code, 1)
                self.assertEqual(report["summary"]["error"], 2)
                self.assertTrue(any(d["status"] == "unsupported" for d in report["diagnostics"]))

    def test_foreign_paths_and_actual_spelling(self):
        xml = self.game(rom=r"C:\Old\aces of the air (usa).chd", box=r"C:\Old\media\box2d\Aces.png")
        code, report = self.run_scan(xml, "--map-root", "C:/Old", str(self.root))
        self.assertEqual(code, 0)
        self.assertTrue(all(Path(r["rom"]).name == "Aces of the Air (USA).chd" for r in report["rows"]))
        self.assertTrue(all(r["match_basis"]["rom"] == "remapped:case-unicode" for r in report["rows"]))
        code, report = self.run_scan(xml)
        self.assertEqual(report["summary"]["error"], 2)

    def test_metadata_missing_signature_and_traversal(self):
        self.put("media/box2d/._Aces.png", PNG)
        self.put("media/box2d/bad.png", b"bad")
        for source, status in [("media/box2d/._Aces.png", "unsupported"), ("media/box2d/bad.png", "unsupported"),
                               ("missing.png", "missing"), ("../outside.png", "error")]:
            with self.subTest(source=source):
                _, report = self.run_scan(self.game(box=source))
                self.assertEqual(report["summary"][status], 1)

    def test_unicode_normalization(self):
        actual = self.put("Caf\u00e9.chd", b"rom")
        _, report = self.run_scan(self.game(rom="Cafe\u0301.chd"))
        self.assertEqual(Path(report["rows"][0]["rom"]).name, actual.name)

    def test_ambiguous_fallback(self):
        self.put("GAME.chd", b"a")
        self.put("game.chd", b"b")
        if len([p for p in self.root.iterdir() if p.name.lower() == "game.chd"]) != 2:
            self.skipTest("Filesystem cannot hold case-distinct names")
        _, report = self.run_scan(self.game(rom="Game.chd"))
        self.assertEqual(report["summary"]["ambiguous"], 2)

    def test_symlinks_and_output_alias(self):
        (self.root / "linked").symlink_to(self.root / "media", target_is_directory=True)
        code, _ = self.run_scan(self.game(), output="linked")
        self.assertEqual(code, 2)
        _, report = self.run_scan(self.game(box="linked/box2d/Aces.png"))
        self.assertEqual(report["summary"]["error"], 1)
        self.put("Aces.chd", b"rom")
        _, report = self.run_scan(self.game(rom="Aces.chd"), output="media/box2d")
        self.assertEqual(report["summary"]["conflict"], 1)

    def test_output_collisions_and_identical_sharing(self):
        self.put("one/Game.chd", b"rom1")
        self.put("two/Game.chd", b"rom2")
        xml = self.game(rom="one/Game.chd") + self.game(rom="two/Game.chd")
        _, report = self.run_scan(xml)
        self.assertEqual(report["summary"]["copy"], 4)
        self.put("other.png", PNG + b"different")
        _, report = self.run_scan(self.game(rom="one/Game.chd") + self.game(rom="two/Game.chd", box="other.png"))
        self.assertEqual(report["summary"]["conflict"], 2)
        self.put("Game-BG.chd", b"rom")
        _, report = self.run_scan(self.game(rom="one/Game.chd") + self.game(rom="Game-BG.chd"))
        self.assertEqual(report["summary"]["conflict"], 2)

    def test_existing_variants_and_case_collisions(self):
        self.put("media/Aces of the Air (USA).jpg", b"old")
        self.put("media/aces of the air (usa).png", PNG)
        _, report = self.run_scan(self.game())
        self.assertEqual(report["summary"]["conflict"], 1)
        self.put("one/Game.chd", b"rom")
        self.put("two/game.chd", b"rom")
        _, report = self.run_scan(self.game(rom="one/Game.chd") + self.game(rom="two/game.chd"))
        self.assertEqual(report["summary"]["conflict"], 4)

    def test_cross_game_source_alias(self):
        self.put("Other.chd", b"rom")
        self.put("media/Aces of the Air (USA).png", PNG)
        _, report = self.run_scan(self.game() + self.game(rom="Other.chd", box="media/Aces of the Air (USA).png"))
        self.assertEqual(report["summary"]["conflict"], 1)

    def test_input_boundary_and_hardlink_alias(self):
        with TemporaryDirectory() as other:
            outside = Path(other).resolve() / "outside.png"
            outside.write_bytes(PNG)
            _, report = self.run_scan(self.game(box=str(outside)))
            self.assertEqual(report["summary"]["error"], 1)
        os.link(self.root / "media/box2d/Aces.png", self.root / "media/Aces of the Air (USA).png")
        _, report = self.run_scan(self.game())
        self.assertEqual(report["summary"]["conflict"], 1)

    def test_missing_roles_and_determinism(self):
        code, report = self.run_scan(self.game(background=""))
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["missing"], 1)
        _, repeated = self.run_scan(self.game(background=""))
        self.assertEqual(report, repeated)

    def test_invalid_xml_and_missing_volume(self):
        code, error = self.run_scan("<game>")
        self.assertEqual(code, 2)
        self.assertIn("mismatched tag", error)
        code, error = self.run_scan("<!DOCTYPE gameList>")
        self.assertEqual(code, 2)
        with redirect_stderr(StringIO()):
            code = main(["scan", "--gamelist", str(self.root / "absent/gamelist.xml"),
                         "--rom-root", str(self.root / "absent"), "--output", str(self.root / "absent/media")])
        self.assertEqual(code, 2)
        self.assertFalse((self.root / "absent").exists())

    def test_selected_scan_does_not_open_unrelated_artwork(self):
        import mister_mediaprep as module
        xml = self.game()
        for index in range(20):
            self.put(f"Other{index}.chd", b"ROM")
            self.put(f"media/box2d/Other{index}.png", PNG)
            xml += self.game(rom=f"Other{index}.chd", box=f"media/box2d/Other{index}.png", background="")
        with patch.object(module, "image_check", wraps=module.image_check) as check:
            code, report = self.run_scan(xml, "--rom", "Aces of the Air (USA).chd")
        self.assertEqual(code, 0)
        self.assertEqual(check.call_count, 2)
        self.assertIn(str(self.root / "media/box2d/Other19.png"), report["protected_inputs"])


if __name__ == "__main__":
    unittest.main()
