"""Tests for Weibo comment synthesizer."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from data.weibo_comment_synthesizer import (
    SyntheticWeiboRepository,
    WeiboCommentSynthesizer,
    SkinCommentProfile,
    make_synth_batch_id,
)


@pytest.fixture
def test_db(tmp_path: Path) -> Path:
    """Create a temporary test database with weibo_comments data."""
    db_path = tmp_path / "test_weibo.sqlite3"

    # Create empty DB with synth schema
    repo = SyntheticWeiboRepository(db_path)
    repo.ensure_schema()

    # Copy weibo_comments table from real DB for style corpus tests
    real_db = Path("data/weibo_comments/weibo.sqlite3")
    if real_db.exists():
        import sqlite3

        # Attach real DB and copy weibo_comments table
        conn = sqlite3.connect(db_path)
        conn.execute(f"ATTACH DATABASE '{real_db}' AS real_db")
        conn.execute(
            """
            CREATE TABLE weibo_comments AS
            SELECT * FROM real_db.weibo_comments
            """
        )
        conn.execute("DETACH DATABASE real_db")
        conn.close()

    return db_path


def test_repository_roundtrip(test_db: Path) -> None:
    """Test saving and retrieving batch + comments."""
    repo = SyntheticWeiboRepository(test_db)

    batch = {
        "synth_batch": "test-batch-001",
        "seed": 42,
        "model": "qwen2.5vl:3b",
        "generator_version": "test-v1",
        "prompt_version": "v1",
        "params_json": '{"per_skin": 2}',
        "skin_count": 1,
        "per_skin": 2,
        "row_count": 2,
        "rejection_count": 0,
        "cache_hits": 0,
        "cache_misses": 2,
        "elapsed_seconds": 5.0,
        "created_at": time.time(),
    }

    comments = [
        {
            "skin_key": "0097-56406",
            "hero_name": "赵云",
            "skin_name": "测试皮肤",
            "quality": "史诗",
            "text": "这个皮肤特效不错",
            "text_hash": "hash1",
            "temperature": 0.8,
            "prompt_hash": "ph1",
            "cached": 0,
            "attempt": 1,
            "created_at": time.time(),
        },
        {
            "skin_key": "0097-56406",
            "hero_name": "赵云",
            "skin_name": "测试皮肤",
            "quality": "史诗",
            "text": "手感很好",
            "text_hash": "hash2",
            "temperature": 0.8,
            "prompt_hash": "ph2",
            "cached": 0,
            "attempt": 1,
            "created_at": time.time(),
        },
    ]

    repo.save_batch(batch)
    repo.save_comments("test-batch-001", comments)

    loaded_batch = repo.get_batch("test-batch-001")
    assert loaded_batch is not None
    assert loaded_batch["seed"] == 42
    assert loaded_batch["row_count"] == 2

    loaded_comments = repo.comments("test-batch-001")
    assert len(loaded_comments) == 2
    assert loaded_comments[0]["text"] == "这个皮肤特效不错"


def test_repository_uniqueness(test_db: Path) -> None:
    """Test that duplicate text_hash is rejected."""
    repo = SyntheticWeiboRepository(test_db)

    batch = {
        "synth_batch": "test-dup",
        "seed": 42,
        "model": "test",
        "generator_version": "test",
        "prompt_version": "v1",
        "params_json": "{}",
        "skin_count": 1,
        "per_skin": 2,
        "row_count": 2,
        "rejection_count": 0,
        "cache_hits": 0,
        "cache_misses": 2,
        "elapsed_seconds": 1.0,
        "created_at": time.time(),
    }

    repo.save_batch(batch)

    # Insert first comment
    comment1 = {
        "skin_key": "0097-56406",
        "text": "unique text",
        "text_hash": "same_hash",
        "temperature": 0.8,
        "cached": 0,
        "attempt": 1,
        "created_at": time.time(),
    }
    repo.save_comments("test-dup", [comment1])

    # Try to insert duplicate — should be silently ignored
    comment2 = {
        "skin_key": "0097-56406",
        "text": "duplicate text",
        "text_hash": "same_hash",  # Same hash
        "temperature": 0.8,
        "cached": 0,
        "attempt": 1,
        "created_at": time.time(),
    }
    repo.save_comments("test-dup", [comment2])

    # Should only have 1 comment
    comments = repo.comments("test-dup")
    assert len(comments) == 1
    assert comments[0]["text"] == "unique text"


def test_atomic_generation_preserves_inserted_count_and_rejects_batch_id_reuse(
    test_db: Path,
) -> None:
    repo = SyntheticWeiboRepository(test_db)
    batch = {
        "synth_batch": "atomic-batch",
        "seed": 42,
        "model": "test",
        "generator_version": "test",
        "prompt_version": "v1",
        "params_json": "{}",
        "skin_count": 1,
        "per_skin": 2,
        "row_count": 999,
        "rejection_count": 0,
        "cache_hits": 0,
        "cache_misses": 999,
        "elapsed_seconds": 1.0,
        "created_at": time.time(),
    }
    rows = [
        {
            "skin_key": "0097-56406", "text": "first generated comment",
            "text_hash": "same", "temperature": 0.8, "cached": 0,
            "attempt": 1, "created_at": time.time(),
        },
        {
            "skin_key": "0097-56406", "text": "duplicate generated comment",
            "text_hash": "same", "temperature": 0.8, "cached": 0,
            "attempt": 1, "created_at": time.time(),
        },
    ]

    assert repo.save_generation(batch, rows) == 1
    loaded = repo.get_batch("atomic-batch")
    assert loaded is not None
    assert loaded["row_count"] == 1
    assert loaded["cache_misses"] == 1
    with pytest.raises(sqlite3.IntegrityError):
        repo.save_generation(batch, rows)
    assert len(repo.comments("atomic-batch")) == 1


def test_batch_ids_are_unique_per_invocation() -> None:
    first = make_synth_batch_id(20260827)
    second = make_synth_batch_id(20260827)
    assert first.startswith("synth-wc-20260827-")
    assert first != second


def test_list_batches(test_db: Path) -> None:
    """Test listing batches."""
    repo = SyntheticWeiboRepository(test_db)

    # Create two batches
    for i in range(2):
        batch = {
            "synth_batch": f"batch-{i}",
            "seed": i,
            "model": "test",
            "generator_version": "test",
            "prompt_version": "v1",
            "params_json": "{}",
            "skin_count": 1,
            "per_skin": 1,
            "row_count": 1,
            "rejection_count": 0,
            "cache_hits": 0,
            "cache_misses": 1,
            "elapsed_seconds": 1.0,
            "created_at": time.time() + i,
        }
        repo.save_batch(batch)

    batches = repo.list_batches()
    assert len(batches) == 2
    # Should be ordered by created_at DESC
    assert batches[0]["synth_batch"] == "batch-1"


def test_build_style_corpus(test_db: Path) -> None:
    """Test computing corpus-wide style stats."""
    synth = WeiboCommentSynthesizer(db_path=test_db)
    style = synth.build_style_corpus()

    assert "avg_len" in style
    assert "emoji_rate" in style
    assert "total_comments" in style
    assert style["total_comments"] > 0


def test_hash_text() -> None:
    """Test text hashing."""
    synth = WeiboCommentSynthesizer()
    h1 = synth._hash_text("hello")
    h2 = synth._hash_text("hello")
    h3 = synth._hash_text("world")

    assert h1 == h2  # Same text → same hash
    assert h1 != h3  # Different text → different hash
    assert len(h1) == 64  # SHA-256 hex digest


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
