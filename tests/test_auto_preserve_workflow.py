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
