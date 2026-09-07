# Recovery and troubleshooting

## Installation and upgrades

The release installer verifies its embedded runtime before replacing the
installed package and launcher. It preserves reports, journals, artwork choices,
and per-library corrections. Installation does not change game artwork.

Keep the previous verified installer until the new menu opens. If installation
is interrupted, exit MediaPrep and rerun the same installer. If the installer
reports a busy file, finish copying it, disconnect the host SMB share, and run
the installer locally from MiSTer again.

## Interrupted artwork updates

Updates are not whole-library transactions. Each output is staged, flushed,
verified, and published individually. Successfully completed rows remain applied
if a later row fails or the process stops.

Controller updates save reports and journals under
`Scripts/.config/mister-mediaprep/runs`. Restore the original mount paths, close
Console Mode, pause other writers, and choose **Resume interrupted update**.
Resume keeps the original selection, Fill missing/Replace policy, artwork
choices, and correction state while rescanning current inputs.

For the CLI, start a long update with a new journal outside protected folders:

```sh
./mister-mediaprep apply ... --journal /safe/path/job.jsonl
```

Resume with the same roots and options but omit the original `--rom` selections:

```sh
./mister-mediaprep apply ... --resume /safe/path/job.jsonl
```

Journals reject existing files and protected locations, flush each result, and
tolerate a truncated final record. They are tied to their original paths and
options; they are not portable saved deployment plans.

## Changed or partial outputs

Resume verifies current source and destination state before continuing. A raw
file already published with matching bytes can be reused while targeted cleanup
finishes. A changed or partial output stops for review and is not overwritten or
silently accepted. After identifying the correct source, use a selected Replace
preview and update to repair that game.

On filesystems without no-overwrite rename, a new destination uses an exclusive
verified copy. A crash can leave a partial new file. Existing-destination
replacement uses atomic rename where supported. Only after raw output verifies
does MediaPrep remove superseded raw aliases and affected same-stem optimized
caches.

## Common problems

| Symptom | Action |
| --- | --- |
| No library listed | Confirm the drive is mounted and the folder/extensions are configured in Console Mode. Check for `gamelist.xml`, ROMs, and mapped images; run Library health. |
| Game absent from Console Mode | Check Console Mode's configured extensions and database. MediaPrep does not add games or rebuild the frontend database. |
| Missing artwork reported | Finish scraping or correct the exact XML path. Missing roles are skipped without deleting unrelated artwork. |
| Conflict or invalid source | Read the exact path and reason, resolve ambiguity or damage, then preview again. Do not guess between regions or versions. |
| Changed artwork choice had no effect | Use Replace. Fill missing preserves existing prepared images. |
| Console Mode still running | Close it before Apply, Resume, or correction changes; pause other artwork writers too. |
| Update interrupted | Restore the original mount path and Resume. If a changed/partial output is rejected, review a selected Replace repair. |
| Artwork looks stale | Reopen Console Mode. Replace invalidates affected caches; do not purge unrelated caches. |
| Artwork and games disappear after boot | Ensure the game drive is mounted before opening Console Mode. This can be startup timing rather than deleted content. |
| Installer reports a busy file | Exit MediaPrep, complete the copy, disconnect SMB, and rerun the installer locally. |
| Unsupported frontend binary | Do not bypass the check for a library update. Install the exact supported Console Mode binary listed in Compatibility. |
| Imported gamelist mapping rejected | Remove or stop using `ConsoleMode/caches/gamelist_art.tsv` for this deployment; MediaPrep does not write against imported mappings. |
| Unsupported playlist or descriptor | Select supported ROM entries explicitly, or use the controller's filtered library selection. MRA requires Arcade layout. |

## Safety boundaries

- Header signatures identify PNG/JPEG output extensions but do not fully decode
  images or prove frontend compatibility.
- XML must be UTF-8, at most 16 MiB, and contain no DTD/entity declarations.
  Parent traversal and symlinks are rejected.
- Case/Unicode aliases, alternate extensions, duplicate stems, source aliases,
  and main/background collisions are preflighted. Ambiguity never selects an
  arbitrary candidate.
- Source artwork, ROMs, XML, and saved corrections are protected. There is no
  general cleanup or whole-library backup.
- “Unreferenced source image” is a library-XML observation, not proof that a file
  is safe to delete.

Reports may contain local paths and game names. Review and redact them before
sharing. Do not attach ROMs, scraped commercial artwork, credentials, or complete
private library reports.
