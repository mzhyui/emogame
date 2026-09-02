"""Tests for the canonical perceived-premium radar bundle."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from dashboard.premium_radar import (
    CANONICAL_RUN_ID,
    PremiumRadarBundleError,
    load_premium_radar_bundle,
)


def _radar_page() -> None:
    from app import _premium_radar_panel

    _premium_radar_panel()


class PremiumRadarBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "fixture-run"
        self.root.mkdir()
        self.write_bundle()

    def tearDown(self):
        self.tmp.cleanup()

    def write_bundle(self) -> None:
        (self.root / "radar_plots.html").write_text(
            '<section class="card"></section><section class="card"></section>',
            encoding="utf-8",
        )
        (self.root / "report.json").write_text(
            json.dumps(
                {
                    "selection": {"selected": 2},
                    "score_summary": {"complete": 1, "partial": 1, "insufficient": 0},
                    "trace_summary": {
                        "status": "complete",
                        "rows": 2,
                        "manifest_sha256": "manifest",
                    },
                }
            ),
            encoding="utf-8",
        )
        (self.root / "run_metadata.json").write_text(
            json.dumps(
                {
                    "run_id": "fixture-run",
                    "manifest_sha256": "manifest",
                    "evidence_policy": {
                        "synthetic_media_allowed": False,
                        "media_input_mode": "target_aware_comments",
                    },
                }
            ),
            encoding="utf-8",
        )

    def test_valid_bundle_loads_all_evidence_counts(self):
        bundle = load_premium_radar_bundle(
            self.root,
            expected_run_id="fixture-run",
            expected_counts=(2, 1, 1, 0),
        )
        self.assertEqual(bundle.selected, 2)
        self.assertEqual(bundle.complete, 1)
        self.assertEqual(bundle.partial, 1)

    def test_missing_bundle_file_is_rejected(self):
        (self.root / "report.json").unlink()
        with self.assertRaisesRegex(PremiumRadarBundleError, "missing"):
            load_premium_radar_bundle(
                self.root,
                expected_run_id="fixture-run",
                expected_counts=(2, 1, 1, 0),
            )

    def test_inconsistent_html_count_is_rejected(self):
        (self.root / "radar_plots.html").write_text(
            '<section class="card"></section>', encoding="utf-8"
        )
        with self.assertRaisesRegex(PremiumRadarBundleError, "contains 1 cards"):
            load_premium_radar_bundle(
                self.root,
                expected_run_id="fixture-run",
                expected_counts=(2, 1, 1, 0),
            )

    def test_inconsistent_policy_is_rejected(self):
        metadata = json.loads((self.root / "run_metadata.json").read_text())
        metadata["evidence_policy"]["synthetic_media_allowed"] = True
        (self.root / "run_metadata.json").write_text(json.dumps(metadata))
        with self.assertRaisesRegex(PremiumRadarBundleError, "synthetic"):
            load_premium_radar_bundle(
                self.root,
                expected_run_id="fixture-run",
                expected_counts=(2, 1, 1, 0),
            )

    def test_canonical_social_bundle_has_fifty_rows(self):
        root = Path("data/premium_pilot/runs") / CANONICAL_RUN_ID
        bundle = load_premium_radar_bundle(root)
        self.assertEqual(
            (bundle.selected, bundle.complete, bundle.partial, bundle.insufficient),
            (50, 46, 4, 0),
        )

    def test_fifth_page_renders_canonical_run_and_counts(self):
        at = AppTest.from_function(_radar_page).run(timeout=30)
        self.assertFalse(at.exception, f"premium radar raised: {at.exception}")
        captions = " ".join(item.value for item in at.caption)
        self.assertIn(CANONICAL_RUN_ID, captions)
        metrics = {item.label: item.value for item in at.metric}
        self.assertEqual(metrics["试点皮肤"], "50")
        self.assertEqual(metrics["完整证据"], "46")
        self.assertEqual(metrics["部分证据"], "4")


if __name__ == "__main__":
    unittest.main()
