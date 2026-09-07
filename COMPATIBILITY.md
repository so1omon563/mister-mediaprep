# Compatibility

## Supported environment

MediaPrep requires Python 3.9 or newer and no third-party Python packages.
The controller menu runs on MiSTer using its existing controller mapping and
`dialog`. The portable CLI is tested on macOS and Linux; Windows is not a
verified deployment platform.

Artwork writes require Console Mode 1.1.3 with this binary SHA-256:

```text
c228525325c1e5d708d621ba086fb49a732cf864dbd2bba98040fcfbbf5e5ccc
```

The version name alone is insufficient: a different binary fails preflight.
Imported `caches/gamelist_art.tsv` mappings are not supported by the writer.
System folder aliases and file extensions come from the installed frontend's
`themeconfig/section_groups/*.ini`, not a fixed MediaPrep core list.

## Artwork layouts

| Library | Prepared output | Coverage |
| --- | --- | --- |
| Configured console/computer system | `games/SYSTEM/media/ROM-stem.png` or `.jpg`; `ROM-stem-BG` for background | Cartridge files, CHDs, and archive-file mappings use the same path rules |
| Nested game/disc files | System-root `media`, using each file's stem | Multi-disc CHD samples verified; identical stems still require collision checks |
| Arcade | `media` beside `_Arcade`, using each MRA's stem and `-BG` | Root and `_alternatives` MRAs; `_Organized` aliases excluded |

PNG/JPEG signatures determine raw output extensions. A JPEG named `.png`
is copied as `.jpg`; its bytes and source are not converted. Header recognition
is not a full image-decoding guarantee. Main artwork and background are separate
roles. Unused image styles and logos remain untouched.

Playlists, disc descriptors, launch definitions, XML folder mappings and nested
gamelist merging are unsupported. Archive contents are not traversed. Explicit
selection does not bypass full-library collision and source-protection checks.
No approximate game matching is performed.

## Verification evidence

The 0.1.3-dev runtime has passed the focused suite on native MiSTer Python 3.9.6
and macOS. Linux permits the case-sensitive fixture that macOS skips. Packaging
checks exercise deterministic builds, embedded installation, CLI execution and
preservation of existing application state. Hosted CI is a separate release
check; local results do not establish that it has run.

Device artwork and launch spot checks cover PSX, Saturn, NES (including FDS),
SNES, MegaDrive, MegaCD, Neo Geo, Atari 2600/5200/7800, Jaguar, N64, Neo Geo CD,
32X, Master System, TurboGrafx-16/CD, Game Boy, Game Boy Color, Game Gear,
Atari Lynx, Vectrex and Arcade root/alternate entries. These demonstrate common
layout compatibility, not exhaustive testing of every title or available core.

The installed 0.1.3-dev controller checks verified navigation, health reporting,
source preference persistence and fallback, Fill missing preservation, Arcade
Apply, saved corrections and a selected Replace run resumed after a controlled
stop before writes. Automated native tests separately exercise partial-write
failures. File hashes and user display/launch observations are distinct evidence.

## Preservation and recovery limits

Sources, ROMs, gamelists and saved corrections are protected. Only verified raw
outputs and their affected same-stem caches are changed. Fill missing preserves
existing raw artwork; Replace can invalidate its cache even when raw bytes are
already identical, because equality does not establish cache freshness.

Updates are not whole-library transactions. Completed rows remain applied after
a later failure. Atomic replacement is used where supported; the exclusive-copy
fallback on some network filesystems can leave a partial new file after a crash.
Resume verifies current state before continuing. Changed or partial outputs may
require a reviewed selected Replace repair. Keep competing writers stopped.

## Optimization

MediaPrep prepares raw artwork. Console Mode can create thumbnails automatically
and provides its own optional Optimize Artwork action. Native MediaPrep cache
generation is deferred. Existing optimized libraries do not require another full
pass to use this tool. Normal Apply still invalidates affected stale caches.

## Source inventory

Health reports and optional scan/Arcade previews inventory source paths and
logical sizes without image decoding or deletion. Full XML context survives
selected-game filtering and excluded game entries. Synthetic checks cover shared
canonical images, unused alternatives, identical basenames in different folders,
hard links, saved corrections, retained snapshots, raw/cache separation, path
remapping, overrides, hidden referenced files and unsafe/unresolved paths.
