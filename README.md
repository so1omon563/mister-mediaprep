# MiSTer MediaPrep

Prepare your existing game artwork for MiSTer Console Mode.

MiSTer MediaPrep reads `gamelist.xml` artwork mappings and copies the selected
images into the filenames and layout Console Mode expects. Run it directly on
MiSTer with a controller, or use its command-line interface from a desktop.

Preview changes before applying them, fill missing artwork without replacing
existing images, or refresh a library with different artwork choices. Save
individual game corrections so later scrapes do not undo artwork you have
already checked. Library health reports explain missing mappings, unsupported
entries, and unreferenced source images. Interrupted updates can be resumed.

MediaPrep does not download, resize, or convert artwork. It works with your
existing PNG/JPEG images and leaves ROMs, gamelists, and source artwork intact.

## How it works

Given a ROM and artwork mappings in `gamelist.xml`, MediaPrep leaves the scraped
sources in place and prepares file-stem images for Console Mode:

```text
NES/
├── Example Game (USA).nes
├── gamelist.xml
└── media/
    ├── box2d/example-box.png          source main image
    ├── screenshot/example-shot.png   source background
    ├── Example Game (USA).png         prepared main image
    └── Example Game (USA)-BG.png      prepared background
```

Arcade uses a separate layout; see [Arcade artwork](docs/USAGE.md#arcade-artwork).

## Requirements

- MiSTer with Python 3.9 or newer and its standard `dialog` utility for the
  controller menu, or Python 3.9 or newer on macOS/Linux for the CLI.
- Existing ROMs, `gamelist.xml`, and mapped PNG/JPEG artwork from a scraper or
  another source.
- For artwork writes, the exact supported Console Mode 1.1.3 binary. MediaPrep
  verifies its SHA-256 and rejects imported `gamelist_art.tsv` mappings. See
  [Compatibility](COMPATIBILITY.md) for the binary hash and verified layouts.

No third-party Python packages are required.

## Install on MiSTer

Download
[`mister-mediaprep-install.sh`](https://github.com/so1omon563/mister-mediaprep/releases/latest/download/mister-mediaprep-install.sh)
from the [latest release](https://github.com/so1omon563/mister-mediaprep/releases/latest)
and copy it to `/media/fat/Scripts`.

Exit MediaPrep if it is already running, then open
**Scripts → mister-mediaprep-install.sh**. Confirm `Installer exit: 0`, then open
**Scripts → mister-mediaprep.sh**. The installer contains the runtime and
launcher; it updates those files without changing game artwork, preferences,
reports, journals, or saved corrections.

Keep the installer until the menu opens successfully. You can then remove it
from Scripts.

## First controller update

1. Finish scraping the library first.
2. Close Console Mode and pause other artwork writers.
3. Open **Scripts → mister-mediaprep.sh**.
4. Optionally open **Artwork choices**. The defaults are 2D boxes and
   screenshots, with fallback when the preferred type is absent.
5. Open **Preview**, choose Fill missing or Replace, select systems, and review
   added, replaced, kept, missing, and fallback counts. Preview writes nothing.
6. Run **Fill missing artwork** to preserve existing images, or
   **Replace existing artwork** to refresh them. Select the same systems.
7. After completion, reopen Console Mode and check artwork and game launching.

D-pad moves; the controller's mapped Select and Back buttons choose or return.
No keyboard entry is required.

> Changed from 2D to 3D boxes, changed background style, or saved a correction
> over an existing wrong image? Use **Replace existing artwork**. Fill missing
> preserves artwork that is already present.

Use **Library health (read-only)** when a library or game is missing, and use
**Arcade artwork** for `_Arcade`. See the [controller usage guide](docs/USAGE.md)
for artwork choices, saved corrections, health reports, Arcade, and optimization.

## Run from a desktop

Clone the repository, then run a read-only scan:

```sh
git clone https://github.com/so1omon563/mister-mediaprep.git
cd mister-mediaprep
./mister-mediaprep scan \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media \
  --consolemode-root /Volumes/sdcard/ConsoleMode
```

A plain scan may use a nonexistent output directory and does not check the
frontend. Supplying `--consolemode-root` performs deployment preflight; `apply`
requires it and an existing correct media directory. Quit Console Mode and pause
other writers before applying. See the [CLI reference](docs/CLI.md) for complete
commands, mapping rules, JSON output, exit codes, and selected-game examples.

## More documentation

| Guide | Contents |
| --- | --- |
| [Controller usage](docs/USAGE.md) | Discovery, artwork choices, corrections, health, Arcade, optimization |
| [CLI reference](docs/CLI.md) | Commands, options, mapping rules, reports, exit codes |
| [Recovery and troubleshooting](docs/RECOVERY.md) | Interrupted updates, journals, repairs, installation recovery |
| [Compatibility](COMPATIBILITY.md) | Exact frontend requirement, supported layouts, verification evidence |
| [Release checks](RELEASING.md) | Maintainer build, tagging, and release process |

## Safety model

Preview is read-only. Apply rechecks current inputs and the complete output
namespace before writing. Existing artwork is preserved unless Replace is
chosen. Updates publish verified files individually and invalidate only affected
same-stem optimized caches; they are not whole-library transactions. Read
[Recovery and troubleshooting](docs/RECOVERY.md) before repairing an interrupted
or externally changed update.

## License

MIT. See [LICENSE](LICENSE). No games or scraped artwork are included.
