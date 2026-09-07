"""Read-only stable-release gates; shared GitHub actions create tags/releases."""
import argparse
import os
from pathlib import Path
import re
import subprocess

from mister_mediaprep import VERSION

STABLE = r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"


def plan(message, tags, version):
    title = message.splitlines()[0].lower() if message else ""
    markers = set(re.findall(r"(?<![a-z0-9])#([a-z][a-z0-9_-]*)", title))
    bumps = markers & {"patch", "minor", "major"}
    if not bumps:
        return dict(requested="false", publish="false", tag="")
    if len(bumps) != 1 or markers - {"patch", "minor", "major", "release"}:
        raise ValueError("Use one stable bump marker and optionally #release in the squash title")
    versions = [tuple(map(int, match.groups())) for tag in tags
                if (match := re.fullmatch(STABLE, tag.split("+", 1)[0]))]
    base = list(max(versions, default=(0, 0, 0)))
    index = {"major": 0, "minor": 1, "patch": 2}[bumps.pop()]
    base[index] += 1
    base[index + 1:] = [0] * (2 - index)
    tag = "v" + ".".join(map(str, base))
    if tag != "v" + version:
        raise ValueError("Stage VERSION %s before requesting this release" % tag[1:])
    return dict(requested="true", publish=str("release" in markers).lower(), tag=tag)


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def verify_tag(tag):
    if not re.fullmatch(STABLE, tag) or tag != "v" + VERSION:
        raise ValueError("Tag must match the staged stable VERSION")
    if git("rev-parse", "refs/tags/" + tag + "^{commit}") != git("rev-parse", "HEAD"):
        raise ValueError("Check out the existing release tag exactly; tags must never move")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", help="Verify an existing tag for release or recovery")
    args = parser.parse_args()
    if args.tag:
        verify_tag(args.tag)
    else:
        result = plan(git("log", "-1", "--pretty=%B"), git("tag", "--list").splitlines(), VERSION)
        text = "".join(name + "=" + value + "\n" for name, value in result.items())
        if os.environ.get("GITHUB_OUTPUT"):
            with Path(os.environ["GITHUB_OUTPUT"]).open("a") as stream:
                stream.write(text)
        print(text, end="")


if __name__ == "__main__":
    main()
