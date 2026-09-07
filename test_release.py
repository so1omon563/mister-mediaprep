"""Release artifacts and gates must fail before an immutable tag is created."""
import ast
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import build
import release
from corrections import STORE, MANIFEST
from mister_mediaprep import VERSION


class ReleaseTests(unittest.TestCase):
    def test_reproducible_package_embedded_install_upgrade_and_runtime(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            first = build.release(root / 'first')
            second = build.release(root / 'second')
            for a, b in zip(first, second):
                self.assertEqual(a.read_bytes(), b.read_bytes())
            runtime, launcher, installer = first
            checksums = (runtime.parent / 'SHA256SUMS').read_text().splitlines()
            for line in checksums:
                expected, name = line.split('  ')
                self.assertEqual(hashlib.sha256((runtime.parent / name).read_bytes()).hexdigest(), expected)
            body, payload = installer.read_bytes().split(b'\n__PAYLOAD__\n', 1)
            self.assertEqual(base64.b64decode(payload), runtime.read_bytes())
            self.assertNotIn(b'@VERSION@', body)
            self.assertIn(VERSION.encode(), body)
            self.assertIn(hashlib.sha256(runtime.read_bytes()).hexdigest().encode(), body)
            with zipfile.ZipFile(runtime) as archive:
                self.assertIn(b'MIT License', archive.read('LICENSE'))
                for name in archive.namelist():
                    if name.endswith('.py'):
                        ast.parse(archive.read(name), filename=name, feature_version=(3, 9))
            scripts = root / 'Scripts'
            scripts.mkdir()
            # Execute the embedded installer implementation, without its device entrypoint.
            embedded = body.split(b"<<'PYTHON_INSTALLER'\n", 1)[1].split(b'\nPYTHON_INSTALLER\n', 1)[0]
            scope = {'__name__': 'test_embedded_installer'}
            exec(compile(embedded, 'embedded-installer', 'exec'), scope)
            data = runtime.read_bytes()
            scope['install'](data, hashlib.sha256(data).hexdigest(), scripts)
            app = scripts / '.config/mister-mediaprep'
            preserved = []
            for name in ('runs/update/report.json', 'runs/update/selection.json', 'runs/update/library.jsonl',
                         'artwork.json'):
                path = app / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'preserve saved user state')
                preserved.append(path)
            for name in (MANIFEST, 'kept.png'):
                path = root / 'library' / STORE / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'preserve library correction')
                preserved.append(path)
            before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved}
            (app / 'mister-mediaprep.pyz').write_bytes(b'previous installed runtime')
            scope['install'](data, hashlib.sha256(data).hexdigest(), scripts)
            self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in preserved})
            self.assertEqual((scripts / 'mister-mediaprep.sh').read_bytes(), launcher.read_bytes())
            version = subprocess.check_output([sys.executable, '-B', str(app / 'mister-mediaprep.pyz'), '--version'], text=True)
            self.assertEqual(version.strip(), 'MiSTer MediaPrep ' + VERSION)
            game = root / 'Game.nes'
            game.write_bytes(b'ROM')
            image = root / 'media/box2d/Game.png'
            image.parent.mkdir(parents=True)
            image.write_bytes(b'\x89PNG\r\n\x1a\nimage')
            xml = root / 'gamelist.xml'
            xml.write_text('<gameList><game><path>Game.nes</path><image>media/box2d/Game.png</image></game></gameList>')
            command = [sys.executable, '-B', str(runtime), 'scan', '--gamelist', str(xml), '--rom-root', str(root),
                       '--output', str(root / 'media'), '--format', 'json', '--quiet']
            report = json.loads(subprocess.check_output(command, text=True))
            self.assertEqual(report['summary']['copy'], 1)
            self.assertFalse((root / 'media/Game.png').exists())
            with self.assertRaises(ValueError):
                build.release(root / 'bad-version', 'v99.0.0')
            self.assertFalse((root / 'bad-version').exists())

    def test_marker_free_tag_only_release_and_existing_tag_recovery(self):
        tags = ['v1', 'v0.1.2', 'v0.1.1', 'v9.0.0-rc.1']
        self.assertEqual(release.plan('fix: ordinary merge\n#patch', tags, VERSION)['requested'], 'false')
        self.assertEqual(release.plan('fix: #patchwork #release', tags, VERSION)['requested'], 'false')
        self.assertEqual(release.plan('release: #patch', tags, '0.1.3'),
                         {'requested': 'true', 'publish': 'false', 'tag': 'v0.1.3'})
        self.assertEqual(release.plan('release: #patch #release\nexample #major', tags, '0.1.3')['publish'], 'true')
        self.assertEqual(release.plan('release: #minor #release', [], '0.1.0')['tag'], 'v0.1.0')
        for title, version in [('release: #patch', VERSION), ('release: #minor #patch', '0.1.3'),
                               ('release: #patch #skip', '0.1.3'), ('release: #patch #prerelease:rc', '0.1.3')]:
            with self.subTest(title=title), self.assertRaises(ValueError):
                release.plan(title, tags, version)
        with patch('release.VERSION', '0.1.3'), patch('release.git', side_effect=['abc', 'abc']):
            release.verify_tag('v0.1.3')
        with patch('release.VERSION', '0.1.3'), patch('release.git', side_effect=['old', 'new']), self.assertRaises(ValueError):
            release.verify_tag('v0.1.3')
        with self.assertRaises(ValueError):
            release.verify_tag('main')


if __name__ == '__main__':
    unittest.main()
