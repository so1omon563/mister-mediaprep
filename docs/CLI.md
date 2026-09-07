# Command-line reference

The CLI runs on Python 3.9 or newer without third-party packages. Clone the
repository before using the source entrypoint:

```sh
git clone https://github.com/so1omon563/mister-mediaprep.git
cd mister-mediaprep
./mister-mediaprep --help
```

On MiSTer, the installed `mister-mediaprep.sh` launcher accepts the same CLI
arguments over SSH or from the F9 terminal.

## Scan and apply

`scan` creates a read-only plan. Existing images are preserved by default; add
`--replace` to plan a refresh.

```sh
./mister-mediaprep scan \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media \
  --consolemode-root /Volumes/sdcard/ConsoleMode \
  --format json > /tmp/psx-scan.json
```

A plain scan does not perform frontend deployment checks and permits a
nonexistent output directory. Supplying `--consolemode-root` enables preflight.
`apply` always requires it, and the correct `media` output must already exist.
Apply reruns the current scan and preflight; it never executes a saved report.

Quit Console Mode and pause other artwork writers before applying:

```sh
./mister-mediaprep apply \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media \
  --consolemode-root /Volumes/sdcard/ConsoleMode \
  --replace --format json > /tmp/psx-apply.json
```

Keep reports outside game, media, correction, and frontend directories.

## Common options

| Option | Meaning |
| --- | --- |
| `--box-source boxart2d\|boxart3d` | Preferred box type; the other type is fallback |
| `--background-source screenshot\|titlescreen` | Preferred background type; the other type is fallback |
| `--box-dir PATH` | Select box images by mapped filename from this folder |
| `--background-dir PATH` | Select backgrounds by mapped filename from this folder |
| `--map-root OLD NEW` | Remap a moved absolute XML path prefix; repeatable |
| `--replace` | Plan or apply replacement of existing artwork |
| `--rom PATH` | Limit work to an exact ROM path relative to the root; repeatable |
| `--consolemode-root PATH` | Console Mode directory; required for apply, optional scan preflight |
| `--format text\|json` | Report format; default is text |
| `--quiet` | Suppress progress on stderr |
| `--source-inventory` | Add full-library source inventory to scan; read-only only |
| `--journal PATH` | Apply with a new progress journal outside protected directories |
| `--resume PATH` | Resume a journal with its original paths and options |
| `--experimental-layout` | Explicit selected-game test of an unconfigured file type |
| `--arcade-layout` | Use the verified Arcade root/sibling-media layout |

Example path remapping:

```sh
--map-root 'C:/Games/PSX' /Volumes/usb0/games/PSX
```

Slashes are normalized. Exact spelling wins, followed only by unique
case/Unicode fallback. Folder overrides match mapped filenames and do not guess
game titles.

## Mapping and output rules

The default choices are `boxart2d` and `screenshot`, with `boxart3d` and
`titlescreen` as their respective fallbacks. A game's direct mappings take
priority over shared canonical `parentid` mappings. Saved corrections take
priority over both.

Generic `image` and `thumbnail` XML tags are accepted only when their path has
exactly one recognized artwork folder type: `box2d`/`boxart2d`,
`box3d`/`boxart3d`, `screenshot`, or `titlescreen`. Explicit typed tags win.
Ambiguous paths are reported; image type is never inferred from a filename.

Prepared output uses the actual ROM stem; backgrounds add `-BG`. PNG/JPEG
signatures determine the output extension. A JPEG named `.png` is copied as
`.jpg` without conversion or source modification.

Nested ROM files still write to the system-root `media` directory using their
own stems. Playlists, disc descriptors, launch definitions, XML folder mappings,
and nested gamelists are unsupported. Archive contents are not traversed; a ZIP
mapping refers to the archive file itself. MRA files require `--arcade-layout`.

Deployment also requires the exact supported Console Mode binary and rejects
imported `caches/gamelist_art.tsv` mappings. See
[Compatibility](../COMPATIBILITY.md).

## Selected games

Use repeatable exact selections to constrain an update:

```sh
./mister-mediaprep scan \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media \
  --consolemode-root /Volumes/sdcard/ConsoleMode \
  --rom 'Aces of the Air (USA).chd' \
  --rom 'Alien Resurrection (USA).chd' \
  --replace --format json > /tmp/psx-selection.json
```

Selected scans still inspect the full output namespace and all inputs needed for
collision and source protection. An unfiltered apply containing unsupported
entries stops before any writes.

## Corrections

Save current prepared artwork:

```sh
./mister-mediaprep correction --rom-root /media/usb0/games/NES \
  --rom 'Game (Japan).nes' --keep both
```

Choose an existing source or remove the correction:

```sh
./mister-mediaprep correction --rom-root /media/usb0/games/NES \
  --rom 'Game (Japan).nes' --source box 'media/box2d/Chosen image.png'

./mister-mediaprep correction --rom-root /media/usb0/games/NES \
  --rom 'Game (Japan).nes' --remove
```

Use `--keep box`, `--keep background`, or `--source background PATH` for one
role. Sources must stay inside the library and cannot be optimized caches.
Saving a correction does not update displayed artwork; use Replace afterward.

## Health and Arcade preview

```sh
./mister-mediaprep health \
  --rom-root /media/usb0/games/VECTREX \
  --consolemode-root /media/fat/ConsoleMode --format json

./mister-mediaprep arcade-preview \
  --arcade-root /Volumes/sdcard/_Arcade \
  --replace --format json > /tmp/arcade-preview.json
```

Add `--arcade-layout` to health for an Arcade root. Health and Arcade preview
are read-only and cannot be applied. `arcade-preview` supports the common source,
mapping, selection, and report options. For Arcade writes, use `apply` with
`--arcade-layout`, the Arcade root/gamelist, and its existing sibling `media`.

## Reports and exit codes

JSON includes roots, policy, artwork preferences, compatibility, one row per
ROM/role, summary counts, and XML diagnostics. Resolved rows include source,
destination, mapping field/type, fallback status, match basis, operation, reason,
existing candidates, and cache action. Apply adds per-row results and verified
file counts. Plan counts are rows, not necessarily unique files.

- Exit 0: completed; missing artwork may still be reported and skipped.
- Exit 1: conflicts, unsupported/invalid inputs, diagnostics, or apply failures.
- Exit 2: invalid invocation, unreadable XML, or failed deployment preflight.

For journal semantics and partial-write recovery, see
[Recovery and troubleshooting](RECOVERY.md).
