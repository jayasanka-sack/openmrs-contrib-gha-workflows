#!/usr/bin/env python3
"""Describe the state a failed release run left behind.

A failed release is never rolled back. The commit and tag are pushed BEFORE
publishing (npm versions are immutable, so the reverse order could strand
packages that no tag points at), and nothing can un-publish npm. So rather than
leaving an operator to work out what escaped, report exactly what did and did
not happen, and what to do about it.

Configuration comes from the environment:

  RELEASE_TAG      tag this run was cutting, empty if it failed before that
  RELEASE_VERSION  version behind that tag
  BRANCH           branch the release was cut from
  PUBLISH_ONLY     "true" when this run reused an existing tag
  PUSHED           "true" when THIS run's push step succeeded

PUSHED is the push step's own outcome rather than a probe of the remote: a tag
being on the remote says nothing about who put it there, and publish-only
always reuses one that was already pushed.

Writes markdown to GITHUB_STEP_SUMMARY. This runs when the job has already
failed, so it must never add a failure of its own: every path exits 0.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from utils import publishable_package_names, yarn_workspaces

HEADING = "### ❌ Release failed — current state"
NO_GH_RELEASE = (
    "No GitHub Release was created: that job is skipped when this one fails."
)


def is_published(package, version):
    """True if `package@version` is already on the registry."""
    try:
        result = subprocess.run(
            ["npm", "view", f"{package}@{version}", "version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as e:
        print(f"::warning::Could not query npm for {package}: {e}", file=sys.stderr)
        return False
    return result.stdout.strip() == version


def split_by_publish_state(packages, version, probe=None):
    """Partition packages into (already published, not published).

    `probe` resolves at call time rather than binding `is_published` as a
    default, so the registry lookup stays substitutable.
    """
    probe = probe or is_published
    live, missing = [], []
    for package in packages:
        (live if probe(package, version) else missing).append(package)
    return live, missing


def render(tag, version, branch, publish_only, pushed, live=(), missing=()):
    """Return the markdown report for the state this run ended in.

    Pure: `live` and `missing` are supplied by the caller so the branching can
    be tested without touching the network.
    """
    out = ["", HEADING, ""]

    if not tag:
        out.append(
            "Failed before a release tag was computed. Nothing was committed, "
            "pushed or published — fix the error above and re-run."
        )
        return "\n".join(out) + "\n"

    if not publish_only and not pushed:
        out += [
            f"- **Tag `{tag}`:** not pushed",
            "- **npm:** nothing published",
            "",
            f"Nothing left the runner, so `{version}` is still free. Fix the "
            "error above and re-run.",
        ]
        return "\n".join(out) + "\n"

    # Either this run pushed the tag, or publish-only reused an existing one.
    # Both mean the failure came at or after publish.
    if publish_only:
        out.append(
            f"- **Tag `{tag}`:** pre-existing — this run committed and pushed nothing"
        )
    else:
        out.append(
            f"- **Tag `{tag}`:** pushed to `{branch}` — commit and tag are live"
        )

    if live:
        out.append(f"- **npm — already published at `{version}`:**")
        out += [f"  - `{name}`" for name in live]
    else:
        out.append(f"- **npm:** nothing published at `{version}`")

    # Only worth naming when the publish got partway; if nothing published, the
    # line above already says so.
    if live and missing:
        out.append("- **npm — NOT published:**")
        out += [f"  - `{name}`" for name in missing]

    out += ["", "#### Recovery", ""]

    if live:
        out += [
            f"⚠️ Some packages are already on npm at `{version}`, and "
            "**npm versions cannot be republished or reused**. Do not re-run "
            f"this workflow at `{version}`, and do not delete the tag — it "
            "matches what is on npm.",
            "",
            f"Re-running with **publish_only** and `release_version: {version}` "
            "is safe here: the already-published packages fail with "
            "EPUBLISHCONFLICT, so use it only if your publish command tolerates "
            "that. Otherwise publish the missing packages by hand at the same "
            f"version, or leave `{version}` partial and cut the next version.",
        ]
    elif publish_only:
        out.append(
            "Nothing reached npm and the tag is untouched. Fix the error above "
            f"and re-run with **publish_only** and `release_version: {version}`."
        )
    else:
        out += [
            f"Nothing reached npm, so `{version}` is still free. The tag and "
            "commit are already pushed, so pick one:",
            "",
            "1. **Finish this release** — re-run with **publish_only** and "
            f"`release_version: {version}`. It builds and publishes the existing "
            "tag; no second bump or commit.",
            "2. **Start over** — remove the tag and the release commit, then "
            "release again:",
            "",
            "```bash",
            f"git push origin :refs/tags/{tag}",
            f"git revert --no-edit {tag}^{{commit}}",
            "```",
        ]

    out += ["", NO_GH_RELEASE]
    return "\n".join(out) + "\n"


def write_summary(markdown):
    """Append to GITHUB_STEP_SUMMARY, or stdout when running outside Actions."""
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not summary_file:
        print(markdown, end="")
        return
    with open(summary_file, "a") as f:
        f.write(markdown)


def main():
    tag = os.environ.get("RELEASE_TAG", "").strip()
    version = os.environ.get("RELEASE_VERSION", "").strip()
    branch = os.environ.get("BRANCH", "").strip()
    publish_only = os.environ.get("PUBLISH_ONLY", "") == "true"
    pushed = os.environ.get("PUSHED", "") == "true"

    live, missing = (), ()
    if tag and (publish_only or pushed):
        packages = publishable_package_names(yarn_workspaces(("--no-private",)))
        live, missing = split_by_publish_state(packages, version)

    write_summary(render(tag, version, branch, publish_only, pushed, live, missing))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001 - the job has already failed
        # Never stack a second failure on top of the one being reported.
        print(f"::warning::Could not report release state: {e}", file=sys.stderr)
    sys.exit(0)
