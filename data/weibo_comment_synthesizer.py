"""Synthetic Weibo comment generator using local Ollama.

Mirrors the architecture of ``invoice_synthesizer.py``:
- ``SyntheticWeiboRepository`` — SQLite persistence for batch metadata + generated comments
- ``WeiboCommentSynthesizer`` — orchestration: load real comments, sample examples, generate via Ollama

Storage: separate ``synth_*`` tables in ``data/weibo_comments/weibo.sqlite3``
to avoid contaminating real scraped comments.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from vlm.ollama_client import OllamaClient
from vlm.text_prompts import build_generation_prompt

DEFAULT_WEIBO_DB = Path("data/weibo_comments/weibo.sqlite3")
GENERATOR_VERSION = "weibo-comment-synth-v1"


def make_synth_batch_id(seed: int) -> str:
    """Return a unique, traceable identifier for one generator invocation."""
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"synth-wc-{seed}-{timestamp}-{uuid.uuid4().hex[:8]}"


# ═══════════════════════════════════════════════════════════════════════════
# Data models
# ═══════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SkinCommentProfile:
    """Bundle of skin metadata + real comment examples for few-shot prompting."""

    skin_key: str
    hero_name: str
    skin_name: str
    quality: str
    example_comments: list[str]
    fallback_used: bool = False


# ═══════════════════════════════════════════════════════════════════════════
# Repository
# ═══════════════════════════════════════════════════════════════════════════


class SyntheticWeiboRepository:
    """SQLite persistence for synth_weibo_batches + synthetic_weibo_comments."""

    def __init__(self, db_path: Path | str = DEFAULT_WEIBO_DB):
        self.db_path = Path(db_path)
        self.ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _rows(cursor: sqlite3.Cursor) -> list[dict[str, Any]]:
        return [dict(row) for row in cursor.fetchall()]

    def ensure_schema(self) -> None:
        """Create tables if they don't exist."""
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS synth_weibo_batches (
                    synth_batch TEXT PRIMARY KEY,
                    seed INTEGER NOT NULL,
                    model TEXT NOT NULL,
                    generator_version TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    skin_count INTEGER NOT NULL,
                    per_skin INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    rejection_count INTEGER NOT NULL,
                    cache_hits INTEGER NOT NULL DEFAULT 0,
                    cache_misses INTEGER NOT NULL DEFAULT 0,
                    elapsed_seconds REAL NOT NULL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS synthetic_weibo_comments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    synth_batch TEXT NOT NULL,
                    skin_key TEXT NOT NULL,
                    hero_name TEXT,
                    skin_name TEXT,
                    quality TEXT,
                    text TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    temperature REAL NOT NULL,
                    prompt_hash TEXT,
                    cached INTEGER NOT NULL DEFAULT 0,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (synth_batch) REFERENCES synth_weibo_batches(synth_batch),
                    UNIQUE(synth_batch, text_hash)
                );

                CREATE INDEX IF NOT EXISTS idx_synth_weibo_comments_batch
                    ON synthetic_weibo_comments(synth_batch);
                CREATE INDEX IF NOT EXISTS idx_synth_weibo_comments_skin
                    ON synthetic_weibo_comments(skin_key);
                """
            )

    def save_batch(self, batch: dict[str, Any]) -> None:
        """Insert or replace a batch metadata row."""
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO synth_weibo_batches
                    (synth_batch, seed, model, generator_version, prompt_version,
                     params_json, skin_count, per_skin, row_count, rejection_count,
                     cache_hits, cache_misses, elapsed_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["synth_batch"],
                    batch["seed"],
                    batch["model"],
                    batch["generator_version"],
                    batch["prompt_version"],
                    batch["params_json"],
                    batch["skin_count"],
                    batch["per_skin"],
                    batch["row_count"],
                    batch["rejection_count"],
                    batch.get("cache_hits", 0),
                    batch.get("cache_misses", 0),
                    batch["elapsed_seconds"],
                    batch["created_at"],
                ),
            )
            conn.commit()

    def save_comments(self, synth_batch: str, rows: list[dict[str, Any]]) -> None:
        """Insert generated comments (skip duplicates via UNIQUE constraint)."""
        with closing(self._connect()) as conn:
            for row in rows:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO synthetic_weibo_comments
                            (synth_batch, skin_key, hero_name, skin_name, quality,
                             text, text_hash, temperature, prompt_hash, cached, attempt, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            synth_batch,
                            row["skin_key"],
                            row.get("hero_name"),
                            row.get("skin_name"),
                            row.get("quality"),
                            row["text"],
                            row["text_hash"],
                            row["temperature"],
                            row.get("prompt_hash"),
                            row.get("cached", 0),
                            row.get("attempt", 1),
                            row["created_at"],
                        ),
                    )
                except sqlite3.IntegrityError:
                    # Duplicate text_hash within this batch — skip
                    pass
            conn.commit()

    def save_generation(self, batch: dict[str, Any], rows: list[dict[str, Any]]) -> int:
        """Atomically persist one new batch and return its inserted row count.

        A batch identifier is immutable: attempting to reuse it raises instead
        of replacing its metadata while appending another set of comments.
        """
        with closing(self._connect()) as conn:
            conn.execute(
                """
                INSERT INTO synth_weibo_batches
                    (synth_batch, seed, model, generator_version, prompt_version,
                     params_json, skin_count, per_skin, row_count, rejection_count,
                     cache_hits, cache_misses, elapsed_seconds, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch["synth_batch"],
                    batch["seed"],
                    batch["model"],
                    batch["generator_version"],
                    batch["prompt_version"],
                    batch["params_json"],
                    batch["skin_count"],
                    batch["per_skin"],
                    0,
                    batch["rejection_count"],
                    batch.get("cache_hits", 0),
                    0,
                    batch["elapsed_seconds"],
                    batch["created_at"],
                ),
            )

            inserted = 0
            for row in rows:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO synthetic_weibo_comments
                        (synth_batch, skin_key, hero_name, skin_name, quality,
                         text, text_hash, temperature, prompt_hash, cached, attempt, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch["synth_batch"],
                        row["skin_key"],
                        row.get("hero_name"),
                        row.get("skin_name"),
                        row.get("quality"),
                        row["text"],
                        row["text_hash"],
                        row["temperature"],
                        row.get("prompt_hash"),
                        row.get("cached", 0),
                        row.get("attempt", 1),
                        row["created_at"],
                    ),
                )
                inserted += cursor.rowcount

            conn.execute(
                """
                UPDATE synth_weibo_batches
                SET row_count = ?, cache_misses = ?
                WHERE synth_batch = ?
                """,
                (inserted, inserted, batch["synth_batch"]),
            )
            conn.commit()
            return inserted

    def get_batch(self, synth_batch: str) -> dict[str, Any] | None:
        """Fetch one batch metadata row."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT * FROM synth_weibo_batches WHERE synth_batch = ?",
                (synth_batch,),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def list_batches(self) -> list[dict[str, Any]]:
        """List all synth batches (newest first)."""
        with closing(self._connect()) as conn:
            cursor = conn.execute(
                "SELECT * FROM synth_weibo_batches ORDER BY created_at DESC"
            )
            return self._rows(cursor)

    def comments(
        self, synth_batch: str, *, skin_key: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch comments for a batch, optionally filtered by skin_key."""
        with closing(self._connect()) as conn:
            if skin_key:
                cursor = conn.execute(
                    """
                    SELECT * FROM synthetic_weibo_comments
                    WHERE synth_batch = ? AND skin_key = ?
                    ORDER BY id
                    """,
                    (synth_batch, skin_key),
                )
            else:
                cursor = conn.execute(
                    """
                    SELECT * FROM synthetic_weibo_comments
                    WHERE synth_batch = ?
                    ORDER BY id
                    """,
                    (synth_batch,),
                )
            return self._rows(cursor)


# ═══════════════════════════════════════════════════════════════════════════
# Synthesizer
# ═══════════════════════════════════════════════════════════════════════════


class WeiboCommentSynthesizer:
    """Orchestrate synthetic Weibo comment generation."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_WEIBO_DB,
        ollama: OllamaClient | None = None,
    ):
        self.db_path = Path(db_path)
        self.repo = SyntheticWeiboRepository(db_path)
        self.ollama = ollama or OllamaClient()
        self._real_comment_texts: set[str] | None = None

    # ── helpers ──

    def _load_real_comment_texts(self) -> set[str]:
        """Load all real comment texts for deduplication."""
        if self._real_comment_texts is None:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT text FROM weibo_comments")
            self._real_comment_texts = {row["text"] for row in cursor.fetchall()}
            conn.close()
        return self._real_comment_texts

    @staticmethod
    def _hash_text(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    # ── core methods ──

    def build_style_corpus(self) -> dict[str, Any]:
        """Compute corpus-wide style stats from real comments.

        Returns dict with keys: avg_len, emoji_rate, question_rate, etc.
        """
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT text, like_count FROM weibo_comments")
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {"avg_len": 50, "emoji_rate": 0.1, "question_rate": 0.1}

        texts = [r["text"] for r in rows]
        lens = [len(t) for t in texts]

        # Simple emoji detection (common emoji ranges)
        import re
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F680-\U0001F6FF"  # transport & map
            "\U0001F1E0-\U0001F1FF"  # flags
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "]+",
            flags=re.UNICODE,
        )

        emoji_count = sum(1 for t in texts if emoji_pattern.search(t))
        question_count = sum(1 for t in texts if "？" in t or "?" in t)

        return {
            "avg_len": int(np.mean(lens)),
            "median_len": int(np.median(lens)),
            "emoji_rate": round(emoji_count / len(texts), 2),
            "question_rate": round(question_count / len(texts), 2),
            "total_comments": len(texts),
        }

    def build_skin_profiles(
        self, skin_keys: list[str] | None = None
    ) -> list[SkinCommentProfile]:
        """Resolve skin_key → hero/skin/quality + example comments.

        Joins community_labels (skin_key↔mid) with weibo_comments (mid→text)
        and skins (source_key→hero_name, skin_name, quality).
        """
        import json

        # Load community labels with skin_key
        labels_path = Path("data/community_labels/community_labels.jsonl")
        skin_key_to_mids: dict[str, list[str]] = {}

        with open(labels_path) as f:
            for line in f:
                label = json.loads(line)
                sk = label.get("skin_key")
                if sk:
                    skin_key_to_mids.setdefault(sk, []).append(label["mid"])

        # Filter to requested skin_keys if provided
        if skin_keys:
            skin_key_to_mids = {
                k: v for k, v in skin_key_to_mids.items() if k in skin_keys
            }

        if not skin_key_to_mids:
            return []

        # Load skin metadata from skins.sqlite3
        skins_db = Path("data/wzry_skins/skins.sqlite3")
        skin_meta: dict[str, dict[str, Any]] = {}

        if skins_db.exists():
            conn = sqlite3.connect(skins_db)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT source_key, hero_name, skin_name, quality, acquire_method FROM skins"
            )
            for row in cursor.fetchall():
                skin_meta[row["source_key"]] = {
                    "hero_name": row["hero_name"],
                    "skin_name": row["skin_name"],
                    "quality": row["quality"] or "未知",
                    "acquire_method": row["acquire_method"] or "",
                }
            conn.close()

        # Build profiles
        profiles: list[SkinCommentProfile] = []
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        for sk, mids in skin_key_to_mids.items():
            # Fetch comments for this skin's MIDs
            placeholders = ",".join("?" * len(mids))
            cursor = conn.execute(
                f"SELECT text, like_count FROM weibo_comments WHERE mid IN ({placeholders})",
                mids,
            )
            comments = [(r["text"], r["like_count"]) for r in cursor.fetchall()]

            # Get skin metadata
            meta = skin_meta.get(sk, {})
            hero_name = meta.get("hero_name", sk.split("-")[0])
            skin_name = meta.get("skin_name", sk.split("-")[1] if "-" in sk else "")
            quality = meta.get("quality", "未知")

            # Sample examples
            rng = np.random.default_rng(20260808)  # Fixed seed for example sampling
            examples = self._sample_from_comments(comments, k=5, rng=rng)
            fallback_used = len(examples) < 3

            profiles.append(
                SkinCommentProfile(
                    skin_key=sk,
                    hero_name=hero_name,
                    skin_name=skin_name,
                    quality=quality,
                    example_comments=examples,
                    fallback_used=fallback_used,
                )
            )

        conn.close()
        return profiles

    @staticmethod
    def _sample_from_comments(
        comments: list[tuple[str, int]], k: int = 5, rng: np.random.Generator | None = None
    ) -> list[str]:
        """Stratified sample: top by likes + random middle + low."""
        if not comments:
            return []

        if rng is None:
            rng = np.random.default_rng()

        # Sort by like_count descending
        sorted_comments = sorted(comments, key=lambda x: x[1], reverse=True)

        if len(sorted_comments) <= k:
            return [c[0] for c in sorted_comments]

        # Stratified: top-2, 2 random middle, 1 low
        top = sorted_comments[:2]
        middle = sorted_comments[2 : len(sorted_comments) // 2]
        low = sorted_comments[len(sorted_comments) // 2 :]

        result = [c[0] for c in top]

        if middle:
            mid_idx = rng.choice(len(middle), size=min(2, len(middle)), replace=False)
            result.extend([middle[i][0] for i in mid_idx])

        if low:
            low_idx = rng.choice(len(low), size=min(1, len(low)), replace=False)
            result.extend([low[i][0] for i in low_idx])

        return result[:k]

    def sample_examples(
        self, skin_key: str, k: int = 5, rng: np.random.Generator | None = None
    ) -> list[str]:
        """Stratified sample of real comments for this skin.

        Public wrapper around _sample_from_comments for external use.
        """
        import json

        # Find MIDs for this skin_key
        labels_path = Path("data/community_labels/community_labels.jsonl")
        mids = []
        with open(labels_path) as f:
            for line in f:
                label = json.loads(line)
                if label.get("skin_key") == skin_key:
                    mids.append(label["mid"])

        if not mids:
            return []

        # Fetch comments
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(mids))
        cursor = conn.execute(
            f"SELECT text, like_count FROM weibo_comments WHERE mid IN ({placeholders})",
            mids,
        )
        comments = [(r["text"], r["like_count"]) for r in cursor.fetchall()]
        conn.close()

        return self._sample_from_comments(comments, k=k, rng=rng)

    async def generate_one(
        self,
        profile: SkinCommentProfile,
        *,
        style_spec: dict[str, Any],
        temperature: float,
        model: str,
        rng: np.random.Generator,
    ) -> dict[str, Any] | None:
        """Build prompt, call Ollama, validate output. Returns comment dict or None."""
        import asyncio

        # Shuffle examples deterministically
        examples = list(profile.example_comments)
        rng.shuffle(examples)

        # Build prompt
        skin_meta = {
            "hero_name": profile.hero_name,
            "skin_name": profile.skin_name,
            "quality": profile.quality,
        }

        system, user_prompt = build_generation_prompt(skin_meta, examples[:5])

        # Compute prompt hash for caching
        prompt_hash = hashlib.sha256(
            f"{model}|{system}|{user_prompt}|{temperature}".encode()
        ).hexdigest()

        # Call Ollama
        try:
            result = await self.ollama.chat_text(
                model=model,
                prompt=user_prompt,
                system=system,
                temperature=temperature,
            )
        except Exception as e:
            print(f"  ⚠️  Ollama call failed: {e}")
            return None

        text = result["content"].strip()

        # Validate
        if not text or len(text) < 10 or len(text) > 140:
            return None

        # Check for duplicates with real comments
        real_texts = self._load_real_comment_texts()
        if text in real_texts:
            return None

        text_hash = self._hash_text(text)

        return {
            "skin_key": profile.skin_key,
            "hero_name": profile.hero_name,
            "skin_name": profile.skin_name,
            "quality": profile.quality,
            "text": text,
            "text_hash": text_hash,
            "temperature": temperature,
            "prompt_hash": prompt_hash,
            "cached": 0,
            "attempt": 1,
            "created_at": time.time(),
        }

    async def generate_batch(
        self,
        skin_key: str,
        n: int,
        *,
        seed: int,
        temperature: float = 0.8,
        model: str = "qwen2.5vl:3b",
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Generate N comments for one skin with semaphore + retry."""
        import asyncio

        profiles = self.build_skin_profiles([skin_key])
        if not profiles:
            return {"skin_key": skin_key, "generated": 0, "rejected": 0}

        profile = profiles[0]
        style_spec = self.build_style_corpus()
        rng = np.random.default_rng(seed)

        if dry_run:
            return {
                "skin_key": skin_key,
                "generated": 0,
                "rejected": 0,
                "dry_run": True,
            }

        # Generate with semaphore for concurrency control
        semaphore = asyncio.Semaphore(4)
        results: list[dict[str, Any] | None] = []

        async def gen_one() -> dict[str, Any] | None:
            async with semaphore:
                for attempt in range(3):  # Max 3 retries
                    result = await self.generate_one(
                        profile,
                        style_spec=style_spec,
                        temperature=temperature,
                        model=model,
                        rng=rng,
                    )
                    if result:
                        result["attempt"] = attempt + 1
                        return result
                return None

        tasks = [gen_one() for _ in range(n)]
        results = await asyncio.gather(*tasks)

        valid = [r for r in results if r is not None]
        rejected = len(results) - len(valid)

        # Save to repo
        if valid:
            self.repo.save_comments(f"temp-{seed}", valid)

        return {
            "skin_key": skin_key,
            "generated": len(valid),
            "rejected": rejected,
            "comments": valid,
        }

    def generate(
        self,
        *,
        per_skin: int = 20,
        seed: int = 20260808,
        skin_keys: list[str] | None = None,
        temperature: float = 0.8,
        model: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Top-level entry: generate for all eligible skins, save batch, return report."""
        import asyncio

        model = model or self.ollama.settings.weibo_synth_model

        # Build batch ID
        batch_id = make_synth_batch_id(seed)

        # Get skin profiles
        profiles = self.build_skin_profiles(skin_keys)

        if not profiles:
            return {
                "synth_batch": batch_id,
                "status": "no_eligible_skins",
                "row_count": 0,
            }

        started = time.perf_counter()
        rng = np.random.default_rng(seed)

        # Generate for all skins
        all_comments: list[dict[str, Any]] = []
        total_rejected = 0

        if dry_run:
            print(f"🔍 Dry run: would generate {per_skin} comments × {len(profiles)} skins")
            for p in profiles:
                print(f"  - {p.skin_key}: {p.hero_name} - {p.skin_name} ({p.quality})")
            return {
                "synth_batch": batch_id,
                "status": "dry_run",
                "skin_count": len(profiles),
                "per_skin": per_skin,
                "row_count": 0,
            }

        # Run async generation
        async def run_all() -> list[dict[str, Any]]:
            results = []
            for profile in profiles:
                print(f"Generating {per_skin} comments for {profile.skin_key}...")
                batch_result = await self.generate_batch(
                    profile.skin_key,
                    per_skin,
                    seed=seed,
                    temperature=temperature,
                    model=model,
                    dry_run=dry_run,
                )
                results.append(batch_result)
            return results

        batch_results = asyncio.run(run_all())

        # Aggregate results
        for br in batch_results:
            all_comments.extend(br.get("comments", []))
            total_rejected += br.get("rejected", 0)

        elapsed = time.perf_counter() - started

        # Persist metadata and comments as one transaction. The stored count,
        # rather than attempted generation count, is the reported batch size.
        batch_meta = {
            "synth_batch": batch_id,
            "seed": seed,
            "model": model,
            "generator_version": GENERATOR_VERSION,
            "prompt_version": "v1",
            "params_json": json.dumps({
                "per_skin": per_skin,
                "temperature": temperature,
                "skin_keys": skin_keys,
            }),
            "skin_count": len(profiles),
            "per_skin": per_skin,
            "row_count": 0,
            "rejection_count": total_rejected,
            "cache_hits": 0,  # TODO: track in M5
            "cache_misses": len(all_comments),
            "elapsed_seconds": round(elapsed, 2),
            "created_at": time.time(),
        }

        inserted = self.repo.save_generation(batch_meta, all_comments)
        total_rejected += len(all_comments) - inserted

        return {
            "synth_batch": batch_id,
            "status": "success",
            "skin_count": len(profiles),
            "per_skin": per_skin,
            "row_count": inserted,
            "rejection_count": total_rejected,
            "elapsed_seconds": round(elapsed, 2),
        }
