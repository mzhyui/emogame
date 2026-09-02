"""Regression coverage for safe legacy image reconciliation."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from data.image_reconciliation import SnapshotExpectation, reconcile_skin_images


SCHEMA = """
CREATE TABLE skins (
    source_key TEXT PRIMARY KEY,
    hero_name TEXT NOT NULL,
    skin_id TEXT,
    skin_name TEXT NOT NULL,
    image_url TEXT,
    image_path TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE skin_assets (
    asset_id TEXT PRIMARY KEY,
    source_key TEXT NOT NULL,
    asset_type TEXT NOT NULL,
    remote_url TEXT NOT NULL,
    local_path TEXT,
    content_hash TEXT,
    download_status TEXT NOT NULL,
    error TEXT,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_test_asset_source_type
ON skin_assets(source_key, asset_type);
"""


class ImageReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "skins.sqlite3"
        self.image_dir = self.root / "images"
        self.report_dir = self.root / "reports"
        self.image_dir.mkdir()
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(SCHEMA)

    def tearDown(self):
        self.tmp.cleanup()

    def add_skin(
        self,
        source_key: str,
        *,
        hero: str = "曹操",
        skin_id: str = "12802",
        skin_name: str = "超能战警",
        remote_url: str | None = "https://example.invalid/skin.jpg",
        image_path: str | None = None,
        asset_path: str | None = None,
        asset_hash: str | None = None,
        status: str = "skipped",
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO skins VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    source_key,
                    hero,
                    skin_id,
                    skin_name,
                    remote_url,
                    image_path,
                    "baseline",
                ),
            )
            if remote_url is not None:
                conn.execute(
                    "INSERT INTO skin_assets VALUES (?, ?, 'skin_primary', ?, ?, ?, ?, NULL, ?)",
                    (
                        "asset-" + source_key,
                        source_key,
                        remote_url,
                        asset_path,
                        asset_hash,
                        status,
                        "baseline",
                    ),
                )

    def add_image(
        self,
        *,
        prefix: str = "0752",
        hero: str = "曹操",
        skin_id: str = "12802",
        skin_name: str = "超能战警",
    ) -> Path:
        path = self.image_dir / f"{prefix}-{hero}-{skin_id}-{skin_name}.png"
        Image.new("RGB", (4, 3), (10, 20, 30)).save(path)
        return path

    @staticmethod
    def expected(skins: int, urls: int, verified: int) -> SnapshotExpectation:
        return SnapshotExpectation(
            skins=skins, primary_urls=urls, verified_images=verified
        )

    def test_dry_run_verifies_without_mutating(self):
        self.add_skin("128-02")
        self.add_image()
        report, report_path = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            expectation=self.expected(1, 1, 1),
        )
        self.assertEqual(report["status"], "ready")
        self.assertEqual(report["summary"]["update_candidate"], 1)
        self.assertTrue(report_path.is_file())
        with sqlite3.connect(self.db_path) as conn:
            self.assertIsNone(
                conn.execute("SELECT image_path FROM skins").fetchone()[0]
            )
            self.assertEqual(
                conn.execute("SELECT download_status FROM skin_assets").fetchone()[0],
                "skipped",
            )

    def test_apply_backs_up_updates_and_second_run_is_idempotent(self):
        self.add_skin("128-02")
        image = self.add_image()
        report, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(1, 1, 1),
        )
        self.assertEqual(report["status"], "applied")
        self.assertEqual(report["updated"], 1)
        backup = Path(report["backup_path"])
        self.assertTrue(backup.is_file())
        with sqlite3.connect(backup) as conn:
            self.assertIsNone(
                conn.execute("SELECT image_path FROM skins").fetchone()[0]
            )
        with sqlite3.connect(self.db_path) as conn:
            skin_path = conn.execute("SELECT image_path FROM skins").fetchone()[0]
            asset = conn.execute(
                "SELECT local_path, content_hash, download_status FROM skin_assets"
            ).fetchone()
        self.assertEqual(Path(skin_path), image.resolve())
        self.assertEqual(Path(asset[0]), image.resolve())
        self.assertEqual(len(asset[1]), 64)
        self.assertEqual(asset[2], "existing")

        second, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(1, 1, 1),
        )
        self.assertEqual(second["status"], "already-reconciled")
        self.assertEqual(second["summary"]["already_bound"], 1)
        self.assertEqual(second["updated"], 0)
        self.assertIsNone(second["backup_path"])

    def test_corrupt_cached_image_aborts_without_mutation(self):
        self.add_skin("128-02")
        (self.image_dir / "0752-曹操-12802-超能战警.jpg").write_bytes(b"not-image")
        report, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(1, 1, 1),
        )
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["summary"]["corrupt_cached_image"], 1)
        self.assertIsNone(report["backup_path"])
        with sqlite3.connect(self.db_path) as conn:
            self.assertIsNone(
                conn.execute("SELECT image_path FROM skins").fetchone()[0]
            )

    def test_ambiguous_database_identity_remains_unbound(self):
        self.add_skin("128-02")
        self.add_skin("detail-12802")
        self.add_image()
        report, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(2, 2, 0),
        )
        self.assertEqual(report["status"], "already-reconciled")
        self.assertEqual(report["summary"]["ambiguous_identity"], 2)
        self.assertEqual(
            report["unresolved"]["ambiguous_identity"],
            ["128-02", "detail-12802"],
        )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT count(*) FROM skins WHERE image_path IS NOT NULL"
                ).fetchone()[0],
                0,
            )

    def test_existing_binding_conflict_is_preserved_and_aborts(self):
        self.add_skin("128-02", image_path="do-not-overwrite.jpg")
        self.add_image()
        report, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(1, 1, 0),
        )
        self.assertEqual(report["status"], "aborted")
        self.assertEqual(report["summary"]["binding_conflict"], 1)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT image_path FROM skins").fetchone()[0],
                "do-not-overwrite.jpg",
            )

    def test_snapshot_mismatch_aborts_before_backup_or_update(self):
        self.add_skin("128-02")
        self.add_image()
        report, _ = reconcile_skin_images(
            self.db_path,
            self.image_dir,
            self.report_dir,
            apply=True,
            expectation=self.expected(960, 806, 798),
        )
        self.assertEqual(report["status"], "aborted")
        self.assertTrue(report["mismatches"])
        self.assertIsNone(report["backup_path"])
        with sqlite3.connect(self.db_path) as conn:
            self.assertIsNone(
                conn.execute("SELECT image_path FROM skins").fetchone()[0]
            )


if __name__ == "__main__":
    unittest.main()
