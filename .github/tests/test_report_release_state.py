#!/usr/bin/env python3
"""Tests for report_release_state.py."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import report_release_state as rrs

TAG = "v9.9.9"
VER = "9.9.9"


def render(**kw):
    args = dict(tag=TAG, version=VER, branch="main", publish_only=False, pushed=True)
    args.update(kw)
    return rrs.render(**args)


class TestFailedBeforeTag(unittest.TestCase):
    def test_reports_nothing_escaped(self):
        out = render(tag="", version="", pushed=False)
        self.assertIn("Failed before a release tag was computed", out)
        self.assertNotIn("#### Recovery", out)

    def test_wins_over_every_other_state(self):
        # No tag means no release to describe, whatever else happened.
        out = render(tag="", publish_only=True, pushed=True, live=["@o/a"])
        self.assertIn("Failed before a release tag was computed", out)
        self.assertNotIn("@o/a", out)


class TestNotPushed(unittest.TestCase):
    def test_version_still_free(self):
        out = render(pushed=False)
        self.assertIn("not pushed", out)
        self.assertIn(f"`{VER}` is still free", out)
        self.assertNotIn("#### Recovery", out)

    def test_publish_only_is_not_treated_as_unpushed(self):
        # publish-only never pushes, but the tag it reuses is already live.
        out = render(publish_only=True, pushed=False)
        self.assertNotIn("not pushed", out)
        self.assertIn("pre-existing", out)


class TestTagLive(unittest.TestCase):
    def test_normal_run_names_the_branch(self):
        self.assertIn("pushed to `main`", render())

    def test_publish_only_does_not_claim_to_have_pushed(self):
        out = render(publish_only=True, pushed=False)
        self.assertIn("this run committed and pushed nothing", out)
        self.assertNotIn("pushed to `main`", out)


class TestNpmState(unittest.TestCase):
    def test_nothing_published(self):
        out = render(live=[], missing=["@o/a", "@o/b"])
        self.assertIn(f"nothing published at `{VER}`", out)
        # Listing every package adds nothing once we have said none published.
        self.assertNotIn("NOT published", out)

    def test_partial_publish_names_both_sides(self):
        out = render(live=["@o/a"], missing=["@o/b"])
        self.assertIn("already published", out)
        self.assertIn("  - `@o/a`", out)
        self.assertIn("NOT published", out)
        self.assertIn("  - `@o/b`", out)

    def test_fully_published(self):
        out = render(live=["@o/a", "@o/b"], missing=[])
        self.assertIn("already published", out)
        self.assertNotIn("NOT published", out)


class TestRecovery(unittest.TestCase):
    def test_partial_publish_warns_version_is_burned(self):
        out = render(live=["@o/a"], missing=["@o/b"])
        self.assertIn("cannot be republished or reused", out)
        self.assertIn("do not delete the tag", out)
        # Must not offer the clean restart: the version is already spent.
        self.assertNotIn("Start over", out)

    def test_pushed_but_unpublished_offers_both_routes(self):
        out = render(live=[], missing=["@o/a"])
        self.assertIn("Finish this release", out)
        self.assertIn("publish_only", out)
        self.assertIn("Start over", out)
        self.assertIn(f"git push origin :refs/tags/{TAG}", out)
        self.assertIn(f"git revert --no-edit {TAG}^{{commit}}", out)

    def test_publish_only_says_just_re_run(self):
        out = render(publish_only=True, pushed=False, live=[], missing=["@o/a"])
        self.assertIn("the tag is untouched", out)
        self.assertNotIn("Start over", out)
        self.assertNotIn("git push origin :refs/tags/", out)

    def test_github_release_note_present_once_tag_is_live(self):
        self.assertIn(rrs.NO_GH_RELEASE, render())
        self.assertNotIn(rrs.NO_GH_RELEASE, render(pushed=False))


class TestSplitByPublishState(unittest.TestCase):
    def test_partitions_on_probe(self):
        live, missing = rrs.split_by_publish_state(
            ["@o/a", "@o/b"], VER, probe=lambda p, v: p == "@o/a"
        )
        self.assertEqual((live, missing), (["@o/a"], ["@o/b"]))

    def test_empty_package_list(self):
        self.assertEqual(rrs.split_by_publish_state([], VER), ([], []))


class TestIsPublished(unittest.TestCase):
    def _result(self, stdout):
        class R:
            pass

        r = R()
        r.stdout = stdout
        return r

    def test_true_on_exact_version_match(self):
        with patch.object(rrs.subprocess, "run", return_value=self._result("9.9.9\n")):
            self.assertTrue(rrs.is_published("@o/a", VER))

    def test_false_when_npm_says_nothing(self):
        with patch.object(rrs.subprocess, "run", return_value=self._result("")):
            self.assertFalse(rrs.is_published("@o/a", VER))

    def test_false_when_npm_is_missing(self):
        with patch.object(rrs.subprocess, "run", side_effect=OSError("no npm")):
            self.assertFalse(rrs.is_published("@o/a", VER))


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.summary = os.path.join(self._tmp.name, "summary.md")
        self.addCleanup(self._tmp.cleanup)
        self._env = patch.dict(
            os.environ, {"GITHUB_STEP_SUMMARY": self.summary}, clear=False
        )
        self._env.start()
        self.addCleanup(self._env.stop)

    def _run(self, **env):
        os.environ.update(
            {
                "RELEASE_TAG": TAG,
                "RELEASE_VERSION": VER,
                "BRANCH": "main",
                "PUBLISH_ONLY": "false",
                "PUSHED": "false",
                **env,
            }
        )
        rrs.main()
        with open(self.summary) as f:
            return f.read()

    def test_skips_npm_probe_when_nothing_was_pushed(self):
        # No tag on the remote means no reason to ask npm anything.
        with patch.object(rrs, "yarn_workspaces") as ws:
            out = self._run(PUSHED="false")
        ws.assert_not_called()
        self.assertIn("not pushed", out)

    def test_probes_npm_once_the_tag_is_live(self):
        with patch.object(
            rrs, "yarn_workspaces", return_value=[{"location": ".", "name": "@o/solo"}]
        ), patch.object(rrs, "is_published", return_value=True):
            out = self._run(PUSHED="true")
        self.assertIn("already published", out)
        self.assertIn("@o/solo", out)

    def test_drops_root_when_other_workspaces_exist(self):
        workspaces = [
            {"location": ".", "name": "@o/root"},
            {"location": "packages/a", "name": "@o/a"},
        ]
        with patch.object(rrs, "yarn_workspaces", return_value=workspaces), patch.object(
            rrs, "is_published", return_value=True
        ):
            out = self._run(PUSHED="true")
        self.assertIn("@o/a", out)
        self.assertNotIn("@o/root", out)

    def test_appends_rather_than_truncating(self):
        with open(self.summary, "w") as f:
            f.write("### Release Parameters\n")
        out = self._run(PUSHED="false")
        self.assertIn("### Release Parameters", out)
        self.assertIn("Release failed", out)

    def test_summary_is_optional(self):
        del os.environ["GITHUB_STEP_SUMMARY"]
        os.environ.update({"RELEASE_TAG": TAG, "RELEASE_VERSION": VER})
        rrs.main()  # must not raise


if __name__ == "__main__":
    unittest.main()
