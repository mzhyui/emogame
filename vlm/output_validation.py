"""Strict validation for structured local VLM output.

The Pydantic output models intentionally provide defaults so downstream
feature consumers can represent unavailable fields.  Those defaults must not
also turn a missing or malformed model reply into a successful local result.
This module enforces the prompt contract at the model boundary instead.
"""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import ValidationError

from vlm.schemas import L1Output, L2Output


L1_REQUIRED_FIELDS = (
    "rarity_tier",
    "dominant_colors",
    "scene_type",
    "character_ratio",
    "effect_density",
    "confidence",
)
L2_SCORE_FIELDS = (
    "model_detail",
    "effect_quality",
    "color_scheme",
    "composition",
    "uniqueness",
    "costume_design",
    "background_quality",
)
L2_REQUIRED_FIELDS = (*L2_SCORE_FIELDS, "ui_elements")


class LocalOutputValidationError(ValueError):
    """A local model reply did not satisfy its required structured contract."""

    def __init__(self, reason: str, diagnostic: dict[str, Any]) -> None:
        super().__init__(reason)
        self.reason = reason
        self.diagnostic = diagnostic


def _content_diagnostic(response: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, non-content diagnostics for a model response."""
    content = response.get("content", "")
    content = content if isinstance(content, str) else ""
    return {
        "content_chars": len(content),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "parsed_json_present": "parsed_json" in response,
        "parsed_json_type": type(response.get("parsed_json")).__name__,
    }


def _require_mapping(
    payload: Any,
    *,
    tier: str,
    required_fields: tuple[str, ...],
    diagnostic: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        diagnostic["provided_type"] = type(payload).__name__
        raise LocalOutputValidationError(f"{tier}_non_object_json", diagnostic)

    missing = [field for field in required_fields if field not in payload]
    if missing:
        diagnostic["provided_fields"] = sorted(str(key) for key in payload)
        diagnostic["missing_fields"] = missing
        raise LocalOutputValidationError(f"{tier}_missing_required_fields", diagnostic)
    return payload


def _schema_error(
    exc: ValidationError, *, tier: str, diagnostic: dict[str, Any]
) -> LocalOutputValidationError:
    diagnostic["schema_errors"] = [
        {"field": ".".join(str(part) for part in error["loc"]), "type": error["type"]}
        for error in exc.errors()
    ]
    return LocalOutputValidationError(f"{tier}_schema_validation_failed", diagnostic)


def validate_l1_payload(payload: Any, *, diagnostic: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate a complete L1 object rather than filling schema defaults."""
    info = dict(diagnostic or {})
    values = _require_mapping(
        payload, tier="l1", required_fields=L1_REQUIRED_FIELDS, diagnostic=info
    )
    try:
        validated = L1Output(**values).model_dump()
    except ValidationError as exc:
        raise _schema_error(exc, tier="l1", diagnostic=info) from exc

    empty_labels = [
        field
        for field in ("rarity_tier", "scene_type", "effect_density")
        if not validated[field]
    ]
    if empty_labels:
        info["empty_fields"] = empty_labels
        raise LocalOutputValidationError("l1_empty_required_labels", info)
    return validated


def validate_l2_payload(payload: Any, *, diagnostic: dict[str, Any] | None = None) -> dict[str, Any]:
    """Validate L2 and enforce the prompt's 1--10 scoring range."""
    info = dict(diagnostic or {})
    values = _require_mapping(
        payload, tier="l2", required_fields=L2_REQUIRED_FIELDS, diagnostic=info
    )
    try:
        validated = L2Output(**values).model_dump()
    except ValidationError as exc:
        raise _schema_error(exc, tier="l2", diagnostic=info) from exc

    out_of_contract = [
        field
        for field in L2_SCORE_FIELDS
        if not 1.0 <= float(validated[field]) <= 10.0
    ]
    if out_of_contract:
        info["out_of_contract_score_fields"] = out_of_contract
        raise LocalOutputValidationError("l2_scores_outside_prompt_range", info)
    return validated


def validate_l1_response(response: dict[str, Any]) -> dict[str, Any]:
    """Validate one L1 chat response and describe malformed replies safely."""
    diagnostic = _content_diagnostic(response)
    if "parsed_json" not in response:
        reason = "l1_empty_response" if diagnostic["content_chars"] == 0 else "l1_unparseable_json"
        raise LocalOutputValidationError(reason, diagnostic)
    return validate_l1_payload(response["parsed_json"], diagnostic=diagnostic)


def validate_l2_response(response: dict[str, Any]) -> dict[str, Any]:
    """Validate one L2 chat response and describe malformed replies safely."""
    diagnostic = _content_diagnostic(response)
    if "parsed_json" not in response:
        reason = "l2_empty_response" if diagnostic["content_chars"] == 0 else "l2_unparseable_json"
        raise LocalOutputValidationError(reason, diagnostic)
    return validate_l2_payload(response["parsed_json"], diagnostic=diagnostic)
