"""Deterministic workflow helpers for the P1 emotion-evidence cohort."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable, Mapping

from models.emotion_evidence import (
    ANNOTATION_SCHEMA_VERSION,
    COHORT_SIZE,
    OBSERVATION_END,
    OBSERVATION_START,
    PROTOCOL_VERSION,
    SUBJECTIVE_ASPECTS,
    VALID_RELEVANCE,
    evidence_content_hash,
    pseudonymous_author_hash,
    sanitize_public_text,
)


NEW_COHORT_QUOTAS = {
    "pre_2021": 20,
    "2021_2024": 20,
    "missing_date": 10,
}

ASPECT_QUERY_TERMS: dict[str, tuple[str, ...]] = {
    "visual_appeal": ("特效", "原画", "造型", "好看"),
    "in_game_feel": ("手感", "局内", "实战", "音效"),
    "craftsmanship_quality": ("品质", "建模", "制作", "敷衍"),
    "collection_value": ("限定", "收藏", "返场", "绝版"),
    "value_for_money": ("性价比", "值不值", "价格", "点券"),
    "purchase_intent": ("必买", "入手", "不买", "抽", "保底"),
}

POSITIVE_TERMS = (
    "好看", "帅", "美", "喜欢", "期待", "必买", "必冲", "入手",
    "值", "香", "爱了", "高级", "顶", "绝", "流畅", "舒服", "良心",
)
NEGATIVE_TERMS = (
    "丑", "难看", "敷衍", "不值", "贵", "垃圾", "失望", "别买",
    "不买", "买不起", "烂", "一般", "卡", "僵硬", "割韭菜", "劝退",
)
ACTUAL_USE_TERMS = ("用过", "买了", "入手了", "玩了", "实战", "局内", "手感")
SPAM_PATTERNS = (
    re.compile(r"^(转发微博|转发|打卡|沙发|来了|第一)$"),
    re.compile(r"^[\W_]{1,8}$", re.UNICODE),
)

MODEL_SYSTEM_PROMPT = """你是公开社区评论的证据编码器。只判断给定评论是否明确讨论目标皮肤，
以及它表达的六个固定方面。不得使用官方品质、价格先验、销量、收入或其他评论。
relevance 只能是 relevant、irrelevant、uncertain。aspects 必须是 JSON 数组，元素只能来自：
visual_appeal,in_game_feel,craftsmanship_quality,collection_value,value_for_money,purchase_intent。
polarities 必须是 JSON 对象，并且键必须与 aspects 数组完全相同；每个值必须是 -2,-1,0,1,2。
若 relevance 为 irrelevant 或 uncertain，必须输出 aspects: [] 和 polarities: {}。
actual_use 仅在评论明确描述已使用或局内体验时为 true。confidence 必须是 0 到 1 的数字。
必须只输出且完整输出这五个键：relevance、aspects、polarities、actual_use、confidence。
无关示例：{"relevance":"irrelevant","aspects":[],"polarities":{},"actual_use":false,"confidence":0.9}
相关示例：{"relevance":"relevant","aspects":["visual_appeal"],"polarities":{"visual_appeal":2},"actual_use":false,"confidence":0.9}
证据不足时选择 uncertain。只输出一个 JSON 对象，不要解释。"""

MODEL_THINK = False
MODEL_GENERATION_OPTIONS = {"temperature": 0, "num_predict": 256}

MODEL_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relevance", "aspects", "polarities", "actual_use", "confidence"],
    "properties": {
        "relevance": {
            "type": "string",
            "enum": ["relevant", "irrelevant", "uncertain"],
        },
        "aspects": {
            "type": "array",
            "items": {"type": "string", "enum": list(SUBJECTIVE_ASPECTS)},
            "uniqueItems": True,
        },
        "polarities": {
            "type": "object",
            "properties": {
                aspect: {"type": "integer", "minimum": -2, "maximum": 2}
                for aspect in SUBJECTIVE_ASPECTS
            },
            "additionalProperties": False,
        },
        "actual_use": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


def model_prompt_hash() -> str:
    return sha256_json(
        {
            "system": MODEL_SYSTEM_PROMPT,
            "schema": MODEL_RESPONSE_SCHEMA,
            "think": MODEL_THINK,
            "generation_options": MODEL_GENERATION_OPTIONS,
            "annotation_schema_version": ANNOTATION_SCHEMA_VERSION,
        }
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def release_era(online_date: Any) -> str:
    text = str(online_date or "").strip()
    match = re.match(r"^(\d{4})", text)
    if not match:
        return "missing_date"
    year = int(match.group(1))
    if year <= 2020:
        return "pre_2021"
    if year <= 2024:
        return "2021_2024"
    return "2025_2026"


def _stable_rank(seed: int, source_key: str) -> str:
    return hashlib.sha256(f"{seed}|{source_key}".encode("utf-8")).hexdigest()


def build_cohort_manifest(
    skins: Iterable[Mapping[str, Any]],
    warm_source_keys: Iterable[str],
    *,
    seed: int = 20260902,
) -> dict[str, Any]:
    """Build the fixed warm-50 plus era-balanced new-50 cohort."""
    by_key = {str(row["source_key"]): dict(row) for row in skins}
    warm_keys = list(dict.fromkeys(str(key) for key in warm_source_keys))
    if len(warm_keys) != 50:
        raise ValueError("warm-start cohort must contain exactly 50 unique keys")
    missing = [key for key in warm_keys if key not in by_key]
    if missing:
        raise ValueError("warm-start keys missing from skins database: " + ",".join(missing))

    records: list[dict[str, Any]] = []
    used_heroes: set[str] = set()
    for key in warm_keys:
        skin = by_key[key]
        hero = str(skin.get("hero_name") or "").strip()
        skin_name = str(skin.get("skin_name") or "").strip()
        if not hero or not skin_name:
            raise ValueError(f"incomplete warm-start identity: {key}")
        records.append(
            {
                "source_key": key,
                "hero_name": hero,
                "skin_name": skin_name,
                "online_date": str(skin.get("online_date") or ""),
                "cohort_role": "warm_start",
                "release_era": release_era(skin.get("online_date")),
            }
        )
        used_heroes.add(hero)

    warm_set = set(warm_keys)
    candidates = [
        row
        for key, row in by_key.items()
        if key not in warm_set
        and str(row.get("hero_name") or "").strip()
        and str(row.get("skin_name") or "").strip()
        and str(row.get("hero_name") or "").strip() not in used_heroes
        and release_era(row.get("online_date")) in NEW_COHORT_QUOTAS
    ]

    for era, quota in NEW_COHORT_QUOTAS.items():
        era_candidates = sorted(
            (row for row in candidates if release_era(row.get("online_date")) == era),
            key=lambda row: _stable_rank(seed, str(row["source_key"])),
        )
        selected = []
        selected_heroes: set[str] = set()
        for skin in era_candidates:
            hero = str(skin.get("hero_name") or "").strip()
            if hero in used_heroes or hero in selected_heroes:
                continue
            selected.append(skin)
            selected_heroes.add(hero)
            if len(selected) == quota:
                break
        if len(selected) != quota:
            raise ValueError(f"unable to fill {era} quota: {len(selected)}/{quota}")
        for skin in selected:
            hero = str(skin["hero_name"]).strip()
            records.append(
                {
                    "source_key": str(skin["source_key"]),
                    "hero_name": hero,
                    "skin_name": str(skin["skin_name"]).strip(),
                    "online_date": str(skin.get("online_date") or ""),
                    "cohort_role": "coverage_extension",
                    "release_era": era,
                }
            )
            used_heroes.add(hero)

    if len(records) != COHORT_SIZE or len({row["source_key"] for row in records}) != COHORT_SIZE:
        raise ValueError("cohort construction did not produce 100 unique source keys")
    record_hash = sha256_json(records)
    return {
        "schema_version": 1,
        "protocol_version": PROTOCOL_VERSION,
        "observation_window": {"start": OBSERVATION_START, "end": OBSERVATION_END},
        "selection": {
            "seed": seed,
            "cohort_size": COHORT_SIZE,
            "warm_start_count": 50,
            "extension_quotas": NEW_COHORT_QUOTAS,
            "method": "fixed_warm_start_plus_distinct_hero_release_era_extension",
            "claim_scope": "purposeful cohort; not representative of all catalog skins",
        },
        "records_sha256": record_hash,
        "records": records,
    }


def validate_cohort_manifest(payload: Mapping[str, Any]) -> None:
    records = list(payload.get("records") or [])
    if payload.get("protocol_version") != PROTOCOL_VERSION:
        raise ValueError("unsupported emotion protocol version")
    if len(records) != COHORT_SIZE:
        raise ValueError("emotion manifest must contain 100 records")
    keys = [str(row.get("source_key") or "") for row in records]
    if not all(keys) or len(set(keys)) != COHORT_SIZE:
        raise ValueError("emotion manifest source keys must be unique and non-empty")
    if sha256_json(records) != payload.get("records_sha256"):
        raise ValueError("emotion manifest record hash mismatch")
    roles = [str(row.get("cohort_role") or "") for row in records]
    if roles.count("warm_start") != 50 or roles.count("coverage_extension") != 50:
        raise ValueError("emotion manifest must contain warm 50 and extension 50")


def parse_public_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    formats = (
        "%a %b %d %H:%M:%S %z %Y",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    if text.isdigit():
        try:
            return datetime.fromtimestamp(int(text), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    return None


def in_observation_window(value: Any) -> bool:
    parsed = parse_public_datetime(value)
    if parsed is None:
        return False
    day = parsed.date().isoformat()
    return OBSERVATION_START <= day <= OBSERVATION_END


def normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\s\-_·・•—–《》【】\[\]()（）]", "", text)


def contains_exact_name(text: Any, name: Any) -> bool:
    needle = normalized_name(name)
    return bool(needle and needle in normalized_name(text))


def is_obvious_spam(text: Any) -> bool:
    normalized = sanitize_public_text(text)
    return len(normalized) <= 2 or any(pattern.match(normalized) for pattern in SPAM_PATTERNS)


def build_sanitized_evidence_item(
    *,
    run_id: str,
    source_key: str,
    platform: str,
    parent_external_id: str,
    external_id: str,
    mapping_scope: str,
    author_value: Any,
    text: Any,
    published_at: Any,
    parent_title: Any,
    skin_name: str,
    url: str | None,
    metrics: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """Build one privacy-minimized evidence row and its quarantine reason."""
    clean_text = sanitize_public_text(text)
    clean_title = sanitize_public_text(parent_title)
    author_present = author_value not in (None, "", 0, "0")
    author_hash = pseudonymous_author_hash(
        run_id,
        platform,
        author_value if author_present else f"missing:{external_id}",
    )
    content_hash = evidence_content_hash(
        source_key, platform, parent_external_id, author_hash, clean_text
    )
    parsed = parse_public_datetime(published_at)
    quarantine: str | None = None
    if synthetic:
        quarantine = "synthetic_record"
    elif mapping_scope != "exact_skin" or not contains_exact_name(clean_title, skin_name):
        quarantine = "hero_only_or_fallback_mapping"
    elif not parent_external_id:
        quarantine = "missing_parent_document"
    elif not clean_text:
        quarantine = "empty_comment"
    elif is_obvious_spam(clean_text):
        quarantine = "spam_or_low_information"
    elif not in_observation_window(published_at):
        quarantine = "outside_observation_window"
    elif not author_present:
        quarantine = "missing_author_identity"
    safe_provenance = dict(provenance or {})
    return {
        "run_id": run_id,
        "source_key": source_key,
        "platform": platform,
        "external_id": external_id or content_hash[:32],
        "parent_external_id": parent_external_id or "missing",
        "mapping_scope": mapping_scope,
        "content_hash": content_hash,
        "author_hash": author_hash,
        "url": url,
        "title": clean_title,
        "published_at": parsed.isoformat() if parsed else None,
        "text": clean_text,
        "metrics": dict(metrics or {}),
        "raw": safe_provenance,
        "is_synthetic": bool(synthetic),
        "quarantine_reason": quarantine,
        "input_class": "synthetic" if synthetic else "public_comment",
    }


def sanitized_warm_evidence(
    payload: Mapping[str, Any],
    *,
    run_id: str,
    allowed_source_keys: set[str],
    source_artifact_sha256: str,
) -> list[dict[str, Any]]:
    """Convert target-aware warm comments into privacy-minimized rows."""
    output: list[dict[str, Any]] = []
    for target_index, target in enumerate(payload.get("targets") or []):
        source_key = str(target.get("source_key") or "")
        if source_key not in allowed_source_keys:
            raise ValueError(f"warm evidence target is outside cohort: {source_key}")
        target_scope = str(target.get("match_scope") or "none")
        posts = {
            str(post.get("mid") or ""): post for post in target.get("posts") or []
        }
        for comment_index, comment in enumerate(target.get("comments") or []):
            parent_id = str(comment.get("post_mid") or "")
            post = posts.get(parent_id) or {}
            author_value = comment.get("user_id") or comment.get("user")
            output.append(
                build_sanitized_evidence_item(
                    run_id=run_id,
                    source_key=source_key,
                    platform="weibo",
                    external_id=str(comment.get("comment_id") or ""),
                    parent_external_id=parent_id,
                    mapping_scope=(
                        "exact_skin" if target_scope == "exact_skin" else "hero_fallback"
                    ),
                    author_value=author_value,
                    text=comment.get("text"),
                    published_at=comment.get("created_at"),
                    parent_title=post.get("title"),
                    skin_name=str(target.get("skin_name") or ""),
                    url=f"https://m.weibo.cn/detail/{parent_id}" if parent_id else None,
                    metrics={
                        "like_count": int(comment.get("like_count") or 0),
                        "reply_count": int(comment.get("total_number") or 0),
                    },
                    provenance={
                        "source_artifact_sha256": source_artifact_sha256,
                        "target_index": target_index,
                        "comment_index": comment_index,
                        "post_source_type": str(comment.get("post_source_type") or ""),
                    },
                )
            )
    return output


def deterministic_annotation(
    *,
    source_key: str,
    hero_name: str,
    skin_name: str,
    text: str,
    mapping_scope: str,
) -> dict[str, Any]:
    """High-precision local baseline; uncertain rows never contribute."""
    normalized = sanitize_public_text(text)
    target_mentioned = skin_name in normalized or (
        hero_name in normalized and any(
            term in normalized for terms in ASPECT_QUERY_TERMS.values() for term in terms
        )
    )
    if mapping_scope != "exact_skin":
        relevance = "irrelevant"
    elif target_mentioned:
        relevance = "relevant"
    else:
        relevance = "uncertain"
    aspects = [
        aspect
        for aspect, terms in ASPECT_QUERY_TERMS.items()
        if any(term in normalized for term in terms)
    ]
    positive = sum(term in normalized for term in POSITIVE_TERMS)
    negative = sum(term in normalized for term in NEGATIVE_TERMS)
    if positive and not negative:
        polarity = 2 if positive >= 2 else 1
    elif negative and not positive:
        polarity = -2 if negative >= 2 else -1
    else:
        polarity = 0
    if relevance != "relevant":
        aspects = []
    return {
        "source_key": source_key,
        "relevance": relevance,
        "aspects": aspects,
        "polarities": {aspect: polarity for aspect in aspects},
        "actual_use": (
            "in_game_feel" in aspects
            and any(term in normalized for term in ACTUAL_USE_TERMS)
        ),
        "confidence": 1.0 if relevance != "uncertain" else 0.5,
        "notes": "deterministic high-precision baseline",
    }


def annotation_hash(annotation: Mapping[str, Any]) -> str:
    fields = {
        "run_id": annotation.get("run_id"),
        "evidence_id": annotation.get("evidence_id"),
        "source_key": annotation.get("source_key"),
        "annotator_id": annotation.get("annotator_id"),
        "annotator_kind": annotation.get("annotator_kind"),
        "phase": annotation.get("phase"),
        "relevance": annotation.get("relevance"),
        "aspects": sorted(annotation.get("aspects") or []),
        "polarities": dict(sorted((annotation.get("polarities") or {}).items())),
        "actual_use": bool(annotation.get("actual_use")),
        "confidence": float(annotation.get("confidence") or 0),
        "notes": str(annotation.get("notes") or ""),
        "extractor_digest": annotation.get("extractor_digest"),
        "prompt_hash": annotation.get("prompt_hash"),
    }
    return sha256_json(fields)


def validate_annotation(annotation: Mapping[str, Any]) -> None:
    relevance = str(annotation.get("relevance") or "")
    if relevance not in VALID_RELEVANCE:
        raise ValueError(f"invalid relevance: {relevance}")
    aspects = list(annotation.get("aspects") or [])
    unknown = sorted(set(aspects) - set(SUBJECTIVE_ASPECTS))
    if unknown:
        raise ValueError("unknown aspects: " + ",".join(unknown))
    polarities = dict(annotation.get("polarities") or {})
    if set(polarities) != set(aspects):
        raise ValueError("polarities must contain exactly the labelled aspects")
    for aspect, value in polarities.items():
        try:
            numeric = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid polarity for {aspect}") from exc
        if numeric < -2 or numeric > 2:
            raise ValueError(f"polarity outside -2..2 for {aspect}")
    if relevance != "relevant" and aspects:
        raise ValueError("irrelevant or uncertain annotations cannot label aspects")
    confidence = float(annotation.get("confidence") or 0)
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be in 0..1")


def review_skin_split(records: Iterable[Mapping[str, Any]]) -> dict[str, str]:
    """Choose 20 development and 10 locked skins across role/era groups."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[(str(record["cohort_role"]), str(record["release_era"]))].append(
            dict(record)
        )
    for rows in groups.values():
        rows.sort(key=lambda row: _stable_rank(20260902, str(row["source_key"])))
    ordered_groups = sorted(groups)
    selected: list[str] = []
    positions = {key: 0 for key in ordered_groups}
    while len(selected) < 30:
        progressed = False
        for key in ordered_groups:
            index = positions[key]
            if index < len(groups[key]):
                selected.append(str(groups[key][index]["source_key"]))
                positions[key] += 1
                progressed = True
                if len(selected) == 30:
                    break
        if not progressed:
            raise ValueError("unable to select 30 review skins")
    return {
        source_key: ("development" if index < 20 else "locked")
        for index, source_key in enumerate(selected)
    }


REVIEW_COLUMNS = (
    "review_id",
    "evidence_id",
    "phase",
    "source_key",
    "hero_name",
    "skin_name",
    "platform",
    "parent_external_id",
    "parent_title",
    "published_at",
    "text",
    "relevance",
    "aspects",
    "polarities",
    "actual_use",
    "confidence",
    "notes",
)


def write_review_pack(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    count = 0
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in REVIEW_COLUMNS})
            count += 1
    temporary.replace(path)
    return count


def parse_review_pack(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for raw in csv.DictReader(handle):
            aspects = [part.strip() for part in str(raw.get("aspects") or "").split("|") if part.strip()]
            polarity_values: dict[str, int] = {}
            for part in str(raw.get("polarities") or "").split("|"):
                if not part.strip():
                    continue
                name, separator, value = part.partition(":")
                if not separator:
                    raise ValueError(f"invalid polarity cell: {part}")
                polarity_values[name.strip()] = int(value.strip())
            annotation = {
                "evidence_id": int(raw["evidence_id"]),
                "source_key": str(raw["source_key"]),
                "phase": str(raw["phase"]),
                "relevance": str(raw.get("relevance") or "").strip().lower(),
                "aspects": aspects,
                "polarities": polarity_values,
                "actual_use": str(raw.get("actual_use") or "").strip().lower()
                in {"1", "true", "yes", "y"},
                "confidence": float(raw.get("confidence") or 0),
                "notes": str(raw.get("notes") or ""),
            }
            validate_annotation(annotation)
            rows.append(annotation)
    return rows


def _binary_metrics(expected: list[bool], predicted: list[bool]) -> dict[str, float]:
    tp = sum(a and b for a, b in zip(expected, predicted, strict=True))
    fp = sum(not a and b for a, b in zip(expected, predicted, strict=True))
    fn = sum(a and not b for a, b in zip(expected, predicted, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def linear_weighted_kappa(expected: list[int], predicted: list[int]) -> float:
    """Linear weighted Cohen kappa for the locked five-point scale."""
    if not expected or len(expected) != len(predicted):
        return 0.0
    categories = (-2, -1, 0, 1, 2)
    index = {value: idx for idx, value in enumerate(categories)}
    observed = [[0.0] * 5 for _ in categories]
    for left, right in zip(expected, predicted, strict=True):
        observed[index[left]][index[right]] += 1.0
    total = float(len(expected))
    left_counts = [sum(row) for row in observed]
    right_counts = [sum(observed[i][j] for i in range(5)) for j in range(5)]
    observed_disagreement = sum(
        (abs(i - j) / 4.0) * observed[i][j]
        for i in range(5)
        for j in range(5)
    ) / total
    expected_disagreement = sum(
        (abs(i - j) / 4.0) * left_counts[i] * right_counts[j]
        for i in range(5)
        for j in range(5)
    ) / (total * total)
    if expected_disagreement == 0:
        return 1.0 if observed_disagreement == 0 else 0.0
    return 1.0 - observed_disagreement / expected_disagreement


def calibration_metrics(
    truth: Iterable[Mapping[str, Any]], predictions: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    truth_by_id = {int(row["evidence_id"]): dict(row) for row in truth}
    pred_by_id = {int(row["evidence_id"]): dict(row) for row in predictions}
    common = sorted(set(truth_by_id) & set(pred_by_id))
    expected_relevance = [truth_by_id[key]["relevance"] == "relevant" for key in common]
    predicted_relevance = [pred_by_id[key]["relevance"] == "relevant" for key in common]
    relevance = _binary_metrics(expected_relevance, predicted_relevance)
    aspect_f1: dict[str, float] = {}
    expected_polarity: list[int] = []
    predicted_polarity: list[int] = []
    for aspect in SUBJECTIVE_ASPECTS:
        expected = [aspect in truth_by_id[key].get("aspects", []) for key in common]
        predicted = [aspect in pred_by_id[key].get("aspects", []) for key in common]
        aspect_f1[aspect] = _binary_metrics(expected, predicted)["f1"]
        for key in common:
            truth_polarities = truth_by_id[key].get("polarities") or {}
            prediction_polarities = pred_by_id[key].get("polarities") or {}
            if aspect in truth_polarities and aspect in prediction_polarities:
                expected_polarity.append(int(truth_polarities[aspect]))
                predicted_polarity.append(int(prediction_polarities[aspect]))
    mae = (
        mean(abs(a - b) for a, b in zip(expected_polarity, predicted_polarity, strict=True))
        if expected_polarity
        else None
    )
    metrics = {
        "n": len(common),
        "relevance_precision": round(relevance["precision"], 4),
        "relevance_recall": round(relevance["recall"], 4),
        "aspect_macro_f1": round(mean(aspect_f1.values()), 4),
        "aspect_f1": {key: round(value, 4) for key, value in aspect_f1.items()},
        "polarity_weighted_kappa": round(
            linear_weighted_kappa(expected_polarity, predicted_polarity), 4
        ),
        "polarity_mae": None if mae is None else round(mae, 4),
    }
    metrics["passed"] = bool(
        metrics["n"]
        and metrics["relevance_precision"] >= 0.90
        and metrics["relevance_recall"] >= 0.80
        and metrics["aspect_macro_f1"] >= 0.75
        and (
            metrics["polarity_weighted_kappa"] >= 0.65
            or (metrics["polarity_mae"] is not None and metrics["polarity_mae"] <= 0.5)
        )
    )
    return metrics
