"""H2: install-signal-flow-app must restore WA npm + wa-ingest-run post-install.

After app/ replace, Contabo historically lost whatsapp_sidecar/node_modules and
left wa-ingest-run dead. Install must npm-ci when connector present and modules
missing, restart wa-ingest-run when whatsapp[] is configured or WA was alive
pre-replace, never start bot-run, and print scrubbed SIGNAL_INGEST / WA_INGEST /
node_modules_ok markers.

Static grep contract over the Contabo workflow YAML (no SSH).
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
INSTALL_WF = REPO / ".github" / "workflows" / "install-signal-flow-app.yml"


class TestInstallSignalFlowAppH2WaRestore(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not INSTALL_WF.is_file():
            raise unittest.SkipTest(f"missing {INSTALL_WF}")
        cls.text = INSTALL_WF.read_text(encoding="utf-8")

    def test_h2_npm_ci_omit_dev_present(self):
        self.assertIn("npm ci --omit=dev", self.text)
        self.assertIn("whatsapp_sidecar", self.text)
        self.assertIn("connector.js", self.text)
        self.assertIn("node_modules", self.text)

    def test_h2_wa_ingest_run_post_install(self):
        self.assertIn("wa-ingest-run", self.text)
        self.assertIn("/tmp/sf-wa-ingest.pid", self.text)
        self.assertIn("WA_WAS_ALIVE", self.text)

    def test_h2_status_markers_scrubbed(self):
        self.assertIn("SIGNAL_INGEST", self.text)
        self.assertIn("WA_INGEST", self.text)
        self.assertIn("node_modules_ok", self.text)
        self.assertIn("[redacted]", self.text)
        # sed filter drops sgnl links (ERE may escape slashes)
        self.assertTrue(
            "sgnl://" in self.text or "sgnl:" in self.text,
            "expected sgnl scrub in log sed filters",
        )

    def test_h2_never_starts_bot_run(self):
        self.assertIn("bot-run", self.text)
        self.assertTrue(
            "BOT_RUN=stopped" in self.text
            or "bot-run stays stopped" in self.text.lower()
            or "never start bot-run" in self.text.lower()
            or ("Does not" in self.text and "bot-run" in self.text),
            "install must document bot-run stayed stopped",
        )
        for line in self.text.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "nohup" in stripped and "bot-run" in stripped:
                self.fail(f"install must not nohup bot-run: {stripped}")


if __name__ == "__main__":
    unittest.main()
