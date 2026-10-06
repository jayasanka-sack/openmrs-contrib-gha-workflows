"""Shared utilities for GitHub Actions inference scripts."""

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET


def strip_ns(root):
    """Remove XML namespace prefixes from all elements."""
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]


def parse_pom(path="pom.xml"):
    """Parse a POM file, strip namespaces, and return the root element.

    Returns None and prints a GitHub Actions warning if the file is missing
    or cannot be parsed.
    """
    if not os.path.isfile(path):
        print(
            f"::warning::No {path} found; skipping inference",
            file=sys.stderr,
        )
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as e:
        print(f"::warning::Failed to parse {path}: {e}", file=sys.stderr)
        return None
    strip_ns(root)
    return root


def write_github_outputs(outputs):
    """Write key=value pairs to GITHUB_OUTPUT or stdout.

    Args:
        outputs: dict of {key: value} pairs. None values are skipped.

    Values containing newlines use the heredoc delimiter syntax required
    by GitHub Actions for multiline outputs.
    """
    entries = []
    for key, value in outputs.items():
        if value is not None:
            if "\n" in str(value):
                entries.append(f"{key}<<EOF\n{value}\nEOF")
            else:
                entries.append(f"{key}={value}")
    if not entries:
        return
    out_file = os.environ.get("GITHUB_OUTPUT", "")
    if out_file:
        with open(out_file, "a") as f:
            f.write("\n".join(entries) + "\n")
    else:
        print("\n".join(entries))


def yarn_workspaces(extra_args=(), cwd=None):
    """Return yarn's workspace list as [{"location": ..., "name": ...}].

    Yarn is the authority on which workspaces exist, rather than globbing the
    `workspaces` field ourselves: it resolves nested workspaces and negated
    patterns the same way the publish and version commands will. Pass
    ("--no-private",) to drop workspaces marked private.

    Returns [] if yarn is unavailable or fails, so callers can degrade rather
    than abort.
    """
    try:
        result = subprocess.run(
            ["yarn", "workspaces", "list", "--json", *extra_args],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
        )
    except (OSError, subprocess.CalledProcessError) as e:
        print(f"::warning::Could not list yarn workspaces: {e}", file=sys.stderr)
        return []

    workspaces = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            workspaces.append(json.loads(line))
        except ValueError:
            print(
                f"::warning::Skipping unparseable workspace entry: {line}",
                file=sys.stderr,
            )
    return workspaces


def publishable_package_names(workspaces):
    """Names of the workspaces a publish command would actually push.

    The root workspace is dropped whenever other workspaces exist: the inferred
    publish command excludes it by name (`--exclude <root>`), and `--no-private`
    keeps it whenever the root is not marked private. A repo whose only
    workspace IS the root is a single-package repo, and there the root is the
    package being published, so it stays.
    """
    if len(workspaces) > 1:
        workspaces = [w for w in workspaces if w.get("location") != "."]
    return [w["name"] for w in workspaces if w.get("name")]
