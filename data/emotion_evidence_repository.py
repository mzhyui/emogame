"""SQLite persistence for provenance-qualified community emotion evidence."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping

from data.market_signal_repository import MarketSignalRepository
from data.skin_repository import DEFAULT_DB_PATH
from data.sqlite_read import connect_readonly, table_exists
from models.emotion_evidence import (
    COHORT_SIZE,
    REQUIRED_VALIDATED_SKINS,
    EvidenceQualificationProfile,
    qualification_reasons,
)


PRIVATE_RAW_KEYS = {
    "author",
    "author_id",
    "location",
    "member",
    "screen_name",
    "source_location",
    "user",
    "user_id",
    "user_name",
    "username",
}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _private_raw_key(value: Any) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).casefold() in PRIVATE_RAW_KEYS:
                return str(key)
            nested = _private_raw_key(child)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for child in value:
            nested = _private_raw_key(child)
            if nested:
                return nested
    return None


class EmotionEvidenceRepository:
    """Explicit writer and provenance-preserving reader for emotion evidence."""

    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH):
        self.db_path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _read_connect(self) -> sqlite3.Connection:
        return connect_readonly(self.db_path)

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection, table: str, column: str, declaration: str
    ) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def ensure_schema(self) -> None:
        """Create the additive writer schema; never called from read paths."""
        MarketSignalRepository(self.db_path).ensure_schema()
        with closing(self._connect()) as conn:
            for column, declaration in (
                ("collection_run_id", "TEXT"),
                ("parent_external_id", "TEXT"),
                ("mapping_scope", "TEXT NOT NULL DEFAULT 'legacy_unverified'"),
                ("content_hash", "TEXT"),
                ("author_hash", "TEXT"),
                ("is_synthetic", "INTEGER NOT NULL DEFAULT 0"),
                ("quarantine_reason", "TEXT"),
                ("input_class", "TEXT NOT NULL DEFAULT 'public_comment'"),
            ):
                self._ensure_column(conn, "opinion_evidence_items", column, declaration)

            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS emotion_collection_runs (
                    run_id TEXT PRIMARY KEY,
                    protocol_version TEXT NOT NULL,
                    protocol_hash TEXT NOT NULL,
                    cohort_hash TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    input_hashes_json TEXT NOT NULL DEFAULT '{}',
                    observation_start TEXT NOT NULL,
                    observation_end TEXT NOT NULL,
                    cohort_size INTEGER NOT NULL,
                    required_validated INTEGER NOT NULL,
                    ethics_status TEXT NOT NULL DEFAULT 'pending',
                    ethics_record_hash TEXT,
                    calibration_status TEXT NOT NULL DEFAULT 'pending',
                    audit_status TEXT NOT NULL DEFAULT 'pending',
                    release_status TEXT NOT NULL DEFAULT 'staged',
                    model_name TEXT,
                    model_digest TEXT,
                    prompt_hash TEXT,
                    model_selection_status TEXT NOT NULL DEFAULT 'pending',
                    model_selection_metrics_json TEXT NOT NULL DEFAULT '{}',
                    annotation_schema_version INTEGER NOT NULL,
                    calibration_metrics_json TEXT NOT NULL DEFAULT '{}',
                    audit_metrics_json TEXT NOT NULL DEFAULT '{}',
                    artifact_manifest_sha256 TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    published_at TEXT
                );

                CREATE TABLE IF NOT EXISTS emotion_cohort_members (
                    run_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    cohort_position INTEGER NOT NULL,
                    cohort_role TEXT NOT NULL,
                    release_era TEXT NOT NULL,
                    hero_name TEXT NOT NULL,
                    skin_name TEXT NOT NULL,
                    online_date TEXT,
                    PRIMARY KEY(run_id, source_key),
                    UNIQUE(run_id, cohort_position),
                    FOREIGN KEY(run_id) REFERENCES emotion_collection_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS emotion_evidence_annotations (
                    annotation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    evidence_id INTEGER NOT NULL,
                    source_key TEXT NOT NULL,
                    annotator_id TEXT NOT NULL,
                    annotator_kind TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    relevance TEXT NOT NULL,
                    aspects_json TEXT NOT NULL DEFAULT '[]',
                    polarities_json TEXT NOT NULL DEFAULT '{}',
                    actual_use INTEGER NOT NULL DEFAULT 0,
                    confidence REAL NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    extractor_digest TEXT,
                    prompt_hash TEXT,
                    annotation_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, evidence_id, annotator_id, phase),
                    FOREIGN KEY(run_id) REFERENCES emotion_collection_runs(run_id),
                    FOREIGN KEY(evidence_id) REFERENCES opinion_evidence_items(evidence_id)
                );

                CREATE INDEX IF NOT EXISTS idx_emotion_annotations_source
                    ON emotion_evidence_annotations(run_id, source_key, phase);

                CREATE TABLE IF NOT EXISTS emotion_collection_checkpoints (
                    run_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    status TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    accepted_count INTEGER NOT NULL DEFAULT 0,
                    quarantined_count INTEGER NOT NULL DEFAULT 0,
                    proxy_used INTEGER NOT NULL DEFAULT 0,
                    error_text TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, source_key, platform),
                    FOREIGN KEY(run_id) REFERENCES emotion_collection_runs(run_id)
                );

                CREATE TABLE IF NOT EXISTS emotion_validation_results (
                    run_id TEXT NOT NULL,
                    source_key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    protocol_version TEXT NOT NULL,
                    protocol_hash TEXT NOT NULL,
                    aspect_scores_json TEXT NOT NULL,
                    aspect_author_counts_json TEXT NOT NULL,
                    relevant_comment_count INTEGER NOT NULL,
                    unique_author_count INTEGER NOT NULL,
                    parent_document_count INTEGER NOT NULL,
                    platform_count INTEGER NOT NULL,
                    feel_actual_use_author_count INTEGER NOT NULL,
                    calibration_passed INTEGER NOT NULL,
                    audit_passed INTEGER NOT NULL,
                    forbidden_inputs_json TEXT NOT NULL DEFAULT '[]',
                    validation_reasons_json TEXT NOT NULL DEFAULT '[]',
                    score INTEGER,
                    score_ci_low REAL,
                    score_ci_high REAL,
                    evidence_cutoff TEXT NOT NULL,
                    published INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    published_at TEXT,
                    PRIMARY KEY(run_id, source_key),
                    FOREIGN KEY(run_id) REFERENCES emotion_collection_runs(run_id)
                );

                CREATE INDEX IF NOT EXISTS idx_emotion_results_published
                    ON emotion_validation_results(source_key, published, published_at);
                """
            )
            for column, declaration in (
                ("protocol_hash", "TEXT NOT NULL DEFAULT ''"),
                ("input_hashes_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("model_selection_status", "TEXT NOT NULL DEFAULT 'pending'"),
                ("model_selection_metrics_json", "TEXT NOT NULL DEFAULT '{}'"),
                ("ethics_record_hash", "TEXT"),
            ):
                self._ensure_column(conn, "emotion_collection_runs", column, declaration)
            for column, declaration in (
                ("extractor_digest", "TEXT"),
                ("prompt_hash", "TEXT"),
            ):
                self._ensure_column(conn, "emotion_evidence_annotations", column, declaration)
            conn.commit()

    def create_run(
        self,
        *,
        run_id: str,
        protocol_version: str,
        protocol_hash: str,
        cohort_hash: str,
        manifest: Mapping[str, Any],
        observation_start: str,
        observation_end: str,
        annotation_schema_version: int,
        ethics_status: str = "pending",
    ) -> None:
        """Create an immutable run contract and its ordered cohort."""
        records = list(manifest.get("records") or [])
        if len(records) != COHORT_SIZE:
            raise ValueError(f"emotion cohort must contain {COHORT_SIZE} records")
        now = _now()
        self.ensure_schema()
        with closing(self._connect()) as conn:
            existing = conn.execute(
                """
                SELECT protocol_version, protocol_hash, cohort_hash, manifest_json,
                       observation_start, observation_end, ethics_status
                FROM emotion_collection_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            serialized = _json(manifest)
            if existing:
                bound = (
                    existing["protocol_version"],
                    existing["protocol_hash"],
                    existing["cohort_hash"],
                    existing["manifest_json"],
                    existing["observation_start"],
                    existing["observation_end"],
                )
                requested = (
                    protocol_version,
                    protocol_hash,
                    cohort_hash,
                    serialized,
                    observation_start,
                    observation_end,
                )
                if bound != requested:
                    raise ValueError(f"run_id already binds a different cohort: {run_id}")
                if existing["ethics_status"] != ethics_status:
                    raise ValueError(
                        "ethics status is immutable; initialize a new run for a changed determination"
                    )
                return
            conn.execute(
                """
                INSERT INTO emotion_collection_runs(
                    run_id, protocol_version, protocol_hash, cohort_hash, manifest_json,
                    observation_start, observation_end, cohort_size,
                    required_validated, ethics_status, annotation_schema_version,
                    created_at, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    protocol_version,
                    protocol_hash,
                    cohort_hash,
                    serialized,
                    observation_start,
                    observation_end,
                    len(records),
                    REQUIRED_VALIDATED_SKINS,
                    ethics_status,
                    annotation_schema_version,
                    now,
                    now,
                ),
            )
            for position, record in enumerate(records, start=1):
                conn.execute(
                    """
                    INSERT INTO emotion_cohort_members(
                        run_id, source_key, cohort_position, cohort_role,
                        release_era, hero_name, skin_name, online_date
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(record["source_key"]),
                        position,
                        str(record["cohort_role"]),
                        str(record["release_era"]),
                        str(record["hero_name"]),
                        str(record["skin_name"]),
                        str(record.get("online_date") or "") or None,
                    ),
                )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_collection_runs"):
                    return None
                row = conn.execute(
                    "SELECT * FROM emotion_collection_runs WHERE run_id = ?", (run_id,)
                ).fetchone()
        except sqlite3.Error:
            return None
        if row is None:
            return None
        result = dict(row)
        for key in (
            "manifest_json",
            "input_hashes_json",
            "model_selection_metrics_json",
            "calibration_metrics_json",
            "audit_metrics_json",
        ):
            result[key.removesuffix("_json")] = _decode(result.pop(key), {})
        return result

    def bind_input_hash(self, run_id: str, name: str, digest: str) -> None:
        """Bind a named immutable input digest to a run."""
        if not name or not digest:
            raise ValueError("input name and digest are required")
        self.ensure_schema()
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT input_hashes_json FROM emotion_collection_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"emotion run not found: {run_id}")
            hashes = _decode(row["input_hashes_json"], {})
            existing = hashes.get(name)
            if existing and existing != digest:
                raise ValueError(f"input hash is immutable for {name}")
            hashes[name] = digest
            conn.execute(
                """
                UPDATE emotion_collection_runs
                SET input_hashes_json = ?, updated_at = ? WHERE run_id = ?
                """,
                (_json(hashes), _now(), run_id),
            )
            conn.commit()

    def record_ethics_status(
        self, run_id: str, *, status: str, record_hash: str
    ) -> None:
        if status not in {"ready", "exempt", "not_applicable"}:
            raise ValueError("ethics status must be ready, exempt, or not_applicable")
        if not record_hash:
            raise ValueError("ethics determination record hash is required")
        self.ensure_schema()
        with closing(self._connect()) as conn:
            row = conn.execute(
                """
                SELECT ethics_status, ethics_record_hash, release_status
                FROM emotion_collection_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"emotion run not found: {run_id}")
            if row["ethics_status"] != "pending":
                if (row["ethics_status"], row["ethics_record_hash"]) != (
                    status,
                    record_hash,
                ):
                    raise ValueError("ethics determination is immutable once recorded")
                return
            if row["release_status"] == "published":
                raise ValueError("cannot change ethics status after publication")
            conn.execute(
                """
                UPDATE emotion_collection_runs
                SET ethics_status = ?, ethics_record_hash = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (status, record_hash, _now(), run_id),
            )
            conn.commit()

    def list_cohort_members(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_cohort_members"):
                    return []
                return [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT * FROM emotion_cohort_members
                        WHERE run_id = ? ORDER BY cohort_position
                        """,
                        (run_id,),
                    ).fetchall()
                ]
        except sqlite3.Error:
            return []

    def save_collection_checkpoint(
        self,
        *,
        run_id: str,
        source_key: str,
        platform: str,
        status: str,
        query_hash: str,
        accepted_count: int,
        quarantined_count: int,
        proxy_used: bool,
        error_text: str | None = None,
    ) -> None:
        if status not in {"completed", "failed"}:
            raise ValueError("checkpoint status must be completed or failed")
        self.ensure_schema()
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO emotion_collection_checkpoints(
                    run_id, source_key, platform, status, query_hash,
                    accepted_count, quarantined_count, proxy_used,
                    error_text, updated_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, source_key, platform) DO UPDATE SET
                    status = excluded.status,
                    query_hash = excluded.query_hash,
                    accepted_count = excluded.accepted_count,
                    quarantined_count = excluded.quarantined_count,
                    proxy_used = excluded.proxy_used,
                    error_text = excluded.error_text,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    source_key,
                    platform,
                    status,
                    query_hash,
                    int(accepted_count),
                    int(quarantined_count),
                    int(proxy_used),
                    error_text,
                    _now(),
                ),
            )
            conn.commit()

    def collection_checkpoints(self, run_id: str) -> list[dict[str, Any]]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_collection_checkpoints"):
                    return []
                return [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT * FROM emotion_collection_checkpoints
                        WHERE run_id = ? ORDER BY source_key, platform
                        """,
                        (run_id,),
                    ).fetchall()
                ]
        except sqlite3.Error:
            return []

    def add_evidence(self, item: Mapping[str, Any]) -> int:
        """Insert an immutable sanitized evidence item, idempotently."""
        self.ensure_schema()
        required = (
            "run_id",
            "source_key",
            "platform",
            "external_id",
            "parent_external_id",
            "mapping_scope",
            "content_hash",
            "author_hash",
            "text",
        )
        missing = [name for name in required if not item.get(name)]
        if missing:
            raise ValueError("evidence item missing fields: " + ",".join(missing))
        private_key = _private_raw_key(item.get("raw") or {})
        if private_key:
            raise ValueError(f"raw evidence contains private identity key: {private_key}")
        quarantine_reason = item.get("quarantine_reason")
        input_class = str(item.get("input_class") or "public_comment")
        if bool(item.get("is_synthetic")):
            quarantine_reason = quarantine_reason or "synthetic_record"
            input_class = "synthetic"
        now = _now()
        with closing(self._connect()) as conn:
            member = conn.execute(
                """
                SELECT 1 FROM emotion_cohort_members
                WHERE run_id = ? AND source_key = ?
                """,
                (item["run_id"], item["source_key"]),
            ).fetchone()
            if member is None:
                raise ValueError("evidence target is outside the run's frozen cohort")
            existing = conn.execute(
                """
                SELECT evidence_id, content_hash FROM opinion_evidence_items
                WHERE source_key = ? AND platform = ? AND external_id = ?
                """,
                (item["source_key"], item["platform"], item["external_id"]),
            ).fetchone()
            if existing:
                if existing["content_hash"] != item["content_hash"]:
                    raise ValueError("existing evidence identity has different content hash")
                return int(existing["evidence_id"])
            cursor = conn.execute(
                """
                INSERT INTO opinion_evidence_items(
                    source_key, platform, external_id, url, title, author,
                    published_at, text, metrics_json, aspect_tags, raw_json,
                    collected_at, collection_run_id, parent_external_id,
                    mapping_scope, content_hash, author_hash, is_synthetic,
                    quarantine_reason, input_class
                ) VALUES(?, ?, ?, ?, ?, NULL, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item["source_key"],
                    item["platform"],
                    item["external_id"],
                    item.get("url"),
                    item.get("title"),
                    item.get("published_at"),
                    item["text"],
                    _json(item.get("metrics") or {}),
                    _json(item.get("raw") or {}),
                    now,
                    item["run_id"],
                    item["parent_external_id"],
                    item["mapping_scope"],
                    item["content_hash"],
                    item["author_hash"],
                    int(bool(item.get("is_synthetic"))),
                    quarantine_reason,
                    input_class,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid)

    def add_annotation(self, annotation: Mapping[str, Any]) -> int:
        """Insert an immutable annotation; exact repeats are idempotent."""
        self.ensure_schema()
        now = _now()
        values = (
            str(annotation["run_id"]),
            int(annotation["evidence_id"]),
            str(annotation["source_key"]),
            str(annotation["annotator_id"]),
            str(annotation["annotator_kind"]),
            str(annotation["phase"]),
            str(annotation["relevance"]),
            _json(annotation.get("aspects") or []),
            _json(annotation.get("polarities") or {}),
            int(bool(annotation.get("actual_use"))),
            float(annotation.get("confidence") or 0.0),
            str(annotation.get("notes") or ""),
            annotation.get("extractor_digest"),
            annotation.get("prompt_hash"),
            str(annotation["annotation_hash"]),
            now,
        )
        with closing(self._connect()) as conn:
            evidence = conn.execute(
                """
                SELECT source_key, collection_run_id FROM opinion_evidence_items
                WHERE evidence_id = ?
                """,
                (values[1],),
            ).fetchone()
            if evidence is None or (
                evidence["source_key"], evidence["collection_run_id"]
            ) != (values[2], values[0]):
                raise ValueError("annotation is not bound to the run evidence target")
            existing = conn.execute(
                """
                SELECT annotation_id, annotation_hash
                FROM emotion_evidence_annotations
                WHERE run_id = ? AND evidence_id = ? AND annotator_id = ? AND phase = ?
                """,
                (values[0], values[1], values[3], values[5]),
            ).fetchone()
            if existing:
                if existing["annotation_hash"] != values[14]:
                    raise ValueError("annotation is immutable; conflicting revision rejected")
                return int(existing["annotation_id"])
            cursor = conn.execute(
                """
                INSERT INTO emotion_evidence_annotations(
                    run_id, evidence_id, source_key, annotator_id, annotator_kind,
                    phase, relevance, aspects_json, polarities_json, actual_use,
                    confidence, notes, extractor_digest, prompt_hash,
                    annotation_hash, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            conn.commit()
            return int(cursor.lastrowid)

    def list_annotated_evidence(
        self,
        run_id: str,
        source_key: str,
        *,
        annotator_kind: str = "adjudicated",
    ) -> list[dict[str, Any]]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_evidence_annotations"):
                    return []
                rows = conn.execute(
                    """
                    SELECT e.evidence_id, e.source_key, e.platform,
                           e.parent_external_id, e.mapping_scope, e.author_hash,
                           e.is_synthetic, e.quarantine_reason, e.input_class,
                           a.relevance, a.aspects_json, a.polarities_json,
                           a.actual_use, a.confidence
                    FROM opinion_evidence_items e
                    JOIN emotion_evidence_annotations a
                      ON a.evidence_id = e.evidence_id
                    WHERE a.run_id = ? AND a.source_key = ?
                      AND a.annotator_kind = ?
                    ORDER BY e.evidence_id
                    """,
                    (run_id, source_key, annotator_kind),
                ).fetchall()
        except sqlite3.Error:
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["aspects"] = _decode(item.pop("aspects_json"), [])
            item["polarities"] = _decode(item.pop("polarities_json"), {})
            result.append(item)
        return result

    def list_review_candidates(
        self, run_id: str, source_keys: Iterable[str]
    ) -> list[dict[str, Any]]:
        keys = list(dict.fromkeys(source_keys))
        if not keys:
            return []
        placeholders = ",".join("?" for _ in keys)
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "opinion_evidence_items"):
                    return []
                rows = conn.execute(
                    f"""
                    SELECT e.evidence_id, e.source_key, e.platform,
                           e.parent_external_id, e.title AS parent_title,
                           e.published_at, e.text, e.content_hash,
                           m.hero_name, m.skin_name, m.cohort_position
                    FROM opinion_evidence_items e
                    JOIN emotion_cohort_members m
                      ON m.run_id = e.collection_run_id
                     AND m.source_key = e.source_key
                    WHERE e.collection_run_id = ?
                      AND e.source_key IN ({placeholders})
                      AND e.mapping_scope = 'exact_skin'
                      AND e.is_synthetic = 0
                      AND e.quarantine_reason IS NULL
                    ORDER BY m.cohort_position, e.evidence_id
                    """,
                    [run_id, *keys],
                ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]

    def list_annotation_candidates(
        self,
        run_id: str,
        *,
        source_keys: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        keys = list(dict.fromkeys(source_keys or []))
        key_clause = ""
        params: list[Any] = [run_id]
        if keys:
            key_clause = " AND e.source_key IN (" + ",".join("?" for _ in keys) + ")"
            params.extend(keys)
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "opinion_evidence_items"):
                    return []
                rows = conn.execute(
                    f"""
                    SELECT e.evidence_id, e.source_key, e.platform,
                           e.parent_external_id, e.mapping_scope, e.title AS parent_title,
                           e.published_at, e.text, e.content_hash, e.author_hash,
                           e.is_synthetic, e.quarantine_reason, e.input_class,
                           m.hero_name, m.skin_name, m.online_date, m.cohort_position
                    FROM opinion_evidence_items e
                    JOIN emotion_cohort_members m
                      ON m.run_id = e.collection_run_id
                     AND m.source_key = e.source_key
                    WHERE e.collection_run_id = ? {key_clause}
                    ORDER BY m.cohort_position, e.evidence_id
                    """,
                    params,
                ).fetchall()
        except sqlite3.Error:
            return []
        return [dict(row) for row in rows]

    def list_annotations(
        self,
        run_id: str,
        *,
        phase: str | None = None,
        annotator_id: str | None = None,
        annotator_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        where = ["run_id = ?"]
        params: list[Any] = [run_id]
        for column, value in (
            ("phase", phase),
            ("annotator_id", annotator_id),
            ("annotator_kind", annotator_kind),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_evidence_annotations"):
                    return []
                rows = conn.execute(
                    f"""
                    SELECT evidence_id, source_key, annotator_id, annotator_kind,
                           phase, relevance, aspects_json, polarities_json,
                           actual_use, confidence, notes, extractor_digest,
                           prompt_hash, annotation_hash
                    FROM emotion_evidence_annotations
                    WHERE {' AND '.join(where)}
                    ORDER BY evidence_id
                    """,
                    params,
                ).fetchall()
        except sqlite3.Error:
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["aspects"] = _decode(item.pop("aspects_json"), [])
            item["polarities"] = _decode(item.pop("polarities_json"), {})
            result.append(item)
        return result

    def save_validation_result(self, profile: EvidenceQualificationProfile) -> None:
        self.ensure_schema()
        reasons = qualification_reasons(profile, require_published=False)
        status = "eligible" if not reasons else "insufficient_market_evidence"
        profile.validation_reasons = reasons
        now = _now()
        with closing(self._connect()) as conn:
            member = conn.execute(
                """
                SELECT c.protocol_version, c.protocol_hash
                FROM emotion_collection_runs c
                JOIN emotion_cohort_members m ON m.run_id = c.run_id
                WHERE c.run_id = ? AND m.source_key = ?
                """,
                (profile.run_id, profile.source_key),
            ).fetchone()
            if member is None:
                raise ValueError("validation result is outside the frozen cohort")
            if (
                member["protocol_version"], member["protocol_hash"]
            ) != (profile.protocol_version, profile.protocol_hash):
                raise ValueError("validation result protocol differs from the run contract")
            conn.execute(
                """
                INSERT INTO emotion_validation_results(
                    run_id, source_key, status, protocol_version, protocol_hash,
                    aspect_scores_json, aspect_author_counts_json,
                    relevant_comment_count, unique_author_count,
                    parent_document_count, platform_count,
                    feel_actual_use_author_count, calibration_passed, audit_passed,
                    forbidden_inputs_json, validation_reasons_json, score,
                    score_ci_low, score_ci_high, evidence_cutoff, published, created_at
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(run_id, source_key) DO UPDATE SET
                    status = excluded.status,
                    protocol_version = excluded.protocol_version,
                    protocol_hash = excluded.protocol_hash,
                    aspect_scores_json = excluded.aspect_scores_json,
                    aspect_author_counts_json = excluded.aspect_author_counts_json,
                    relevant_comment_count = excluded.relevant_comment_count,
                    unique_author_count = excluded.unique_author_count,
                    parent_document_count = excluded.parent_document_count,
                    platform_count = excluded.platform_count,
                    feel_actual_use_author_count = excluded.feel_actual_use_author_count,
                    calibration_passed = excluded.calibration_passed,
                    audit_passed = excluded.audit_passed,
                    forbidden_inputs_json = excluded.forbidden_inputs_json,
                    validation_reasons_json = excluded.validation_reasons_json,
                    score = excluded.score,
                    score_ci_low = excluded.score_ci_low,
                    score_ci_high = excluded.score_ci_high,
                    evidence_cutoff = excluded.evidence_cutoff,
                    published = 0,
                    published_at = NULL,
                    created_at = excluded.created_at
                """,
                (
                    profile.run_id,
                    profile.source_key,
                    status,
                    profile.protocol_version,
                    profile.protocol_hash,
                    _json(profile.aspect_scores),
                    _json(profile.aspect_author_counts),
                    profile.relevant_comment_count,
                    profile.unique_author_count,
                    profile.parent_document_count,
                    profile.platform_count,
                    profile.feel_actual_use_author_count,
                    int(profile.calibration_passed),
                    int(profile.audit_passed),
                    _json(profile.forbidden_inputs),
                    _json(reasons),
                    profile.score,
                    profile.score_ci_low,
                    profile.score_ci_high,
                    profile.evidence_cutoff,
                    now,
                ),
            )
            conn.commit()

    def set_review_gate(
        self,
        run_id: str,
        *,
        gate: str,
        passed: bool,
        metrics: Mapping[str, Any],
        model_name: str | None = None,
        model_digest: str | None = None,
        prompt_hash: str | None = None,
    ) -> None:
        if gate not in {"calibration", "audit"}:
            raise ValueError("gate must be calibration or audit")
        self.ensure_schema()
        status_column = f"{gate}_status"
        metrics_column = f"{gate}_metrics_json"
        assignments = [f"{status_column} = ?", f"{metrics_column} = ?", "updated_at = ?"]
        values: list[Any] = ["passed" if passed else "failed", _json(metrics), _now()]
        for column, value in (
            ("model_name", model_name),
            ("model_digest", model_digest),
            ("prompt_hash", prompt_hash),
        ):
            if value is not None:
                assignments.append(f"{column} = ?")
                values.append(value)
        values.append(run_id)
        with closing(self._connect()) as conn:
            current = conn.execute(
                f"SELECT {status_column} FROM emotion_collection_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise ValueError(f"emotion run not found: {run_id}")
            if current[status_column] != "pending":
                raise ValueError(f"{gate} gate is immutable once evaluated")
            cursor = conn.execute(
                f"UPDATE emotion_collection_runs SET {', '.join(assignments)} WHERE run_id = ?",
                values,
            )
            conn.commit()

    def freeze_model_selection(
        self,
        run_id: str,
        *,
        model_name: str,
        model_digest: str,
        prompt_hash: str,
        metrics: Mapping[str, Any],
    ) -> None:
        """Freeze the development-only extractor choice before locked review."""
        if not all((model_name, model_digest, prompt_hash)):
            raise ValueError("model name, digest and prompt hash are required")
        self.ensure_schema()
        with closing(self._connect()) as conn:
            run = conn.execute(
                """
                SELECT model_name, model_digest, prompt_hash, model_selection_status,
                       calibration_status
                FROM emotion_collection_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"emotion run not found: {run_id}")
            if run["model_selection_status"] == "frozen":
                if (
                    run["model_name"], run["model_digest"], run["prompt_hash"]
                ) != (model_name, model_digest, prompt_hash):
                    raise ValueError("model selection is immutable after freezing")
                return
            if run["calibration_status"] != "pending":
                raise ValueError("cannot select a model after locked calibration")
            conn.execute(
                """
                UPDATE emotion_collection_runs
                SET model_name = ?, model_digest = ?, prompt_hash = ?,
                    model_selection_status = 'frozen',
                    model_selection_metrics_json = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    model_name,
                    model_digest,
                    prompt_hash,
                    _json(metrics),
                    _now(),
                    run_id,
                ),
            )
            conn.commit()

    def bind_validation_artifact(self, run_id: str, digest: str) -> None:
        if not digest:
            raise ValueError("validation artifact digest is required")
        self.ensure_schema()
        with closing(self._connect()) as conn:
            run = conn.execute(
                """
                SELECT release_status FROM emotion_collection_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if run is None:
                raise ValueError(f"emotion run not found: {run_id}")
            if run["release_status"] == "published":
                raise ValueError("published validation artifact is immutable")
            conn.execute(
                """
                UPDATE emotion_collection_runs
                SET artifact_manifest_sha256 = ?, updated_at = ? WHERE run_id = ?
                """,
                (digest, _now(), run_id),
            )
            conn.commit()

    def list_run_profiles(self, run_id: str) -> list[EvidenceQualificationProfile]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_validation_results"):
                    return []
                rows = conn.execute(
                    """
                    SELECT * FROM emotion_validation_results
                    WHERE run_id = ? ORDER BY source_key
                    """,
                    (run_id,),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [self._profile_from_row(row) for row in rows]

    def publish_run(self, run_id: str) -> dict[str, int | str]:
        """Atomically publish an already-qualified run or fail without changes."""
        self.ensure_schema()
        now = _now()
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute(
                "SELECT * FROM emotion_collection_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError(f"emotion run not found: {run_id}")
            if run["ethics_status"] not in {"ready", "exempt", "not_applicable"}:
                raise ValueError("ethics/privacy gate has not been recorded")
            if not run["ethics_record_hash"]:
                raise ValueError("ethics/privacy determination record is not bound")
            if run["model_selection_status"] != "frozen":
                raise ValueError("development model selection is not frozen")
            if run["calibration_status"] != "passed":
                raise ValueError("calibration gate has not passed")
            if run["audit_status"] != "passed":
                raise ValueError("production audit gate has not passed")
            if not run["artifact_manifest_sha256"]:
                raise ValueError("validation artifact is not bound to the run")
            member_count = conn.execute(
                "SELECT COUNT(*) FROM emotion_cohort_members WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            if member_count != int(run["cohort_size"]):
                raise ValueError("cohort membership is incomplete")
            eligible_count = conn.execute(
                """
                SELECT COUNT(*) FROM emotion_validation_results
                WHERE run_id = ? AND status = 'eligible'
                  AND validation_reasons_json = '[]'
                """,
                (run_id,),
            ).fetchone()[0]
            if eligible_count < int(run["required_validated"]):
                raise ValueError(
                    f"release gate failed: {eligible_count}/{run['required_validated']} eligible"
                )
            conn.execute(
                """
                UPDATE emotion_validation_results
                SET published = CASE WHEN status = 'eligible' THEN 1 ELSE 0 END,
                    published_at = CASE WHEN status = 'eligible' THEN ? ELSE NULL END
                WHERE run_id = ?
                """,
                (now, run_id),
            )
            conn.execute(
                """
                UPDATE emotion_collection_runs
                SET release_status = 'published', published_at = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (now, now, run_id),
            )
            conn.commit()
        return {
            "run_id": run_id,
            "cohort_size": int(member_count),
            "validated_count": int(eligible_count),
            "release_status": "published",
        }

    @staticmethod
    def _profile_from_row(row: sqlite3.Row) -> EvidenceQualificationProfile:
        return EvidenceQualificationProfile.from_mapping(
            {
                **dict(row),
                "aspect_scores": _decode(row["aspect_scores_json"], {}),
                "aspect_author_counts": _decode(row["aspect_author_counts_json"], {}),
                "forbidden_inputs": _decode(row["forbidden_inputs_json"], []),
                "validation_reasons": _decode(row["validation_reasons_json"], []),
            }
        )

    def latest_published_profile(
        self, source_key: str
    ) -> EvidenceQualificationProfile | None:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_validation_results") or not table_exists(
                    conn, "emotion_collection_runs"
                ):
                    return None
                active = conn.execute(
                    """
                    SELECT run_id, release_status, required_validated
                    FROM emotion_collection_runs
                    ORDER BY COALESCE(published_at, updated_at) DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if active is None or active["release_status"] != "published":
                    return None
                published_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM emotion_validation_results
                    WHERE run_id = ? AND published = 1 AND status = 'eligible'
                    """,
                    (active["run_id"],),
                ).fetchone()[0]
                if published_count < int(active["required_validated"]):
                    return None
                row = conn.execute(
                    """
                    SELECT * FROM emotion_validation_results
                    WHERE run_id = ? AND source_key = ?
                      AND published = 1 AND status = 'eligible'
                    LIMIT 1
                    """,
                    (active["run_id"], source_key),
                ).fetchone()
        except sqlite3.Error:
            return None
        return None if row is None else self._profile_from_row(row)

    def list_latest_published_profiles(self) -> dict[str, EvidenceQualificationProfile]:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_validation_results") or not table_exists(
                    conn, "emotion_collection_runs"
                ):
                    return {}
                active = conn.execute(
                    """
                    SELECT run_id, release_status, required_validated
                    FROM emotion_collection_runs
                    ORDER BY COALESCE(published_at, updated_at) DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if active is None or active["release_status"] != "published":
                    return {}
                published_count = conn.execute(
                    """
                    SELECT COUNT(*) FROM emotion_validation_results
                    WHERE run_id = ? AND published = 1 AND status = 'eligible'
                    """,
                    (active["run_id"],),
                ).fetchone()[0]
                if published_count < int(active["required_validated"]):
                    return {}
                rows = conn.execute(
                    """
                    SELECT * FROM emotion_validation_results
                    WHERE run_id = ? AND published = 1 AND status = 'eligible'
                    """,
                    (active["run_id"],),
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {
            str(row["source_key"]): self._profile_from_row(row)
            for row in rows
        }

    def latest_run_profile(
        self, source_key: str
    ) -> EvidenceQualificationProfile | None:
        """Return the active run's diagnostic profile, published or not."""
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_validation_results"):
                    return None
                row = conn.execute(
                    """
                    SELECT r.* FROM emotion_validation_results r
                    JOIN emotion_collection_runs c ON c.run_id = r.run_id
                    WHERE r.source_key = ?
                    ORDER BY COALESCE(c.published_at, c.updated_at) DESC,
                             c.run_id DESC LIMIT 1
                    """,
                    (source_key,),
                ).fetchone()
        except sqlite3.Error:
            return None
        return None if row is None else self._profile_from_row(row)

    def list_active_run_profiles(self) -> dict[str, EvidenceQualificationProfile]:
        """Return diagnostic profiles for the latest run without publishing them."""
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_validation_results"):
                    return {}
                active = conn.execute(
                    """
                    SELECT run_id FROM emotion_collection_runs
                    ORDER BY COALESCE(published_at, updated_at) DESC, run_id DESC LIMIT 1
                    """
                ).fetchone()
                if active is None:
                    return {}
                rows = conn.execute(
                    """
                    SELECT * FROM emotion_validation_results
                    WHERE run_id = ? ORDER BY source_key
                    """,
                    (active["run_id"],),
                ).fetchall()
        except sqlite3.Error:
            return {}
        return {
            str(row["source_key"]): self._profile_from_row(row) for row in rows
        }

    def latest_cohort_status(self) -> dict[str, Any] | None:
        try:
            with closing(self._read_connect()) as conn:
                if not table_exists(conn, "emotion_collection_runs"):
                    return None
                row = conn.execute(
                    """
                    SELECT run_id, protocol_version, observation_start,
                           observation_end, cohort_size, required_validated,
                           release_status, published_at
                    FROM emotion_collection_runs
                    ORDER BY COALESCE(published_at, updated_at) DESC, run_id DESC
                    LIMIT 1
                    """
                ).fetchone()
                if row is None:
                    return None
                result = dict(row)
                result["validated_count"] = conn.execute(
                    """
                    SELECT COUNT(*) FROM emotion_validation_results
                    WHERE run_id = ? AND published = 1 AND status = 'eligible'
                    """,
                    (row["run_id"],),
                ).fetchone()[0]
                return result
        except sqlite3.Error:
            return None

    def list_public_evidence(self, source_key: str) -> list[dict[str, Any]]:
        """Return sanitized exact-mapped evidence from the latest scored run."""
        profile = self.latest_run_profile(source_key)
        if profile is None:
            return []
        try:
            with closing(self._read_connect()) as conn:
                rows = conn.execute(
                    """
                    SELECT evidence_id, platform, parent_external_id, url, title,
                           published_at, text, metrics_json, content_hash,
                           mapping_scope
                    FROM opinion_evidence_items
                    WHERE collection_run_id = ? AND source_key = ?
                      AND mapping_scope = 'exact_skin' AND is_synthetic = 0
                      AND quarantine_reason IS NULL
                    ORDER BY published_at DESC, evidence_id DESC
                    """,
                    (profile.run_id, source_key),
                ).fetchall()
        except sqlite3.Error:
            return []
        result = []
        for row in rows:
            item = dict(row)
            item["metrics"] = _decode(item.pop("metrics_json"), {})
            result.append(item)
        return result
