# MiSTer MediaPrep

Prepare your artwork for MiSTer Console Mode.

A dependency-free Python CLI for preparing existing artwork, on the MiSTer itself or on a desktop. `scan` reports a plan without writes; `apply` verifies and publishes images with optional replacement and targeted cache invalidation.

## Run directly on MiSTer

Requires Python 3.9 or newer, MiSTer's standard `dialog` utility, and Console Mode 1.1.3. The native menu works with controllers already mapped by MiSTer. No additional Python packages are required. See [Compatibility](COMPATIBILITY.md) for the verified limits.

Build the self-contained installer from this source checkout:

```sh
python3 -B build.py --release-dir dist
```

Copy `dist/mister-mediaprep-install.sh` to MiSTer's `/media/fat/Scripts` folder. Exit MediaPrep if it is running, then run **Scripts → mister-mediaprep-install.sh**. Wait for `Installer exit: 0`. Open **Scripts → mister-mediaprep.sh**; its title shows the installed version. Installation changes the application and launcher, not game artwork.

### First controller update

1. Finish scraping first. Each system needs ROMs, `gamelist.xml`, and the mapped images. MediaPrep prepares existing artwork; it does not scrape it.
2. Close Console Mode and pause other artwork writers before an update.
3. Open **Artwork choices** if you want 3D boxes or title screens, then Save. The default is 2D boxes and screenshots, with fallback when the preferred type is absent.
4. Open **Preview**, choose Fill missing or Replace, toggle the desired systems, then Continue. Review added/replaced/kept counts, missing roles, and fallbacks. Preview changes no artwork.
5. Open **Fill missing artwork** to preserve current images, or **Replace existing artwork** to refresh them. Select the same systems. Each update repeats preflight before writing.
6. Wait for the completion screen, then reopen Console Mode and check artwork and launching. For Arcade, use **Arcade artwork**, review its preview, then choose Apply.

D-pad moves; your mapped Select and Back buttons choose or return. No keyboard entry is needed. **Library health (read-only)** explains absent mappings and excluded paths. See [Recovery and troubleshooting](#recovery-and-troubleshooting) if an update stops.

The launcher also accepts the CLI arguments shown below over SSH or from the F9 terminal. Use native `/media/usb*/games/...` paths instead of macOS `/Volumes/...` paths; pass `--help` for CLI help. Native operation does not require an SMB connection.

## Run on a desktop

Python 3.9 or newer, with no third-party dependencies or installation required:

```sh
./mister-mediaprep scan \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media
```

Alternatively use `python3 -B mister_mediaprep.py scan ...`. For JSON, add `--format json`. Reports go to stdout; redirect them **outside the media library**, for example to `/tmp/psx-scan.json`. An absent output directory is allowed and is never created. The gamelist and ROM root must exist; mount the library first.

The scanner prefers `boxart2d` and `screenshot` by default. Choose `boxart3d` or `titlescreen` with the options below; when a mapping or its file is missing, it tries the other type for that role. Saved game corrections take priority over XML mappings, source preferences, and folder overrides. Otherwise a game's own mappings take priority over its shared canonical `parentid`, including when the game uses the fallback type. Invalid or ambiguous source files are reported rather than hidden by fallback. The output proposal uses the actual ROM stem for box artwork and adds `-BG` for backgrounds. Existing artwork is preserved by default; add `--replace` to plan replacement instead. Conflicts and missing inputs are reported independently per role.

The scanner also accepts `image` and `thumbnail` tags when their path contains exactly one recognized artwork folder type: `box2d`/`boxart2d`, `box3d`/`boxart3d`, `screenshot`, or `titlescreen` (case-insensitive). Explicit type tags take precedence over generic tags of the same type. Generic mappings still follow your type preferences and direct-game-before-parent precedence. Multiple generic paths for one type are ambiguous; paths without a clear type are reported as ambiguous for roles without explicit mappings. No image type is guessed from its filename. JSON records both the original `artwork_field` and inferred `artwork_type`. All referenced generic images remain protected inputs, including unused ones.

Optional inputs:

| Option | Meaning |
| --- | --- |
| `--box-source boxart2d\|boxart3d` | Preferred box type; default `boxart2d`, with the other type as fallback |
| `--background-source screenshot\|titlescreen` | Preferred background type; default `screenshot`, with the other type as fallback |
| `--box-dir PATH` | Select box images by the mapped artwork filename in this folder |
| `--background-dir PATH` | Select backgrounds by the mapped artwork filename in this folder |
| `--map-root OLD NEW` | Remap a moved absolute path prefix; repeat for different roots |
| `--format text\|json` | Choose report output; default is text |
| `--replace` | Plan replacement of existing artwork; still read-only |
| `--rom PATH` | Limit report to this ROM relative to `--rom-root`; repeat for more |
| `--consolemode-root PATH` | Installed ConsoleMode directory; required for apply, optional for scan preflight |
| `--quiet` | Suppress progress on stderr; JSON stdout stays machine-readable |
| `--journal PATH` | Apply only: create a new progress journal outside the input/frontend directories |
| `--resume PATH` | Apply only: resume the journal with the original paths/options, omitting `--rom` |
| `--experimental-layout` | Explicit `--rom` device tests of unconfigured file types |
| `--arcade-layout` | Scan/apply MRA artwork to the verified `media` folder beside the ROM root; full-library and selected-game updates are supported |

Example Windows path remapping: `--map-root 'C:/Games/PSX' /Volumes/usb1/games/PSX`. Slashes are normalized in XML paths; exact spelling wins, with only unique case/Unicode fallback. Explicit folder overrides do not perform title matching.

JSON includes roots, policy, artwork preferences, compatibility status, one row per ROM/role, summary counts, and separate XML diagnostics. Each resolved row includes the selected `artwork_field`, an `artwork_fallback` flag, source, destination, match basis, existing candidates, operation, reason, and cache action. `operation` and `summary` describe the plan; apply additionally reports each row's `result` and the number of verified `applied_files`. Multiple rows can share a byte-identical destination; plan counts describe rows, not unique files. Exit codes are 0 for completion (missing artwork is allowed), 1 for scan conflicts or apply failures, and 2 for invalid invocation or failed deployment preflight.

## Deploy

On MiSTer, open **Scripts → mister-mediaprep.sh**. The menu reads `romDirs` and `romExts` from Console Mode's `themeconfig/section_groups/*.ini`, including folder aliases and wildcard extensions. It discovers matching libraries under `/media/fat/games` and `/media/usb0/games` through `/media/usb5/games`. A library needs `gamelist.xml`, existing ROMs, a `media` directory, and usable mapped PNG/JPEG artwork. New configured systems do not require a MediaPrep code update; empty core folders are ignored.

The menu offers **Preview**, **Fill missing artwork**, **Replace existing artwork**, and **Resume interrupted update**. Preview asks which update to plan. Fill missing keeps existing raw images and their optimized caches; it adds only absent raw artwork. Use Replace to refresh existing images or change their style. Then toggle systems with your mapped Select button and choose Continue, or choose all. Preview reports how many images will be added, replaced, or kept, plus missing source roles and fallbacks.

**View skipped libraries / entries** explains missing mappings, hidden file types, and unsupported layouts. Detailed paths and reasons are saved in `Scripts/.config/mister-mediaprep/runs/discovery.json`. Discovery checks for usable library artwork; Preview and both update actions validate every selected image and the complete output namespace before writes. A new raw image may invalidate a stale same-name cache even during Fill missing; caches belonging to preserved raw images stay intact.

Use **Artwork choices** to set source preferences. Select Box or Background to toggle its preferred type, then Save. Choices persist in `Scripts/.config/mister-mediaprep/artwork.json` for subsequent previews and updates; Back discards changes. They apply to whichever systems you select next. Preview reports fallback counts, and each update saves its own choices so Resume keeps them even if your current preferences change. This selects existing mapped images; scrape the desired types first. Logos remain source material rather than a new Console Mode display slot.

D-pad navigation, Select, Back, completion screens, and skipped-entry reports use MiSTer's existing controller mapping and standard `dialog` utility. No keyboard text entry is required. Close Console Mode before either update action, Resume, or saving/removing corrections. Restart it after an artwork update.

Both update actions preflight every selected system before writing. Reports, frozen selections, and resume journals are retained under `Scripts/.config/mister-mediaprep/runs`. Resume keeps the original fill-missing/replacement policy and artwork choices, requires the original library mount paths and saved corrections, and uses fresh scans; completed runs are hidden from its list. If corrections change, start a new Preview/update. Earlier menu selections retain their replacement policy. The menu does not need a manually prepared manifest or a Mac. CLI arguments passed to the shell launcher still run the CLI directly.

The runtime is packaged in one `mister-mediaprep.pyz`. The self-contained installer verifies the package and launcher and preserves reports, journals, preferences, and library corrections. It returns after five seconds. Keep the previous verified installer for rollback; rerun the same installer after an interrupted installation. You can remove the installer from Scripts after verifying the menu opens.

## Keep a game's corrected artwork

Use **Game corrections** to preserve artwork you have checked. After visually checking a game's current artwork, choose its system and exact ROM entry, then keep the main image, background, or both. This saves independent copies of the prepared raw images. To correct a wrong or missing mapping, choose **Choose source for main image** or **Choose source for background**, browse the library’s `media` folders, and select an exact PNG/JPEG filename. The text menu lists filenames without rendering image previews; it does not download or guess artwork. Optimized caches, hidden entries and symlinks are excluded from the picker. Back cancels. Saving one role leaves any saved correction for the other role intact.

Each library keeps its own `.mediaprep-corrections/corrections.json` and image snapshots, outside the scraper's `media` files. Entries use exact ROM paths relative to that library. The corrections travel with the library across mount changes and are automatically used by Preview, Fill missing, and Replace. Fill missing still preserves existing raw artwork; use Replace to restore saved corrections over incorrect existing outputs. The game must still have a gamelist entry, but it can be selected for a correction even when that entry has no usable artwork mapping. Renamed ROMs need their correction saved again; similar titles, regions, and filenames in other directories are never guessed.

Choose **Use scraped artwork again** to remove that game's correction. This changes its future source selection; run Replace afterward to refresh displayed artwork. Saving/removing a correction leaves current raw images, optimized caches, ROMs, and gamelist untouched. Unreferenced snapshots are retained; there is no automatic cleanup. Keep the correction folder when copying or backing up a library.

The equivalent CLI actions are:

```sh
./mister-mediaprep correction --rom-root /media/usb1/games/NES \
  --rom 'Super Mario Bros. 2 (Japan) (En).fds' --keep both

./mister-mediaprep correction --rom-root /media/usb1/games/NES \
  --rom 'Super Mario Bros. 2 (Japan) (En).fds' --remove
```

Choose an existing source explicitly through the CLI:

```sh
./mister-mediaprep correction --rom-root /media/usb0/games/NES \
  --rom 'Game (Japan).nes' --source box 'media/box2d/Chosen image.png'
```

Use `--source background PATH` for the other role. Paths must stay inside the
library and must identify original PNG/JPEG artwork, not optimized caches.
Saving one role preserves the other role's correction. The source is copied
into the correction store and verified; future scraper changes to its original
path do not change that saved choice. Preview first, then run Replace to change
existing displayed artwork. Fill missing continues to preserve current images.

`--keep box` and `--keep background` select one role. Stop other artwork writers before saving. Snapshot hashes and PNG/JPEG signatures are checked on each read. Missing, changed, linked, or malformed saved images stop the operation instead of falling back to potentially incorrect scraped art. The manifest is published only after its new image copies verify. Preview counts saved correction roles; JSON marks those rows with `artwork_field: "correction"`. Existing namespace collisions and protected-input checks still apply.

## Library health

The menu includes **Library health (read-only)**. Choose a configured
library, including one without `gamelist.xml` or a media directory. The report
compares on-disk ROMs with XML using Console Mode's directory and extension
definitions. It distinguishes absent game entries, stale XML paths, missing
artwork mappings/files, invalid images, filename conflicts and excluded layouts.
Browse individual reasons using the same controller report browser as Arcade.

```sh
./mister-mediaprep health --rom-root /media/usb0/games/VECTREX \
  --consolemode-root /media/fat/ConsoleMode --format json
```

For an Arcade root, add `--arcade-layout`. JSON includes `health.game_count`
for eligible games on disk and `health.artwork_role_count` for main/background
rows, including stale XML entries. Those are different counts. Reports from the
menu are saved under `Scripts/.config/mister-mediaprep/runs/health-*`.
CLI reports go to stdout; redirect them outside the game library if needed.
No ROMs, XML, artwork or caches are changed, and health reports cannot be applied.

Health also includes a **Source inventory** and **Unreferenced source images**
list with paths and logical byte sizes. It examines the full gamelist, media
folders, correction snapshots, referenced source folders and explicit overrides.
All XML image references are retained, including unchosen styles, canonical
parents, logos and excluded entries. Prepared raw outputs, optimized caches,
active corrections, retained snapshots and hard links to referenced images have
separate categories. No files are deleted or offered for automatic cleanup.

Unreferenced means a PNG/JPEG filename has no resolved reference in this library's
XML; it is not proof that another program or library does not use it. Missing XML
or ambiguous/unsafe references produce unverified candidates instead. Hidden and
symlink directories are excluded from traversal; explicitly referenced regular
files are still accounted for. Sizes count paths, including hard links, rather
than deduplicated disk usage. Excluded paths have unmeasured sizes. This filename
inventory does not decode images or establish image integrity.

For a selected-game scan, add `--source-inventory` to include the same full-library
accounting. It is also available on `arcade-preview`, and is not an Apply option.
JSON stores `source_inventory.entries`, category `summary` counts/sizes, inspected
`roots`, and `unresolved_references`. Normal scans avoid the extra inventory work.

Missing artwork remains a reported condition; invalid/unsupported inputs and
diagnostics return exit 1, while an invalid invocation or unreadable XML returns 2.

## Arcade artwork

Use **Arcade artwork** in the controller menu or the read-only `arcade-preview` CLI. The menu finds `_Arcade` folders beside the SD/USB `games` roots, lets you preview Fill missing or Replace using your artwork choices, and provides browsable missing/conflicting entries, all artwork rows, and excluded paths. Preview reports are saved under `Scripts/.config/mister-mediaprep/runs/arcade-preview-*/arcade.json`. Back cancels without creating an update journal.

After reviewing the preview, choose **Apply** to update that Arcade library. Console Mode must be closed. The updater repeats preflight before writing and saves the MRA selection, policy and source choices under `runs/arcade-update-*`. **Resume interrupted update** uses those original choices, even if you later change Artwork choices. Restore the original mount path before resuming. Root and `_alternatives` MRAs are included; excluded folders remain excluded. Updates use the existing writer, input protections and targeted cache invalidation.

```sh
./mister-mediaprep arcade-preview \
  --arcade-root /Volumes/sdcard/_Arcade \
  --replace --format json > /tmp/arcade-preview.json
```

The command reads the adjacent `gamelist.xml` and recursively inventories MRA filenames, including `_alternatives`. It excludes `_Organized`, `media`, `cores`, hidden metadata, and symlink directories. MRA symlinks are excluded from discovery; gamelist references through symlinks still report path errors. It resolves exact direct/canonical artwork mappings through the existing scanner. It does not open MRA contents or ROM ZIPs, so the ROM drive need not be mounted for these mappings.

An on-disk MRA absent from the gamelist receives explicit missing-entry rows; no title or set-name matching is attempted. The report also identifies stale gamelist paths, missing image mappings/files, invalid images, source aliases, and incompatible output names. An unmapped alternate can block a mapped game's proposed output if they share a filename stem. Byte-identical shared mappings retain the scanner's existing sharing behavior. Sources from excluded XML entries remain protected inputs.

The output is the `media` folder **beside** the Arcade root, using each MRA filename stem and `-BG` for its background. Main artwork, backgrounds, and launching were verified on Console Mode 1.1.3 for root Aliens (World set 1) and alternate 1942 (Revision A). The preview never creates that folder, writes artwork, inspects/removes caches, or changes MRAs, XML, or scraper images. The existing `scan`/`apply` commands now support `--arcade-layout` with the Arcade root/gamelist and an existing sibling `media` output, without an experimental override. Omit `--rom` for the full library or supply exact selections. This reuses the full MRA inventory, collision checks, writer, and journals; selecting a game does not hide conflicting unselected MRAs. On MiSTer, Arcade apply refuses to write while Console Mode is running. The controller Apply action uses this same deployment path.

`--box-source`, `--background-source`, folder overrides, `--map-root`, `--replace`, `--rom`, `--quiet`, and `--format` are available. A selected `--rom` is relative to `--arcade-root`; collision checks still include other discovered MRAs. For XML copied from native paths, use an explicit remap such as `--map-root /media/fat /Volumes/sdcard`. The gamelist must exist and parse successfully; an unfinished scraper write may require retrying after the scrape completes.

JSON adds `read_only: true` and an `arcade` inventory containing MRA paths, mapped counts, absent gamelist entries, stale paths, and exclusions. Each row indicates whether its gamelist mapping is present or absent. Plan counts describe artwork-role rows, including stale MRA paths; they are not unique game counts. Exit 0 permits missing artwork, exit 1 retains a complete report with conflicts/errors, and exit 2 indicates a failed scan or invalid invocation. The controller displays exit-1 reports for inspection.

## Selected-game deployment

First review the exact deployment plan:

```sh
./mister-mediaprep scan \
  --gamelist /Volumes/usb0/games/PSX/gamelist.xml \
  --rom-root /Volumes/usb0/games/PSX \
  --output /Volumes/usb0/games/PSX/media \
  --consolemode-root /Volumes/sdcard/ConsoleMode \
  --rom 'Aces of the Air (USA).chd' \
  --rom 'Alien Resurrection (USA).chd' \
  --replace --format json > /tmp/psx-deployment.json
```

Quit Console Mode and pause other artwork writers before deployment. Run the same command with `apply` instead of `scan`; use a different report filename. Add repeatable `--rom` selections to limit scope. Apply resolves the current gamelist and collision namespace; it never executes a saved report. Selected-game scans resolve all inputs for protection but avoid reading unrelated scraped image contents; saved correction snapshots are always hash-checked. Restart Console Mode afterward so it can load the new artwork and recreate affected optimized files. No image optimizer or conversion dependency is used.

Writes require an existing `system/media` folder, the verified Console Mode 1.1.3 binary, and no imported gamelist-art TSV. Normal apply reads the same frontend folder/extension definitions as discovery, then uses the shared per-ROM filename layout. Device display checks cover 17 systems across cartridge, CHD, and Neo Geo ZIP entries; this supports the common layout, not a claim that every core has been individually tested. Sources and ROMs are never converted. Existing images are preserved unless `--replace` is supplied.

Nested ROM entries use their filename stem for artwork in the system-root `media` directory. The FFVII device test confirmed Disc 1 artwork also updates its directory image; no separate folder image is generated. Playlists (`.m3u`, `.m3u8`), disc descriptors (`.cue`, `.ccd`, `.mds`), launch definitions (`.mgl`), and XML folder-art mappings remain unsupported, including in experimental mode. MRA files require `--arcade-layout` as described above. An unfiltered apply of a mixed library containing unsupported layouts stops before writing; use explicit `--rom` selections or the menu's filtered selection. Archive contents are not traversed; ZIP mappings refer to the archive file itself.

Apply first compares existing destination bytes with the source. Byte-identical raw files retain their timestamps and are not copied again. Under `--replace`, superseded raw aliases and same-stem optimized caches are still cleaned because raw equality cannot establish optimized-cache freshness. Otherwise the source is copied to a temporary file in the output directory, flushed and hash-verified. Publication uses atomic rename where supported. If a filesystem rejects no-overwrite rename, new destinations use exclusive creation followed by a second verified copy. This SMB fallback never overwrites an existing destination, but interruption can leave a partial new file; quit other artwork writers before apply, and rescan with `--replace` after interruption. Existing-destination replacements still use atomic rename. Only after the new output verifies are superseded raw extensions and matching optimized PNG/JPEG files removed. Source artwork, ROMs and XML are never modified. No general cleanup or full-library backup is performed.

Apply stops on the first failure and reports partial results. `error` means that row did not complete; `raw_applied_cleanup_failed` or `raw_applied_cache_failed` means the new raw file is present but cleanup needs attention. Inspect the report and rescan with `--replace` to retry. Earlier successful rows remain applied. This is not a whole-library transaction; interruption or loss of a network share can require a rescan. Keep other writers stopped: filesystem operations across different extensions cannot form one atomic transaction.

## Progress and retry

Progress is written to stderr during scanning, preflight, and application. The JSON plan still reports copy/replace intentions; actual `copied_files` and `unchanged_files` distinguish writes from reuse. A row can finish as `unchanged` when its bytes already match and there is no cleanup left. `applied_files` counts successfully processed unique destinations, including verified reuse.

For a long run, add `--journal /path/outside/library/job.jsonl`. Resume using `--resume` with the same roots, overrides, replacement policy, and experimental-layout option, omitting the original `--rom` selections. The journal selects unfinished games; the CLI rescans those games against the whole current namespace before writing. It also retries completed entries whose source/output metadata changed. A changed gamelist causes the original selection to be rechecked. Both artwork roles of an unfinished game are reconsidered, with identical bytes reused.

If a fill-missing copy published its raw image but failed during cleanup, Resume verifies that the current paths and bytes still match before finishing targeted cleanup. The raw image is reused without rewriting it. A changed or partial output stops with a review message; it is neither overwritten nor silently marked complete. Such a file needs a reviewed, selected `--replace` repair. Existing images that were preserved by the original run remain preserved.

Journals refuse to overwrite existing files, reject input-directory locations, flush each result, and tolerate a truncated final record. A power loss can lose buffered records; those outputs are rechecked on retry. An unreadable header requires a fresh scan/run. Journals are tied to the original paths and options, not portable saved deployment plans.

## Boundaries

- PNG/JPEG signatures choose destination extensions. A JPEG stored as `.png` is proposed as `.jpg`, without conversion or source changes. Header checks do not prove full image integrity or frontend compatibility.
- XML must be UTF-8, at most 16 MiB, without DTD/entity declarations. Symlinks and parent traversal are rejected. Reads are bounded to explicit roots. Apply rechecks file state and pins directory handles before mutation.
- Logos are preserved. Explicit folder artwork mappings, CSV/folder-only sets, approximate matching and conversion are not implemented. Arcade deployment is supported through its dedicated layout.
- Case/Unicode output aliases, alternate extensions, duplicate stems, source aliases and box/background collisions are preflighted. Ambiguity never selects an arbitrary candidate.

## Check

```sh
python3 -B -m unittest -v
```

Tests use synthetic temporary libraries, not bundled games or scraped artwork. Scan checks cover contents and file metadata; deployment checks exercise verification, preservation, collisions and recovery. See [Compatibility](COMPATIBILITY.md) for native and display evidence, and [Release checks](RELEASING.md) for maintainer packaging steps.

## Recovery and troubleshooting

| Symptom | Action |
| --- | --- |
| No library listed | Confirm its drive is mounted, its folder/extensions are configured in Console Mode, and `gamelist.xml`, ROMs and mapped images exist. Use Library health for missing entries. |
| Game absent from Console Mode | Check the frontend's configured extensions and database. MediaPrep does not edit frontend settings or rebuild its database. For example, FDS files require `.fds` in the NES definition. |
| Missing artwork reported | Finish scraping or correct the exact XML path. Missing roles are skipped; they do not cause unrelated artwork to be deleted. |
| Conflict or invalid source | Read the path and reason in the report. Resolve ambiguity or the damaged input, then preview again. Do not guess between region/version matches. |
| Console Mode still running | Close it before Apply, Resume or correction changes. Stop other artwork writers as well. |
| Update interrupted | Restore the original mount path and choose Resume interrupted update. Keep its reports and journals. If a changed/partial file is rejected, review a selected Replace repair. |
| Artwork looks stale | Reopen Console Mode after a successful update. Replace refreshes selected artwork and invalidates its affected caches; do not purge unrelated caches. |
| Artwork and games both disappear after boot | Confirm the game drive is mounted before opening Console Mode. This can be a mount/startup problem rather than missing artwork. |
| Installer reports a busy file | Exit MediaPrep, finish copying the installer, disconnect the host's SMB share, and run the installer again locally. |
| Unsupported frontend binary | Do not bypass the check for a whole library. The writer is verified against the specific 1.1.3 binary in Compatibility. |

Reports may contain your local paths and game names. Review and redact them before sharing publicly. Do not attach ROMs, scraped commercial artwork, credentials, or complete private library reports.

## Optimization

Optimization is optional. The current script prepares raw artwork; you can use Console Mode's Optimize Artwork action or skip a full optimization pass. A full pass is optional; already optimized libraries do not need another pass just to use MediaPrep.

Native MediaPrep cache generation is deferred because it requires additional image dependencies. Use Console Mode's existing Optimize Artwork action, or leave the prepared artwork as is. Normal Preview/Apply remains dependency-free. Skipping a full optimization pass does not disable the frontend's own thumbnail behavior or the updater's targeted stale-cache invalidation.

## License

MIT. See [LICENSE](LICENSE). No games or scraped artwork are included.
