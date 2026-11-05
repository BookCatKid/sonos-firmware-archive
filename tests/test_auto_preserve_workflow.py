import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / ".github/workflows/auto-preserve.yml").read_text()


class AutoPreserveWorkflowTests(unittest.TestCase):
    def test_decrypt_step_runs_after_archive_or_when_no_new_archive(self):
        self.assertIn("id: firmware_decrypt", TEXT)
        self.assertIn("steps.firmware_archive.outcome == 'success'", TEXT)
        self.assertIn("steps.firmware_archive.outcome == 'skipped'", TEXT)

    def test_decrypt_step_uses_six_model_key_secrets(self):
        for model in (1, 8, 9, 12, 16, 17):
            name = f"SONOS_MODEL_{model}_PRIVATE_KEY"
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", TEXT)

    def test_decrypt_step_never_logs_key_material(self):
        self.assertIn("umask 077", TEXT)
        self.assertNotIn("set -x", TEXT)
        for line in TEXT.splitlines():
            stripped = line.strip()
            # a print that expands a key variable without redirecting to a file
            # leaks the key into workflow logs; the name-only notice and the
            # redirected keyfile write are safe
            leaks = (
                ("${!var}" in stripped or "_PRIVATE_KEY}" in stripped.replace("${model}", "8"))
                and (stripped.startswith("echo") or stripped.startswith("printf"))
                and ">" not in stripped
            )
            if leaks:
                self.fail(f"key material printed to logs: {stripped}")

    def test_receipts_land_only_after_publish(self):
        run_start = TEXT.index("id: firmware_decrypt")
        run_end = TEXT.index("- uses:", run_start)
        run = TEXT[run_start:run_end]
        self.assertLess(run.index("upload_raw_assets.py"), run.index("data/raw/"))
        self.assertIn("git checkout -- data/catalog.json", run)

    def test_receipts_are_committed(self):
        self.assertIn("git add data/apps data/catalog.json data/discovery data/manifests data/raw data/upd", TEXT)

    def test_decrypt_failure_opens_preservation_issue(self):
        self.assertIn("steps.firmware_decrypt.outcome == 'failure'", TEXT)
        self.assertIn("firmware_decrypt.outcome", TEXT)


if __name__ == "__main__":
    unittest.main()

import yaml


def _load(path):
    return yaml.safe_load((ROOT / path).read_text())


class ApkeepCacheWiringTests(unittest.TestCase):
    """The pinned apkeep tool is built once and restored from cache after."""

    def _steps(self, path):
        workflow = _load(path)
        jobs = workflow["jobs"]
        steps = [step for job in jobs.values() for step in job["steps"]]
        return {step.get("id"): step for step in steps if step.get("id")}, steps

    def _check_workflow(self, path):
        by_id, steps = self._steps(path)
        cache = by_id.get("apkeep_cache")
        self.assertIsNotNone(cache, f"{path}: missing cache step")
        # hit: actions/cache restore, keyed on the vendored tarball so a pin
        # change is a cache miss; no restore-keys fallback to a stale build
        self.assertEqual(cache["uses"], "actions/cache@v4")
        key = cache["with"]["key"]
        self.assertIn("hashFiles('tools/vendor/apkeep-1.0.0.tar.gz')", key)
        self.assertNotIn("restore-keys", cache["with"])
        # cache failure: non-fatal, the build step still runs
        self.assertTrue(cache.get("continue-on-error"))
        build = by_id.get("apkeep")
        self.assertIsNotNone(build, f"{path}: missing build step")
        self.assertIn("cache-hit != 'true'", str(build.get("if", "")))
        # build installs into the same directory the cache restores
        self.assertIn(cache["with"]["path"].replace("~", "$HOME"), build["run"])
        # every apkeep invocation uses the cached location, never RUNNER_TEMP
        for step in steps:
            run = step.get("run") or ""
            self.assertNotIn("$RUNNER_TEMP/apkeep-bin", run)
        return by_id

    def test_auto_preserve_cache_wiring(self):
        by_id = self._check_workflow(".github/workflows/auto-preserve.yml")
        # hit path: the archive step must accept a restored binary, not only a fresh build
        play = by_id["play_archive"]
        condition = str(play["if"])
        self.assertIn("cache-hit == 'true'", condition)
        self.assertIn("steps.apkeep.outcome == 'success'", condition)
        # cache and build stay scoped to Play changes
        self.assertIn("steps.changes.outputs.play == 'true'", str(by_id["apkeep_cache"]["if"]))
        self.assertIn("steps.changes.outputs.play == 'true'", str(by_id["apkeep"]["if"]))

    def test_history_sweep_cache_wiring(self):
        self._check_workflow(".github/workflows/archive-apkpure-history.yml")
