# Controller usage

The MiSTer menu is the normal way to use MediaPrep without a keyboard. Install
the release as described in the [README](../README.md), then open
**Scripts → mister-mediaprep.sh**.

## Library discovery

MediaPrep reads system folder aliases and file extensions from Console Mode's
`themeconfig/section_groups/*.ini`; it does not contain a fixed core list. It
discovers configured libraries under `/media/fat/games` and
`/media/usb0/games` through `/media/usb5/games`.

A library needs existing ROMs, `gamelist.xml`, a `media` directory, and usable
mapped PNG/JPEG artwork. Empty core folders are ignored. **View skipped
libraries / entries** explains missing mappings, hidden file types, and
unsupported layouts. Detailed discovery results are saved under
`Scripts/.config/mister-mediaprep/runs`.

## Preview and update

**Preview** asks whether to plan Fill missing or Replace, then lets you select
systems. It validates source images and the complete output namespace without
changing artwork.

- **Fill missing artwork** adds absent raw images and preserves existing ones.
- **Replace existing artwork** refreshes existing images and invalidates their
  affected same-stem optimized caches.
- **Resume interrupted update** rechecks and continues a saved update with its
  original systems, policy, artwork choices, paths, and corrections.

Close Console Mode and pause other artwork writers before either update, Resume,
or correction changes. Each update repeats preflight before writing. Reopen
Console Mode after completion.

## Artwork choices

Use **Artwork choices** to select 2D or 3D boxes and screenshots or title
screens. Save makes the choices persistent; Back discards changes. If a game's
preferred type is missing, MediaPrep tries the other supported type for that
role and reports the fallback.

Choices affect future previews and updates. Fill missing still preserves any
prepared image already present. Use Replace when changing the style of existing
artwork. Logos remain source material rather than a separate Console Mode slot.

## Saved game corrections

Use **Game corrections** after checking a game's artwork. You can save its
current main image, background, or both, or browse the library's source folders
and choose an exact PNG/JPEG file. The picker lists filenames without image
previews and excludes optimized caches, hidden entries, and symlinks.

Each library stores independent snapshots in
`.mediaprep-corrections/corrections.json`. Corrections use exact ROM paths and
take priority over scraper mappings, so later scrapes cannot silently change the
saved choice. Saving one role leaves the other role intact. Similar titles,
regions, and filenames are never guessed.

Saving or removing a correction changes future source selection; it does not
change displayed artwork. Run Replace afterward to restore the saved image over
an existing wrong output. **Use scraped artwork again** removes a correction.
Keep the correction folder when backing up or moving the library.

## Library health and source inventory

**Library health (read-only)** compares on-disk ROMs with the gamelist and
reports absent entries, stale XML paths, missing mappings/files, invalid images,
filename conflicts, and excluded layouts. It also reports source images by
category and lists images unreferenced by that library's XML.

“Unreferenced” does not mean “safe to delete.” Another program or library may
still use the file. Missing XML and unsafe or ambiguous references are reported
as unverified instead. The inventory reads filenames and logical sizes; it does
not decode images, delete files, or offer cleanup.

## Arcade artwork

Use **Arcade artwork** for `_Arcade`. MediaPrep finds Arcade roots beside SD/USB
`games` directories, reads the adjacent `gamelist.xml`, and inventories root and
`_alternatives` MRA files. `_Organized`, `media`, `cores`, hidden metadata, and
symlink directories are excluded.

Preview reports mapped artwork, absent gamelist entries, stale paths, missing or
invalid images, and output conflicts. It never opens MRA contents or ROM ZIPs.
After review, choose Apply. Console Mode must be closed. Arcade output goes to
the `media` directory beside `_Arcade`, using each MRA filename stem and `-BG`
for backgrounds.

An MRA absent from the gamelist stays missing; no title or set-name match is
guessed. Selecting one game does not hide output conflicts with other discovered
MRAs. Arcade Resume uses the original MRA selection and artwork choices.

## Unsupported layouts

Playlists (`.m3u`, `.m3u8`), disc descriptors (`.cue`, `.ccd`, `.mds`), launch
definitions (`.mgl`), XML folder artwork, and nested gamelist merging are not
ordinary deployable entries. The controller filters them. An unfiltered CLI
apply containing unsupported entries stops before writing; use explicit ROM
selection or the dedicated Arcade layout as appropriate.

## Optimization

MediaPrep prepares raw artwork. Console Mode can create thumbnails automatically
and provides an optional **Optimize Artwork** action. You can run that action or
leave the prepared artwork as is. Already optimized libraries do not need
another full pass merely to use MediaPrep.

Native cache generation inside MediaPrep is deferred because it would require
additional image dependencies. Normal Preview and Apply remain dependency-free
and still invalidate affected stale caches.

For update recovery and common failures, see
[Recovery and troubleshooting](RECOVERY.md). For command-line operation, see the
[CLI reference](CLI.md).
