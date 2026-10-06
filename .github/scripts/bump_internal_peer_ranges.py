#!/usr/bin/env python3
"""Retarget internal peerDependency ranges after a major version bump.

O3 monorepo packages peer-depend on their siblings by major line, e.g.
`"@openmrs/esm-patient-common-lib": "5.x"`. `yarn version` bumps each
workspace's own `version` field but leaves those ranges alone, so a 5.x -> 6.x
release would publish packages still declaring a peer requirement on the
previous major — unsatisfiable against their own siblings.

Only ranges that are both internal (the dependency is a workspace in this repo)
and already written in the `<major>.x` house style are touched. Anything else —
an external package, a pinned version, a caret or a complex range — is left
exactly as the repo author wrote it.

Configuration comes from the environment:

  NEW_MAJOR  the major being released, e.g. "6"

Manifests are rewritten in place with 2-space indentation and a trailing
newline, matching what npm and yarn themselves write.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import yarn_workspaces

# The house style for an internal peer range: a bare major line, "5.x".
MAJOR_X_RE = re.compile(r"^\d+\.x$")


def workspace_locations():
    """Return every workspace location as yarn reports it, root included."""
    return [w["location"] for w in yarn_workspaces() if "location" in w]


def load_manifests(locations, base_dir="."):
    """Return [(path, parsed package.json)] for each workspace location."""
    manifests = []
    for location in locations:
        path = os.path.normpath(os.path.join(base_dir, location, "package.json"))
        try:
            with open(path) as f:
                manifests.append((path, json.load(f)))
        except (OSError, ValueError) as e:
            print(f"::warning::Failed to read {path}: {e}", file=sys.stderr)
    return manifests


def retarget_peer_ranges(manifests, target):
    """Point internal `<major>.x` peer ranges at `target`, in place.

    Returns the changes as (path, package name, dependency, old range).
    """
    names = {pkg.get("name") for _, pkg in manifests if pkg.get("name")}
    changes = []
    for path, pkg in manifests:
        peers = pkg.get("peerDependencies") or {}
        for dep, current in list(peers.items()):
            if dep not in names:
                continue
            if not MAJOR_X_RE.match(str(current)) or current == target:
                continue
            peers[dep] = target
            changes.append((path, pkg.get("name", path), dep, current))
    return changes


def write_manifest(path, pkg):
    """Rewrite a manifest the way npm and yarn format it."""
    with open(path, "w") as f:
        # ensure_ascii=False so non-ASCII text in descriptions and author
        # fields survives the round trip unescaped, as JSON.stringify leaves it.
        f.write(json.dumps(pkg, indent=2, ensure_ascii=False) + "\n")


def main():
    new_major = os.environ.get("NEW_MAJOR", "").strip()
    if not new_major.isdigit():
        sys.exit(f"::error::NEW_MAJOR must be a major version number, got '{new_major}'")
    target = f"{new_major}.x"

    manifests = load_manifests(workspace_locations())
    changes = retarget_peer_ranges(manifests, target)
    if not changes:
        print(f"No internal peer ranges needed retargeting to {target}")
        return

    by_path = {path: pkg for path, pkg in manifests}
    for path in dict.fromkeys(path for path, _, _, _ in changes):
        write_manifest(path, by_path[path])
    for _, pkg_name, dep, old in changes:
        print(f"{pkg_name}: {dep} {old} -> {target}")


if __name__ == "__main__":
    main()
