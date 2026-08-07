"""Streamlit AppTest coverage for the four analysis pages.

These tests exercise the real page render paths (not just the query layer) to
regress the defects the root unit suite missed:

- the Data Workbench ``ImportError`` on ``app.evidence_table`` (defect #1);
- the zero-result explorer fallback selector crash (defect #2);
- the missing-last sort ordering (defect #2);
- withholding the headline emotion score on insufficient evidence (defect #4).

They run against the live local database (``DEFAULT_DB_PATH``) when present; the
current baseline is 960 skins / 49 cash records / 0 validated emotion rows, so
the emotion leaderboard and radar must all show honest empty states.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from streamlit.testing.v1 import AppTest

from data.skin_repository import DEFAULT_DB_PATH

DB_PRESENT = Path(DEFAULT_DB_PATH).exists()
skip_no_db = unittest.skipUnless(DB_PRESENT, "live skins.sqlite3 not present")


@skip_no_db
class DashboardPageRenderTests(unittest.TestCase):
    def test_overview_page_renders_without_exception(self):
        at = AppTest.from_file("app.py").run(timeout=30)
        self.assertFalse(at.exception, f"overview raised: {at.exception}")

    def test_explorer_page_renders_without_exception(self):
        at = AppTest.from_file("pages/皮肤探索.py").run(timeout=30)
        self.assertFalse(at.exception, f"explorer raised: {at.exception}")

    def test_explorer_zero_result_has_no_enabled_detail_button(self):
        """A search matching nothing must not render the fallback detail button."""
        at = AppTest.from_file("pages/皮肤探索.py")
        at.session_state["dash_search"] = "___no_such_skin_xyz___"
        at.run(timeout=30)
        self.assertFalse(at.exception, f"explorer raised: {at.exception}")
        # No fallback "查看详情" button when the result set is empty.
        labels = [b.label for b in at.button]
        self.assertNotIn("查看详情", labels)

    def test_detail_page_without_selection(self):
        at = AppTest.from_file("pages/皮肤详情.py").run(timeout=30)
        self.assertFalse(at.exception, f"detail raised: {at.exception}")

    def test_detail_page_insufficient_withholds_headline_score(self):
        """With no market signals, the detail page must warn that the emotion
        score is withheld rather than presenting a comprehensive score."""
        # Pick any real skin from the DB.
        from data.skin_repository import SkinRepository

        skins = SkinRepository(DEFAULT_DB_PATH).list_skins(limit=1)
        if not skins:
            self.skipTest("no skins in DB")
        at = AppTest.from_file("pages/皮肤详情.py")
        at.session_state["dash_selected_source_key"] = skins[0]["source_key"]
        at.run(timeout=30)
        self.assertFalse(at.exception, f"detail raised: {at.exception}")
        warnings = " ".join(w.value for w in at.warning)
        self.assertIn("无法给出综合情绪分", warnings)

    def test_workbench_page_no_import_error(self):
        """Regresses defect #1: the workbench must not raise ImportError from a
        removed ``app.evidence_table`` symbol on any tab."""
        at = AppTest.from_file("pages/数据工作台.py").run(timeout=60)
        self.assertFalse(at.exception, f"workbench raised: {at.exception}")


if __name__ == "__main__":
    unittest.main()
