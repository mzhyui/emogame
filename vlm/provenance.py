"""Deterministic producer signatures for auditable VLM cache entries."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest of UTF-8 text."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json_sha256(value: Any) -> str:
    """Hash JSON-compatible data with stable ordering and separators."""
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def producer_signature(
    *,
    tier: str,
    model: str,
    prompt_sha256: str,
    schema_version: str,
    upstream_sha256: str = "",
) -> str:
    """Bind a cached result to the producer configuration that created it."""
    return canonical_json_sha256(
        {
            "tier": tier,
            "model": model,
            "prompt_sha256": prompt_sha256,
            "schema_version": schema_version,
            "upstream_sha256": upstream_sha256,
        }
    )


def tier_provenance(
    *,
    tier: str,
    model: str,
    prompt_sha256: str,
    producer_signature_value: str,
    producer_source: str,
    schema_version: str,
    upstream_sha256: str = "",
) -> dict[str, Any]:
    """Build non-secret producer metadata stored with a tier result."""
    return {
        "tier": tier,
        "model": model,
        "prompt_sha256": prompt_sha256,
        "producer_signature": producer_signature_value,
        "producer_source": producer_source,
        "retrieval_source": "live",
        "schema_version": schema_version,
        "upstream_sha256": upstream_sha256,
    }
