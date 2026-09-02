"""Fail-closed reconciliation of the legacy skin-image cache.

The legacy filenames contain a collector index that is intentionally ignored.
Rows are matched through the stable hero/skin identity resolver used by the
premium pilot.  Only pristine ``NULL``/``skipped`` bindings may be updated.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, UnidentifiedImageError

from data.sqlite_read import connect_readonly, table_exists
from models.premium_pilot import resolve_cached_images, sha256_file


REQUIRED_TABLES = ("skins", "skin_assets")
DEFAULT_EXPECTED_SKINS = 960
DEFAULT_EXPECTED_PRIMARY_URLS = 806
DEFAULT_EXPECTED_VERIFIED = 798


class ReconciliationError(RuntimeError):
    """Raised when a preflight or transactional safety condition fails."""


@dataclass(frozen=True)
class SnapshotExpectation:
    """Counts that lock an apply operation to a reviewed data snapshot."""

    skins: int | None = DEFAULT_EXPECTED_SKINS
    primary_urls: int | None = DEFAULT_EXPECTED_PRIMARY_URLS
    verified_images: int | None = DEFAULT_EXPECTED_VERIFIED


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _is_blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def _stored_path(path: Path) -> str:
    """Prefer repository-relative paths while supporting external fixtures."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(resolved)


def _render_verify(path: Path) -> tuple[str, int, int, str]:
    """Decode an image completely and return its digest and basic metadata."""
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            image_format = image.format or "unknown"
    except (OSError, ValueError, UnidentifiedImageError) as exc:
        raise ReconciliationError(f"image decode failed: {path}: {exc}") from exc
    return sha256_file(path), int(width), int(height), str(image_format)


def _duplicate_identity_keys(rows: Iterable[dict[str, Any]]) -> set[str]:
    """Return source keys whose stable ID and name identities are both tied."""
    rows = list(rows)
    id_counts = Counter(
        (str(row.get("hero_name") or "").strip(), str(row.get("skin_id") or "").strip())
        for row in rows
        if str(row.get("hero_name") or "").strip()
        and str(row.get("skin_id") or "").strip()
    )
    name_counts = Counter(
        (
            str(row.get("hero_name") or "").strip(),
            str(row.get("skin_name") or "").strip(),
        )
        for row in rows
        if str(row.get("hero_name") or "").strip()
        and str(row.get("skin_name") or "").strip()
    )
    ambiguous: set[str] = set()
    for row in rows:
        by_id = (
            str(row.get("hero_name") or "").strip(),
            str(row.get("skin_id") or "").strip(),
        )
        by_name = (
            str(row.get("hero_name") or "").strip(),
            str(row.get("skin_name") or "").strip(),
        )
        if id_counts.get(by_id, 0) > 1 and name_counts.get(by_name, 0) > 1:
            ambiguous.add(str(row["source_key"]))
    return ambiguous


def _read_snapshot(db_path: Path) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if not db_path.is_file():
        raise ReconciliationError(f"database does not exist: {db_path}")
    try:
        with connect_readonly(db_path) as conn:
            missing = [name for name in REQUIRED_TABLES if not table_exists(conn, name)]
            if missing:
                raise ReconciliationError(
                    "missing required tables: " + ", ".join(sorted(missing))
                )
            rows = [
                dict(row)
                for row in conn.execute(
                    """
                    SELECT
                        s.source_key,
                        s.hero_name,
                        s.skin_id,
                        s.skin_name,
                        s.image_url,
                        s.image_path,
                        a.asset_id,
                        a.remote_url AS primary_url,
                        a.local_path AS primary_path,
                        a.content_hash AS primary_hash,
                        a.download_status AS primary_status
                    FROM skins s
                    LEFT JOIN skin_assets a
                      ON a.source_key = s.source_key
                     AND a.asset_type = 'skin_primary'
                    ORDER BY s.source_key
                    """
                ).fetchall()
            ]
            counts = {
                "skins": conn.execute("SELECT count(*) FROM skins").fetchone()[0],
                "primary_assets": conn.execute(
                    "SELECT count(*) FROM skin_assets WHERE asset_type='skin_primary'"
                ).fetchone()[0],
                "primary_urls": conn.execute(
                    """
                    SELECT count(*) FROM skin_assets
                    WHERE asset_type='skin_primary'
                      AND remote_url IS NOT NULL
                      AND trim(remote_url) <> ''
                    """
                ).fetchone()[0],
            }
    except sqlite3.Error as exc:
        raise ReconciliationError(f"database preflight failed: {exc}") from exc
    if len(rows) != counts["skins"]:
        raise ReconciliationError(
            "primary asset join is not one-to-one with skins; refusing reconciliation"
        )
    return rows, counts


def _check_expectation(
    counts: dict[str, int], expectation: SnapshotExpectation
) -> list[str]:
    mismatches: list[str] = []
    expected = {
        "skins": expectation.skins,
        "primary_urls": expectation.primary_urls,
        "verified_images": expectation.verified_images,
    }
    for key, value in expected.items():
        if value is not None and counts.get(key) != value:
            mismatches.append(f"{key}: expected {value}, found {counts.get(key)}")
    return mismatches


def _backup_database(db_path: Path, report_dir: Path) -> Path:
    backup_dir = report_dir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{db_path.stem}.pre-image-reconciliation-{_utc_stamp()}.sqlite3"
    source_uri = f"{db_path.resolve().as_uri()}?mode=ro"
    try:
        with sqlite3.connect(source_uri, uri=True) as source:
            with sqlite3.connect(backup_path) as destination:
                source.backup(destination)
    except sqlite3.Error as exc:
        if backup_path.exists():
            backup_path.unlink()
        raise ReconciliationError(f"database backup failed: {exc}") from exc
    return backup_path


def _write_report(report: dict[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"image-reconciliation-{_utc_stamp()}.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def reconcile_skin_images(
    db_path: str | Path,
    image_dir: str | Path,
    report_dir: str | Path,
    *,
    apply: bool = False,
    expectation: SnapshotExpectation = SnapshotExpectation(),
) -> tuple[dict[str, Any], Path]:
    """Plan or apply a verified local-image reconciliation.

    The returned report is always written.  Snapshot mismatches and corrupt
    files produce ``status='aborted'`` and never open a write connection.
    """
    database = Path(db_path)
    image_root = Path(image_dir).resolve()
    output_root = Path(report_dir)
    rows, counts = _read_snapshot(database)
    if not image_root.is_dir():
        raise ReconciliationError(f"image directory does not exist: {image_root}")

    ambiguous = _duplicate_identity_keys(rows)
    resolutions = resolve_cached_images(rows, image_root)
    records: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    update_candidates: list[dict[str, Any]] = []
    corrupt_files: list[dict[str, str]] = []

    for row in rows:
        source_key = str(row["source_key"])
        resolution = resolutions[source_key]
        record: dict[str, Any] = {"source_key": source_key}
        if source_key in ambiguous:
            category = "ambiguous_identity"
        elif _is_blank(row.get("primary_url")) and _is_blank(row.get("image_url")):
            category = "missing_url"
        elif not resolution.image_path:
            category = "missing_cached_image"
        else:
            path = Path(resolution.image_path).resolve()
            try:
                path.relative_to(image_root)
            except ValueError:
                category = "path_escape"
            else:
                try:
                    digest, width, height, image_format = _render_verify(path)
                except ReconciliationError as exc:
                    category = "corrupt_cached_image"
                    corrupt_files.append({"source_key": source_key, "error": str(exc)})
                else:
                    stored_path = _stored_path(path)
                    record.update(
                        {
                            "path": stored_path,
                            "sha256": digest,
                            "width": width,
                            "height": height,
                            "format": image_format,
                        }
                    )
                    exact_existing = (
                        str(row.get("image_path") or "") == stored_path
                        and str(row.get("primary_path") or "") == stored_path
                        and str(row.get("primary_hash") or "") == digest
                        and row.get("primary_status") == "existing"
                    )
                    pristine = (
                        _is_blank(row.get("image_path"))
                        and _is_blank(row.get("primary_path"))
                        and _is_blank(row.get("primary_hash"))
                        and row.get("primary_status") == "skipped"
                        and not _is_blank(row.get("asset_id"))
                    )
                    if exact_existing:
                        category = "already_bound"
                    elif pristine:
                        category = "update_candidate"
                        update_candidates.append(
                            {
                                "source_key": source_key,
                                "asset_id": str(row["asset_id"]),
                                "path": stored_path,
                                "sha256": digest,
                            }
                        )
                    else:
                        category = "binding_conflict"
        record["category"] = category
        category_counts[category] += 1
        records.append(record)

    verified_images = category_counts["update_candidate"] + category_counts["already_bound"]
    counts["verified_images"] = verified_images
    mismatches = _check_expectation(counts, expectation)
    fatal_categories = {
        name: category_counts[name]
        for name in ("corrupt_cached_image", "path_escape", "binding_conflict")
        if category_counts[name]
    }
    if fatal_categories:
        mismatches.append(
            "unsafe image/binding state: "
            + ", ".join(f"{key}={value}" for key, value in fatal_categories.items())
        )

    report: dict[str, Any] = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "mode": "apply" if apply else "dry-run",
        "database": str(database.resolve()),
        "image_dir": str(image_root),
        "expectation": {
            "skins": expectation.skins,
            "primary_urls": expectation.primary_urls,
            "verified_images": expectation.verified_images,
        },
        "preflight": counts,
        "summary": dict(sorted(category_counts.items())),
        "unresolved": {
            "missing_url": [
                item["source_key"] for item in records if item["category"] == "missing_url"
            ],
            "missing_cached_image": [
                item["source_key"]
                for item in records
                if item["category"] == "missing_cached_image"
            ],
            "ambiguous_identity": [
                item["source_key"]
                for item in records
                if item["category"] == "ambiguous_identity"
            ],
        },
        "mismatches": mismatches,
        "corrupt_files": corrupt_files,
        "records": records,
        "backup_path": None,
        "updated": 0,
    }

    if mismatches:
        report["status"] = "aborted"
        report_path = _write_report(report, output_root)
        return report, report_path

    if apply and update_candidates:
        backup_path = _backup_database(database, output_root)
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(database) as conn:
                conn.execute("BEGIN IMMEDIATE")
                for item in update_candidates:
                    skin_cursor = conn.execute(
                        """
                        UPDATE skins
                           SET image_path=?, updated_at=?
                         WHERE source_key=?
                           AND (image_path IS NULL OR trim(image_path)='')
                        """,
                        (item["path"], timestamp, item["source_key"]),
                    )
                    asset_cursor = conn.execute(
                        """
                        UPDATE skin_assets
                           SET local_path=?, content_hash=?, download_status='existing',
                               error=NULL, updated_at=?
                         WHERE asset_id=?
                           AND asset_type='skin_primary'
                           AND download_status='skipped'
                           AND (local_path IS NULL OR trim(local_path)='')
                           AND (content_hash IS NULL OR trim(content_hash)='')
                        """,
                        (item["path"], item["sha256"], timestamp, item["asset_id"]),
                    )
                    if skin_cursor.rowcount != 1 or asset_cursor.rowcount != 1:
                        raise ReconciliationError(
                            "concurrent binding change detected for " + item["source_key"]
                        )
                conn.commit()
        except (sqlite3.Error, ReconciliationError) as exc:
            raise ReconciliationError(
                f"image reconciliation transaction failed; backup: {backup_path}: {exc}"
            ) from exc
        report["backup_path"] = str(backup_path.resolve())
        report["updated"] = len(update_candidates)
        report["status"] = "applied"
    elif apply or not update_candidates:
        report["status"] = "already-reconciled"
    else:
        report["status"] = "ready"

    report_path = _write_report(report, output_root)
    return report, report_path
