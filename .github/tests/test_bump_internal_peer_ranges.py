#!/usr/bin/env python3
"""Tests for bump_internal_peer_ranges.py."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import bump_internal_peer_ranges as bipr


def manifest(name, peers=None, **extra):
    pkg = {"name": name, "version": "5.3.1"}
    if peers is not None:
        pkg["peerDependencies"] = peers
    pkg.update(extra)
    return pkg


class TestRetargetPeerRanges(unittest.TestCase):
    def test_bumps_internal_major_line(self):
        ms = [
            ("a/package.json", manifest("@o/a", {"@o/b": "5.x"})),
            ("b/package.json", manifest("@o/b")),
        ]
        changes = bipr.retarget_peer_ranges(ms, "6.x")
        self.assertEqual(changes, [("a/package.json", "@o/a", "@o/b", "5.x")])
        self.assertEqual(ms[0][1]["peerDependencies"]["@o/b"], "6.x")

    def test_leaves_external_packages_alone(self):
        # @openmrs/esm-framework is not a workspace here, so it is not ours.
        ms = [("a/package.json", manifest("@o/a", {"@openmrs/esm-framework": "5.x"}))]
        self.assertEqual(bipr.retarget_peer_ranges(ms, "6.x"), [])
        self.assertEqual(ms[0][1]["peerDependencies"]["@openmrs/esm-framework"], "5.x")

    def test_leaves_non_house_style_ranges_alone(self):
        # Anything the author wrote deliberately stays as written.
        peers = {"@o/b": "^5.0.0", "@o/c": "*", "@o/d": "5.3.1", "@o/e": ">=5 <7"}
        ms = [
            ("a/package.json", manifest("@o/a", peers)),
            ("b/package.json", manifest("@o/b")),
            ("c/package.json", manifest("@o/c")),
            ("d/package.json", manifest("@o/d")),
            ("e/package.json", manifest("@o/e")),
        ]
        self.assertEqual(bipr.retarget_peer_ranges(ms, "6.x"), [])
        self.assertEqual(ms[0][1]["peerDependencies"], peers)

    def test_skips_ranges_already_on_target(self):
        ms = [
            ("a/package.json", manifest("@o/a", {"@o/b": "6.x"})),
            ("b/package.json", manifest("@o/b")),
        ]
        self.assertEqual(bipr.retarget_peer_ranges(ms, "6.x"), [])

    def test_handles_missing_and_null_peer_sections(self):
        ms = [
            ("a/package.json", manifest("@o/a")),
            ("b/package.json", manifest("@o/b", None)),
            ("c/package.json", {"name": "@o/c", "peerDependencies": None}),
        ]
        self.assertEqual(bipr.retarget_peer_ranges(ms, "6.x"), [])

    def test_multiple_packages_and_deps(self):
        ms = [
            ("a/package.json", manifest("@o/a", {"@o/b": "5.x", "@o/c": "5.x"})),
            ("b/package.json", manifest("@o/b", {"@o/c": "5.x"})),
            ("c/package.json", manifest("@o/c")),
        ]
        changes = bipr.retarget_peer_ranges(ms, "6.x")
        self.assertEqual(len(changes), 3)
        self.assertEqual(ms[0][1]["peerDependencies"], {"@o/b": "6.x", "@o/c": "6.x"})
        self.assertEqual(ms[1][1]["peerDependencies"], {"@o/c": "6.x"})

    def test_root_workspace_counts_as_internal(self):
        # yarn lists the root too, so a peer on the root is internal.
        ms = [
            (".../package.json", manifest("@o/root")),
            ("a/package.json", manifest("@o/a", {"@o/root": "5.x"})),
        ]
        self.assertEqual(len(bipr.retarget_peer_ranges(ms, "6.x")), 1)

    def test_major_jump_of_more_than_one(self):
        ms = [
            ("a/package.json", manifest("@o/a", {"@o/b": "4.x"})),
            ("b/package.json", manifest("@o/b")),
        ]
        bipr.retarget_peer_ranges(ms, "12.x")
        self.assertEqual(ms[0][1]["peerDependencies"]["@o/b"], "12.x")


class TestWorkspaceLocations(unittest.TestCase):
    def test_maps_yarn_entries_to_locations(self):
        entries = [
            {"location": ".", "name": "@o/root"},
            {"location": "packages/a", "name": "@o/a"},
        ]
        with patch.object(bipr, "yarn_workspaces", return_value=entries):
            self.assertEqual(bipr.workspace_locations(), [".", "packages/a"])

    def test_skips_entries_without_a_location(self):
        entries = [{"location": "packages/a"}, {"name": "@o/nowhere"}]
        with patch.object(bipr, "yarn_workspaces", return_value=entries):
            self.assertEqual(bipr.workspace_locations(), ["packages/a"])


class TestFileRoundTrip(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def _write(self, relpath, pkg):
        path = os.path.join(self.tmpdir, relpath)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(pkg, f)
        return path

    def test_load_manifests_resolves_locations(self):
        self._write("package.json", manifest("@o/root"))
        self._write("packages/a/package.json", manifest("@o/a"))
        got = bipr.load_manifests([".", "packages/a"], self.tmpdir)
        self.assertEqual([p[1]["name"] for p in got], ["@o/root", "@o/a"])

    def test_load_manifests_skips_unreadable(self):
        self._write("packages/a/package.json", manifest("@o/a"))
        got = bipr.load_manifests(["packages/a", "packages/missing"], self.tmpdir)
        self.assertEqual(len(got), 1)

    def _slurp(self, path):
        with open(path) as f:
            return f.read()

    def test_write_manifest_formatting(self):
        path = os.path.join(self.tmpdir, "package.json")
        bipr.write_manifest(path, {"name": "@o/a", "peerDependencies": {"@o/b": "6.x"}})
        raw = self._slurp(path)
        self.assertTrue(raw.endswith("}\n"), "must end with a trailing newline")
        self.assertIn('\n  "name"', raw, "must use 2-space indentation")

    def test_write_manifest_preserves_key_order_and_unicode(self):
        path = os.path.join(self.tmpdir, "package.json")
        bipr.write_manifest(path, {"name": "@o/a", "author": "Zoë", "version": "6.0.0"})
        raw = self._slurp(path)
        self.assertIn("Zoë", raw, "non-ASCII must not be escaped")
        self.assertLess(raw.index('"name"'), raw.index('"author"'))
        self.assertLess(raw.index('"author"'), raw.index('"version"'))


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = self._tmp.name
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(os.environ, {}, clear=False)
        self._env.start()
        self.addCleanup(self._env.stop)

    def _run(self, new_major, locations):
        os.environ["NEW_MAJOR"] = new_major
        cwd = os.getcwd()
        os.chdir(self.tmpdir)
        try:
            with patch.object(bipr, "workspace_locations", return_value=locations):
                bipr.main()
        finally:
            os.chdir(cwd)

    def _write(self, relpath, pkg):
        path = os.path.join(self.tmpdir, relpath)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(pkg, f)

    def _read(self, relpath):
        with open(os.path.join(self.tmpdir, relpath)) as f:
            return json.load(f)

    def test_rewrites_only_changed_files(self):
        self._write("package.json", manifest("@o/root"))
        self._write("packages/a/package.json", manifest("@o/a", {"@o/root": "5.x"}))
        self._write("packages/b/package.json", manifest("@o/b", {"react": "18.x"}))
        untouched = os.path.join(self.tmpdir, "packages/b/package.json")
        with open(untouched) as f:
            before = f.read()
        self._run("6", [".", "packages/a", "packages/b"])
        self.assertEqual(self._read("packages/a/package.json")["peerDependencies"]["@o/root"], "6.x")
        # untouched file keeps its original bytes, so it stays out of the commit
        with open(untouched) as f:
            self.assertEqual(f.read(), before)

    def test_rejects_non_numeric_major(self):
        self._write("package.json", manifest("@o/root"))
        for bad in ["", "6.x", "v6", "abc"]:
            with self.subTest(bad=bad):
                with self.assertRaises(SystemExit):
                    self._run(bad, ["."])


if __name__ == "__main__":
    unittest.main()
