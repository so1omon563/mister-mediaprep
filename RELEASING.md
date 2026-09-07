# Release checks

The MIT license is included in the runtime and embedded installer payload.
Local checks are not evidence of a hosted GitHub Actions run. Validate the
exact release revision in hosted CI before publication.

Build development assets with no runtime dependencies:

```sh
python3 -B -m unittest -q
python3 -B build.py --release-dir /tmp/mediaprep-dist
```

The directory contains `mister-mediaprep.pyz`, `mister-mediaprep.sh`,
`mister-mediaprep-install.sh`, and `SHA256SUMS`. ZIP members use fixed metadata
and stored bytes, so rebuilding identical source produces identical archives
without depending on a compression-library version. The build checks source
equality, installer payload equality, and launcher equality. Tests execute the
packaged CLI and embedded installer, check Python 3.9 syntax, and verify that
upgrades preserve saved state.

## Stage a stable version

Set `VERSION` in `mister_mediaprep.py` to the intended stable version, then use a
squash-merge title containing exactly one of `#patch`, `#minor`, or `#major`.
Add `#release` to publish assets as well as creating a tag. The staged version
must equal that bump from the highest existing stable version tag. With no
version tags the baseline is `0.0.0`; for example, `#minor` requires `0.1.0`.
Internal device build numbers do not establish Git tags. Choose the initial
public version during repository preparation rather than inventing old tags.

```sh
python3 -B release.py
python3 -B build.py --release-dir /tmp/mediaprep-dist --tag vX.Y.Z
```

The first command reads the current commit title and tags without writing Git
state. Ordinary merge titles, including marker examples in the body, request
neither tags nor releases. Other release modifiers and multiple bump markers
are rejected. Use squash merging so the reviewed PR title becomes the commit
title. Development versions cannot pass stable-release validation.

## GitHub workflow

`checks.yml` tests Python 3.9 and 3.14 on Linux and macOS. `release.yml` runs
those checks, validates the staged version and builds assets before calling
`so1omon563/custom-semver-bumper@v1`. It passes no branch-name fallback and
disables floating tags. The release job in the same workflow honors the
action's `skipped` and `should_release` outputs.

The release job checks out the exact new tag, verifies its version and commit,
runs tests, and rebuilds assets. `so1omon563/release-creator@v2` publishes them.
Its `tag-prefix: v` selects the previous published release for notes, so tag-only
versions do not omit changes from the next release. The workflow downloads and
compares all published assets with the local build before reporting success.

For a failed publication, dispatch the workflow with its **existing immutable
tag**. It never creates or moves a tag on this path. An existing complete release
is verified again. Missing or mismatched assets on an existing release fail
verification and require a reviewed repair; they are not silently declared
complete. Do not move tags to fix source errors; prepare a new version instead.

## Installation and recovery

Run the self-contained installer through MiSTer Scripts. It verifies its embedded
payload before replacing the runtime, publishes through temporary files, and
retains reports, journals, artwork preferences and library corrections. Keep the
previous verified installer until the upgrade has been checked. If installation
is interrupted, rerun the same installer; if a release needs rollback, run the
previous installer. Neither procedure removes library artwork or saved state.

The native runtime requires Python 3.9 or newer; actual MiSTer checks use 3.9.6.
The portable CLI has local macOS evidence. Hosted Linux/macOS CI remains pending
until a source repository exists. The 0.1.3-dev controller and display sample checks are recorded in
[Compatibility](COMPATIBILITY.md).

Native MediaPrep optimization is deferred and is not a release gate. The
supported workflow prepares raw artwork without image dependencies, then leaves
an optional Optimize Artwork pass to Console Mode. Skipping that pass retains
normal targeted stale-cache invalidation during artwork updates.

## Public source contents

`PUBLIC_FILES.txt` is the reviewed initial publication file list. Copy only those
files into the proposed repository; it includes runtime source, synthetic tests,
user/maintainer documentation, MIT licensing and the two workflows. Review any
addition before publishing. Historical investigations, private plans, generated
assets and device receipts are excluded and may be retained separately.

The runtime bundles only this project's Python modules and MIT license. It does
not vendor image libraries, games or scraped artwork. Keep device-specific paths,
credentials and reports out of examples and fixtures. Installation paths such as
`/media/fat/Scripts` describe the supported platform, not a private endpoint.
