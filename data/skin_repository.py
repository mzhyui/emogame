"""SQLite query layer for WZRY hero and skin data.

The repository hides raw SQL details from UI, model, and future API layers.
It intentionally returns dictionaries so downstream code can serialize results
without needing ORM objects.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from data.sqlite_read import connect_readonly, table_exists


DEFAULT_DB_PATH = Path("data/wzry_skins/skins.sqlite3")


class SkinRepository:
    """Read-only helper around the WZRY skin SQLite database."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        return connect_readonly(self.db_path)

    @staticmethod
    def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        return [dict(row) for row in cursor.fetchall()]

    @staticmethod
    def _row(cursor: sqlite3.Cursor) -> dict[str, Any] | None:
        row = cursor.fetchone()
        return dict(row) if row else None

    @staticmethod
    def _limit_offset(limit: int | None, offset: int = 0) -> tuple[str, list[Any]]:
        if limit is None:
            return "", []
        return " LIMIT ? OFFSET ?", [max(0, int(limit)), max(0, int(offset))]

    def stats(self) -> dict[str, int]:
        """Return high-level table and quality counts."""
        empty = {
            "heroes": 0,
            "skins": 0,
            "assets": 0,
            "with_detail": 0,
            "missing_detail": 0,
            "detail_only": 0,
            "failed_assets": 0,
            "missing_primary_asset": 0,
        }
        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "skins"):
                    return empty
                has_heroes = table_exists(conn, "heroes")
                has_assets = table_exists(conn, "skin_assets")
                result = {
                    "heroes": (
                        conn.execute("SELECT count(*) FROM heroes").fetchone()[0]
                        if has_heroes
                        else 0
                    ),
                    "skins": conn.execute("SELECT count(*) FROM skins").fetchone()[0],
                    "assets": (
                        conn.execute("SELECT count(*) FROM skin_assets").fetchone()[0]
                        if has_assets
                        else 0
                    ),
                    "with_detail": conn.execute(
                        "SELECT count(*) FROM skins WHERE has_detail_record = 1"
                    ).fetchone()[0],
                    "missing_detail": conn.execute(
                        "SELECT count(*) FROM skins WHERE has_detail_record = 0"
                    ).fetchone()[0],
                    "detail_only": conn.execute(
                        "SELECT count(*) FROM skins WHERE catalog_source = 'detail_only'"
                    ).fetchone()[0],
                    "failed_assets": (
                        conn.execute(
                            "SELECT count(*) FROM skin_assets WHERE download_status = 'failed'"
                        ).fetchone()[0]
                        if has_assets
                        else 0
                    ),
                }
                result["missing_primary_asset"] = (
                    conn.execute(
                        """
                        SELECT count(*)
                        FROM skins s
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM skin_assets a
                            WHERE a.source_key = s.source_key
                              AND a.asset_type = 'skin_primary'
                        )
                        """
                    ).fetchone()[0]
                    if has_assets
                    else result["skins"]
                )
                return result
        except sqlite3.Error:
            return empty

    def list_heroes(
        self,
        *,
        hero_type: str | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List heroes with optional type/name filtering."""
        where: list[str] = []
        params: list[Any] = []
        if hero_type:
            where.append("hero_type = ?")
            params.append(hero_type)
        if search:
            where.append("(hero_name LIKE ? OR title LIKE ? OR id_name LIKE ?)")
            term = f"%{search}%"
            params.extend([term, term, term])

        query = """
            SELECT hero_id, hero_name, id_name, title, hero_type_code, hero_type, skin_count
            FROM heroes
        """
        if where:
            query += " WHERE " + " AND ".join(where)
        query += " ORDER BY hero_id"
        suffix, suffix_params = self._limit_offset(limit, offset)
        query += suffix

        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "heroes"):
                    return []
                return self._rows(conn.execute(query, params + suffix_params))
        except sqlite3.Error:
            return []

    def list_skins(
        self,
        *,
        hero_id: str | None = None,
        hero_name: str | None = None,
        quality: str | None = None,
        search: str | None = None,
        has_detail_record: bool | None = None,
        catalog_source: str | None = None,
        require_primary_asset: bool | None = None,
        limit: int | None = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List skins with common filters used by UI and data checks."""
        where: list[str] = []
        params: list[Any] = []
        if hero_id:
            where.append("s.hero_id = ?")
            params.append(hero_id)
        if hero_name:
            where.append("s.hero_name = ?")
            params.append(hero_name)
        if quality:
            where.append("s.quality = ?")
            params.append(quality)
        if search:
            where.append("(s.hero_name LIKE ? OR s.skin_name LIKE ? OR s.skin_id LIKE ?)")
            term = f"%{search}%"
            params.extend([term, term, term])
        if has_detail_record is not None:
            where.append("s.has_detail_record = ?")
            params.append(1 if has_detail_record else 0)
        if catalog_source:
            where.append("s.catalog_source = ?")
            params.append(catalog_source)
        if require_primary_asset is True:
            where.append("primary_asset.remote_url IS NOT NULL")
        elif require_primary_asset is False:
            where.append("primary_asset.remote_url IS NULL")

        query = """
            SELECT
                s.source_key,
                s.source_index,
                s.hero_id,
                s.hero_name,
                s.skin_index,
                s.skin_id,
                s.skin_name,
                s.quality,
                s.online_date,
                s.acquire_method,
                s.price_text,
                s.image_url,
                s.image_path,
                s.detail_url,
                s.mobile_url,
                s.video_id,
                s.intro,
                s.catalog_source,
                s.detail_source,
                s.has_detail_record,
                {asset_columns}
            FROM skins s
            {asset_join}
        """
        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "skins"):
                    return []
                has_assets = table_exists(conn, "skin_assets")
                if require_primary_asset is True and not has_assets:
                    return []
                if require_primary_asset is False and not has_assets:
                    where = [item for item in where if not item.startswith("primary_asset.")]
                rendered_query = query.format(
                    asset_columns=(
                        "primary_asset.remote_url AS primary_asset_url, "
                        "primary_asset.local_path AS primary_asset_path, "
                        "primary_asset.download_status AS primary_asset_status"
                        if has_assets
                        else (
                            "NULL AS primary_asset_url, NULL AS primary_asset_path, "
                            "NULL AS primary_asset_status"
                        )
                    ),
                    asset_join=(
                        "LEFT JOIN skin_assets primary_asset "
                        "ON primary_asset.source_key = s.source_key "
                        "AND primary_asset.asset_type = 'skin_primary'"
                        if has_assets
                        else ""
                    ),
                )
                if where:
                    rendered_query += " WHERE " + " AND ".join(where)
                rendered_query += " ORDER BY s.source_index, s.hero_id, s.skin_index"
                suffix, suffix_params = self._limit_offset(limit, offset)
                rendered_query += suffix
                return self._rows(
                    conn.execute(rendered_query, params + suffix_params)
                )
        except sqlite3.Error:
            return []

    def get_skin(self, source_key: str) -> dict[str, Any] | None:
        """Fetch one skin by internal source key."""
        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "skins"):
                    return None
                has_assets = table_exists(conn, "skin_assets")
                query = """
                    SELECT
                        s.source_key,
                        s.source_index,
                        s.hero_id,
                        s.hero_name,
                        s.skin_index,
                        s.skin_id,
                        s.skin_name,
                        s.quality,
                        s.online_date,
                        s.acquire_method,
                        s.price_text,
                        s.image_url,
                        s.image_path,
                        s.detail_url,
                        s.mobile_url,
                        s.video_id,
                        s.intro,
                        s.catalog_source,
                        s.detail_source,
                        s.has_detail_record,
                        {asset_columns}
                    FROM skins s
                    {asset_join}
                    WHERE s.source_key = ?
                """.format(
                    asset_columns=(
                        "primary_asset.remote_url AS primary_asset_url, "
                        "primary_asset.local_path AS primary_asset_path, "
                        "primary_asset.download_status AS primary_asset_status"
                        if has_assets
                        else (
                            "NULL AS primary_asset_url, NULL AS primary_asset_path, "
                            "NULL AS primary_asset_status"
                        )
                    ),
                    asset_join=(
                        "LEFT JOIN skin_assets primary_asset "
                        "ON primary_asset.source_key = s.source_key "
                        "AND primary_asset.asset_type = 'skin_primary'"
                        if has_assets
                        else ""
                    ),
                )
                return self._row(conn.execute(query, (source_key,)))
        except sqlite3.Error:
            return None

    def search_skins(self, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Search by hero name, skin name, or skin id."""
        return self.list_skins(search=query, limit=limit)

    def list_assets(self, source_key: str) -> list[dict[str, Any]]:
        """List image and icon assets for a skin."""
        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "skin_assets"):
                    return []
                return self._rows(conn.execute(
                    """
                    SELECT asset_id, source_key, asset_type, remote_url, local_path,
                           content_hash, download_status, error, updated_at
                    FROM skin_assets
                    WHERE source_key = ?
                    ORDER BY asset_type
                    """,
                    (source_key,),
                ))
        except sqlite3.Error:
            return []

    def missing_detail_skins(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Skins from the catalog baseline without a matching detail record."""
        return self.list_skins(has_detail_record=False, limit=limit)

    def missing_primary_asset_skins(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Skins without a primary image asset URL."""
        return self.list_skins(require_primary_asset=False, limit=limit)

    def failed_assets(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Asset downloads that failed in the last local collection."""
        suffix, params = self._limit_offset(limit)
        try:
            with closing(self._connect()) as conn:
                if not table_exists(conn, "skin_assets"):
                    return []
                return self._rows(conn.execute(
                    """
                    SELECT asset_id, source_key, asset_type, remote_url, error, updated_at
                    FROM skin_assets
                    WHERE download_status = 'failed'
                    ORDER BY updated_at DESC, asset_id
                    """ + suffix,
                    params,
                ))
        except sqlite3.Error:
            return []
