"""Arcade previews inventory MRAs and reuse mapping checks without device writes."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import deployment
import native_library
import native_menu
from mister_mediaprep import main

PNG = b"\x89PNG\r\n\x1a\n"


class ArcadePreviewTests(unittest.TestCase):
    def setUp(self):
        # Synthetic libraries must not depend on the host frontend process.
        for target in ("native_library.require_closed", "native_menu.require_closed"):
            guard = patch(target)
            guard.start()
            self.addCleanup(guard.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.sd, self.usb = self.base / "sdcard", self.base / "usb1"
        self.root = self.sd / "_Arcade"
        (self.root / "media/box3d").mkdir(parents=True)
        (self.root / "media/titlescreen").mkdir()
        (self.usb / "games/mame").mkdir(parents=True)
        (self.usb / "games/mame/game.zip").write_bytes(b"unrelated ROM archive")
        self.box = self.put("media/box3d/Scraped ID.png", PNG + b"box")
        self.bg = self.put("media/titlescreen/Other name.png", PNG + b"background")
        self.mra("Game.mra")
        self.mra("_alternatives/Game (Japan).mra")
        self.xml = self.root / "gamelist.xml"
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra'])

    def put(self, path, data):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)
        return file

    def mra(self, path):
        return self.put(path, b'<misterromdescription><setname>game</setname><rom zip="game.zip"/></misterromdescription>')

    def gamelist(self, paths, extra=""):
        self.xml.write_text('<gameList>' + ''.join('<game parentid="1"><path>./%s</path></game>' % path for path in paths) +
                            '<game id="1"><boxart3d>media/box3d/Scraped ID.png</boxart3d>'
                            '<titlescreen>media/titlescreen/Other name.png</titlescreen></game>' + extra + '</gameList>')

    def snapshot(self):
        return {str(path): (path.read_bytes() if path.is_file() else None, path.stat().st_mtime_ns)
                for path in self.base.rglob("*") if not path.is_symlink()}

    def preview(self, *extra):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(['arcade-preview', '--arcade-root', str(self.root), '--format', 'json', '--quiet', *extra])
        return code, json.loads(out.getvalue()) if out.getvalue() else err.getvalue()

    def test_split_drives_root_and_alternates_are_read_only_without_rom_archive_access(self):
        self.mra('_Organized/By Name/Game.mra')
        self.mra('cores/Helper.mra')
        self.mra('media/Ignored.mra')
        self.mra('._Metadata.mra')
        (self.root / 'Linked.mra').symlink_to(self.root / 'Game.mra')
        (self.root / 'LinkedDirectory').symlink_to(self.usb, target_is_directory=True)
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra', '_Organized/By Name/Game.mra'])
        before = self.snapshot()
        open_file = Path.open
        def artwork_only(path, *args, **kwargs):
            if path.suffix.lower() in {'.zip', '.mra'} or path.is_relative_to(self.usb):
                raise AssertionError('Preview tried to open ROM/MRA content: ' + str(path))
            return open_file(path, *args, **kwargs)
        with patch.object(Path, 'open', artwork_only):
            code, report = self.preview('--box-source', 'boxart3d', '--background-source', 'titlescreen')
        self.assertEqual(code, 0, report)
        self.assertEqual(report['summary']['copy'], 4)
        self.assertEqual(len(report['arcade']['mras']), 2)
        self.assertEqual(report['arcade']['mapped_mras'], 2)
        self.assertTrue(report['read_only'])
        self.assertTrue(all(row['cache_action'] == 'none; preview only' for row in report['rows']))
        self.assertEqual({Path(row['source']) for row in report['rows']}, {self.box, self.bg})
        self.assertEqual({Path(row['destination']).name for row in report['rows']},
                         {'Game.png', 'Game-BG.png', 'Game (Japan).png', 'Game (Japan)-BG.png'})
        self.assertTrue(all(Path(row['destination']).parent == self.sd / 'media' for row in report['rows']))
        excluded = '\n'.join(item['path'] for item in report['arcade']['excluded'])
        for name in ('_Organized', 'cores', 'media', '._Metadata', 'LinkedDirectory', 'Linked.mra'):
            self.assertIn(name, excluded)
        self.assertEqual(before, self.snapshot())
        self.assertFalse((self.sd / 'media').exists())
        self.assertEqual(report, self.preview('--box-source', 'boxart3d', '--background-source', 'titlescreen')[1])

    def test_missing_scrape_entries_missing_artwork_and_stale_paths_are_visible(self):
        orphan = self.mra('Unscraped.mra')
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra', 'Gone.mra'])
        self.bg.unlink()
        code, report = self.preview()
        self.assertEqual(code, 0, report)
        self.assertEqual(report['arcade']['unmapped_mras'], [str(orphan)])
        self.assertEqual(len(report['arcade']['stale_mappings']), 1)
        unmapped = [row for row in report['rows'] if row['rom'] == str(orphan)]
        self.assertEqual(len(unmapped), 2)
        self.assertTrue(all(row['operation'] == 'missing' and row['gamelist_mapping'] == 'absent' for row in unmapped))
        self.assertTrue(all('No gamelist entry' in row['reason'] for row in unmapped))
        self.assertEqual(report['summary']['missing'], 6)
        self.xml.write_text('<gameList/>')
        code, report = self.preview()
        self.assertEqual(code, 0)
        self.assertEqual(report['arcade']['mapped_mras'], 0)
        self.assertEqual(report['summary']['missing'], 6)

    def test_shared_stems_conflict_on_different_or_unknown_sources_even_for_selection(self):
        alternate = self.mra('_alternatives/Game.mra')
        self.put('media/box3d/Different.png', PNG + b'different')
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra'],
                      '<game parentid="1"><path>_alternatives/Game.mra</path><boxart2d>media/box3d/Different.png</boxart2d></game>')
        code, report = self.preview('--rom', 'Game.mra')
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['conflict'], 1)
        self.assertEqual(len(report['rows']), 2)
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra'])
        code, report = self.preview('--rom', 'Game.mra')
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['conflict'], 2)
        self.assertIn(str(alternate), report['arcade']['unmapped_mras'])
        # Exact shared mappings with identical bytes remain a valid shared namespace.
        self.gamelist(['Game.mra', '_alternatives/Game (Japan).mra', '_alternatives/Game.mra'])
        code, report = self.preview('--rom', 'Game.mra')
        self.assertEqual(code, 0, report)
        self.assertTrue(all('shared destination' in row['reason'] for row in report['rows']))
        alternate.rename(alternate.with_name('game.mra'))
        self.gamelist(['Game.mra', '_alternatives/game.mra'])
        code, report = self.preview('--rom', 'Game.mra')
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['conflict'], 2)

    def test_foreign_paths_require_mapping_and_unsafe_entries_never_match(self):
        text = self.xml.read_text().replace('./Game.mra', '/media/fat/_Arcade/Game.mra').replace(
            './_alternatives/', '/media/fat/_Arcade/_alternatives/')
        text = text.replace('media/box3d/', '/media/fat/_Arcade/media/box3d/').replace(
            'media/titlescreen/', '/media/fat/_Arcade/media/titlescreen/')
        self.xml.write_text(text)
        code, report = self.preview('--map-root', '/media/fat', str(self.sd))
        self.assertEqual(code, 0, report)
        self.assertEqual(report['arcade']['mapped_mras'], 2)
        self.assertTrue(all(row['match_basis']['rom'].startswith('remapped:') for row in report['rows']))
        (self.root / 'Linked.mra').symlink_to(self.root / 'Game.mra')
        self.gamelist(['Game.mra', 'Linked.mra', '../Escape.mra'])
        code, report = self.preview()
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['error'], 4)
        self.mra('_Organized/Game.mra')
        code, error = self.preview('--rom', '_Organized/Game.mra')
        self.assertEqual(code, 2)
        self.assertIn('discovered MRA', error)

    def test_existing_output_policies_and_case_aliases_leave_raw_and_caches_unchanged(self):
        output = self.sd / 'media'
        (output / 'optimized').mkdir(parents=True)
        (output / 'Game.png').write_bytes(PNG + b'old raw')
        (output / 'optimized/Game.png').write_bytes(b'old cache')
        before = self.snapshot()
        code, report = self.preview()
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['preserve'], 1)
        code, report = self.preview('--replace')
        self.assertEqual(code, 0)
        self.assertEqual(report['summary']['replace'], 1)
        self.assertEqual(before, self.snapshot())
        (output / 'Game.png').rename(output / 'game.png')
        before = self.snapshot()
        code, report = self.preview('--replace')
        self.assertEqual(code, 1)
        self.assertEqual(report['summary']['conflict'], 1)
        self.assertTrue(any('Case/Unicode' in row['reason'] for row in report['rows']))
        self.assertEqual(before, self.snapshot())

    def test_excluded_canonical_parent_and_source_protection_are_retained(self):
        parent = self.mra('_Organized/Parent.mra')
        self.xml.write_text('<gameList><game id="1"><path>_Organized/Parent.mra</path>'
                            '<boxart2d>media/box3d/Scraped ID.png</boxart2d></game>'
                            '<game parentid="1"><path>Game.mra</path></game>'
                            '<game><path>_Organized/Other.mra</path><boxart3d>media/titlescreen/Other name.png</boxart3d></game></gameList>')
        code, report = self.preview()
        self.assertEqual(code, 0, report)
        self.assertNotIn(str(parent), {row['rom'] for row in report['rows']})
        self.assertIn(str(self.bg), report['protected_inputs'])
        box = next(row for row in report['rows'] if row['rom'] == str(self.root / 'Game.mra') and row['role'] == 'box')
        self.assertEqual(box['source'], str(self.box))

    def test_preview_and_existing_apply_paths_cannot_write_arcade_artwork(self):
        _, report = self.preview('--replace', '--rom', 'Game.mra')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'cannot be deployed'):
            deployment.prepare(report, self.base / 'NoFrontend', experimental=True)
        with self.assertRaisesRegex(ValueError, 'cannot be deployed'):
            deployment.apply(report)
        self.assertEqual(before, self.snapshot())
        cm = self.base / 'ConsoleMode'
        cm.mkdir()
        (cm / 'ConsoleMode_arm').write_bytes(b'frontend')
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()), redirect_stderr(StringIO()):
            code = main(['apply', '--gamelist', str(self.xml), '--rom-root', str(self.root), '--output', str(self.root / 'media'),
                         '--consolemode-root', str(cm), '--experimental-layout', '--rom', 'Game.mra', '--quiet'])
        self.assertEqual(code, 2)
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
            main(['arcade-preview', '--arcade-root', str(self.root), '--journal', str(self.base / 'journal')])
        self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.base / 'journal').exists())
        self.assertFalse((self.sd / 'media').exists())

    def test_arcade_layout_bulk_and_selected_updates_reuse_writer_guards_and_resume(self):
        output = self.sd / 'media'
        cache = output / 'optimized'
        cache.mkdir(parents=True)
        (output / 'Game.jpg').write_bytes(b'old raw')
        (cache / 'Game.jpg').write_bytes(b'old cache')
        (output / 'Unrelated.png').write_bytes(PNG + b'unrelated')
        (cache / 'Unrelated.png').write_bytes(b'unrelated cache')
        cm = self.base / 'ConsoleMode'
        cm.mkdir()
        (cm / 'ConsoleMode_arm').write_bytes(b'frontend')
        journal = self.base / 'arcade.jsonl'
        common = ['--gamelist', str(self.xml), '--rom-root', str(self.root), '--output', str(output),
                  '--consolemode-root', str(cm), '--arcade-layout', '--format', 'json', '--quiet']
        selected = ['--rom', 'Game.mra', '--rom', '_alternatives/Game (Japan).mra']
        def invoke(mode, *extra):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = main([mode, *common, *extra])
            return code, json.loads(out.getvalue()) if out.getvalue() else err.getvalue()
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()):
            before = self.snapshot()
            for args in (['--experimental-layout'],
                         [*selected, '--output', str(self.root / 'media')]):
                code, error = invoke('apply', *args)
                self.assertEqual(code, 2, error)
                self.assertEqual(before, self.snapshot())
            # The apply path inventories unlisted MRAs too, before filtering selections.
            unknown = self.mra('_alternatives/Game.mra')
            before = self.snapshot()
            code, error = invoke('apply', *selected, '--replace')
            self.assertEqual(code, 2, error)
            self.assertIn('conflicts', error)
            self.assertEqual(before, self.snapshot())
            unknown.unlink()
            self.mra('_Organized/Hidden.mra')
            before = self.snapshot()
            code, error = invoke('apply', '--experimental-layout', '--rom', '_Organized/Hidden.mra')
            self.assertEqual(code, 2, error)
            self.assertEqual(before, self.snapshot())
            with patch('mister_mediaprep.sys.platform', 'linux'), \
                    patch('native_library.require_closed', side_effect=RuntimeError('Close Console Mode')):
                code, error = invoke('apply', *selected, '--journal', str(journal))
            self.assertEqual(code, 2, error)
            self.assertEqual(before, self.snapshot())
            # A normal full update skips missing artwork and preserves existing art.
            self.mra('Unscraped.mra')
            code, report = invoke('apply')
            self.assertEqual(code, 0, report)
            self.assertEqual(report['copied_files'], 3)
            self.assertEqual(report['summary']['preserve'], 1)
            self.assertEqual(report['summary']['missing'], 2)
            self.assertEqual((output / 'Game.jpg').read_bytes(), b'old raw')
            self.assertEqual((cache / 'Game.jpg').read_bytes(), b'old cache')
            before = self.snapshot()
            code, report = invoke('scan', *selected, '--replace')
            self.assertEqual(code, 0, report)
            self.assertFalse(report['read_only'])
            self.assertEqual(before, self.snapshot())
            code, report = invoke('apply', '--replace', '--journal', str(journal))
            self.assertEqual(code, 0, report)
            self.assertEqual(report['copied_files'], 1)
            self.assertEqual(report['unchanged_files'], 3)
            self.assertFalse((output / 'Game.jpg').exists())
            self.assertFalse((cache / 'Game.jpg').exists())
            for stem in ('Game', 'Game (Japan)'):
                self.assertEqual((output / (stem + '.png')).read_bytes(), self.box.read_bytes())
                self.assertEqual((output / (stem + '-BG.png')).read_bytes(), self.bg.read_bytes())
            after = self.snapshot()
            for path, value in before.items():
                if Path(path).is_relative_to(self.root) or Path(path).is_relative_to(self.usb) or 'Unrelated' in path:
                    self.assertEqual(after[path], value, path)
            code, report = invoke('apply', '--replace', '--resume', str(journal))
            self.assertEqual(code, 0, report)
            self.assertTrue(report['resume_complete'])
            self.assertEqual(after, self.snapshot())
            common.remove('--arcade-layout')
            code, error = invoke('apply', '--replace', '--resume', str(journal))
            self.assertEqual(code, 2, error)
            self.assertIn('original paths', error)

    def test_controller_browses_missing_entries_and_cancels_without_updates(self):
        self.mra('Missing.mra')
        bases = [self.sd / 'games', self.usb / 'games']
        runs = self.base / 'runs'
        runs.mkdir()
        before = self.snapshot()
        for replies in ([None], ['0', None]):
            with patch('native_menu.dialog', side_effect=replies), patch('native_menu.run') as run:
                native_menu.arcade_menu(bases, runs, None)
                run.assert_not_called()
        self.assertEqual(before, self.snapshot())
        # A real conflicting preview exits 1 but remains readable in the menu.
        self.mra('_alternatives/Game.mra')
        with patch('native_menu.dialog', side_effect=['0', 'replace', 'issues', '0', '', 'excluded', '0', '', '0']) as dialog, \
                patch('native_menu.require_closed') as closed, patch('native_menu.update') as update, redirect_stdout(StringIO()):
            native_menu.arcade_menu(bases, runs, None)
            closed.assert_not_called()
            update.assert_not_called()
        self.assertTrue(any('Incompatible shared output' in call.args[1] or 'No gamelist entry' in call.args[1]
                            for call in dialog.call_args_list))
        reports = list(runs.glob('arcade-preview-*/arcade.json'))
        self.assertEqual(len(reports), 1)
        self.assertTrue(json.loads(reports[0].read_text())['read_only'])
        self.assertFalse(list(runs.glob('*/selection.json')))
        self.assertFalse((self.sd / 'media').exists())
        # Main menu routes directly to this preview, not ordinary library discovery.
        with patch('native_menu.APP', self.base), patch('native_menu.sys.platform', 'linux'), \
                patch('native_menu.dialog', side_effect=['7', '0']), patch('native_menu.arcade_menu') as arcade, \
                patch('native_menu.discover') as discover:
            native_menu.main()
            arcade.assert_called_once()
            discover.assert_not_called()
        self.xml.write_text('<broken>')
        with self.assertRaises(RuntimeError), redirect_stderr(StringIO()):
            native_library.run(['arcade-preview', '--arcade-root', str(self.root), '--format', 'json'],
                               self.base / 'bad.json', allow_issues=True)

    def test_controller_apply_and_resume_keep_arcade_policy_sources_and_exclusions(self):
        cm = self.base / 'ConsoleMode'
        cm.mkdir()
        (cm / 'ConsoleMode_arm').write_bytes(b'frontend')
        self.mra('_Organized/Hidden.mra')
        output = self.sd / 'media'
        (output / 'optimized').mkdir(parents=True)
        (output / 'Game.jpg').write_bytes(b'old raw')
        (output / 'optimized/Game.jpg').write_bytes(b'old cache')
        runs = self.base / 'runs'
        runs.mkdir()
        preferences = {'box': 'boxart3d', 'background': 'titlescreen'}
        bases = [self.sd / 'games', self.usb / 'games']
        protected = {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with patch('native_menu.dialog', side_effect=['0', 'fill-missing', 'apply']), \
                patch('native_menu.require_closed', side_effect=RuntimeError('Close Console Mode')), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Close Console Mode'):
                native_menu.arcade_menu(bases, runs, preferences, cm)
        self.assertFalse(list(runs.glob('*/selection.json')))
        real_publish = deployment.publish
        def fail_alternate(fd, stage, name, replace):
            if name == 'Game (Japan).png':
                raise OSError('Interrupted alternate artwork write')
            return real_publish(fd, stage, name, replace)
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()), \
                patch('native_menu.dialog', side_effect=['0', 'fill-missing', 'apply']), \
                patch('deployment.publish', side_effect=fail_alternate), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(RuntimeError):
                native_menu.arcade_menu(bases, runs, preferences, cm)
        selection_file = next(runs.glob('*/selection.json'))
        selected = json.loads(selection_file.read_text())
        self.assertTrue(selected[0]['arcade'])
        self.assertEqual(selected[0]['policy'], 'fill-missing')
        self.assertEqual(selected[0]['artwork'], preferences)
        self.assertEqual(set(selected[0]['roms']), {'Game.mra', '_alternatives/Game (Japan).mra'})
        self.assertFalse((selection_file.parent / 'complete').exists())
        (self.base / 'artwork.json').write_text(json.dumps({'box': 'boxart2d', 'background': 'screenshot'}))
        def local_path(value):
            return {'/media/fat/games': self.sd / 'games', '/media/fat/ConsoleMode': cm}.get(str(value), Path(value))
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()), \
                patch('native_menu.APP', self.base), patch('native_menu.Path', side_effect=local_path), \
                patch('native_menu.sys.platform', 'linux'), patch('native_menu.discover', return_value=[]), \
                patch('native_menu.dialog', side_effect=['3', '1', '', '0']), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            native_menu.main()
        self.assertTrue((selection_file.parent / 'complete').exists())
        self.assertEqual((output / 'Game.jpg').read_bytes(), b'old raw')
        self.assertEqual((output / 'optimized/Game.jpg').read_bytes(), b'old cache')
        self.assertEqual((output / 'Game (Japan).png').read_bytes(), self.box.read_bytes())
        for stem in ('Game', 'Game (Japan)'):
            self.assertEqual((output / (stem + '-BG.png')).read_bytes(), self.bg.read_bytes())
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()), \
                patch('native_menu.dialog', side_effect=['0', 'replace', 'apply', '']), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            native_menu.arcade_menu(bases, runs, preferences, cm)
        self.assertEqual((output / 'Game.png').read_bytes(), self.box.read_bytes())
        self.assertFalse((output / 'Game.jpg').exists())
        self.assertFalse((output / 'optimized/Game.jpg').exists())
        self.assertEqual(protected, {str(p): p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_controller_apply_rechecks_collisions_before_any_writes(self):
        self.mra('_alternatives/Game.mra')
        cm = self.base / 'ConsoleMode'
        cm.mkdir()
        (cm / 'ConsoleMode_arm').write_bytes(b'frontend')
        runs = self.base / 'runs'
        runs.mkdir()
        with patch.object(deployment, 'FRONTEND_SHA256', hashlib.sha256(b'frontend').hexdigest()), \
                patch('native_menu.dialog', side_effect=['0', 'replace', 'apply']), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            with self.assertRaises(RuntimeError):
                native_menu.arcade_menu([self.sd / 'games'], runs, None, cm)
        self.assertFalse((self.sd / 'media').exists())
        self.assertFalse(list(runs.glob('*/complete')))


if __name__ == '__main__':
    unittest.main()
