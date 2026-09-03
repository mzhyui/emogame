"""Operate the fail-closed P1 community emotion-evidence workflow.

All mutating subcommands are dry-run by default and require ``--apply``.
Publication additionally creates and verifies an explicit SQLite backup.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sqlite3
import sys
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.emotion_evidence_repository import EmotionEvidenceRepository  # noqa: E402
from data.skin_repository import DEFAULT_DB_PATH, SkinRepository  # noqa: E402
from models.emotion_evidence import (  # noqa: E402
    ANNOTATION_SCHEMA_VERSION,
    OBSERVATION_END,
    OBSERVATION_START,
    PROTOCOL_VERSION,
    SUBJECTIVE_ASPECTS,
    aggregate_adjudicated_annotations,
)
from models.emotion_workflow import (  # noqa: E402
    MODEL_RESPONSE_SCHEMA,
    MODEL_GENERATION_OPTIONS,
    MODEL_SYSTEM_PROMPT,
    MODEL_THINK,
    annotation_hash,
    build_cohort_manifest,
    calibration_metrics,
    canonical_json,
    deterministic_annotation,
    model_prompt_hash,
    parse_review_pack,
    review_skin_split,
    sanitized_warm_evidence,
    sha256_file,
    sha256_json,
    validate_annotation,
    validate_cohort_manifest,
    write_review_pack,
)


DEFAULT_RUN_ID = "20260902-current100-v1"
DEFAULT_RUNS_ROOT = Path("data/emotion_evidence/runs")
DEFAULT_MANIFEST = DEFAULT_RUNS_ROOT / DEFAULT_RUN_ID / "manifest.json"
DEFAULT_PROTOCOL = Path("docs/13-emotion-evidence-protocol.md")
DEFAULT_WARM_MANIFEST = Path(
    "data/premium_pilot/runs/20260831-seed42-social-v1/manifest.json"
)
DEFAULT_WARM_COMMENTS = Path(
    "data/premium_pilot/runs/20260828-seed42-partial-v3/weibo_comments.json"
)
EXTRACTOR_IDS = ("deterministic-v1", "qwen3.5:4b", "qwen3.5:27b")
MIN_DEVELOPMENT_JUDGMENTS = 400
MIN_LOCKED_JUDGMENTS = 200


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_text_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _protocol_snapshot_path(run_id: str) -> Path:
    return DEFAULT_RUNS_ROOT / run_id / "protocol.md"


def _manifest_run_id(payload: dict[str, Any], explicit: str | None) -> str:
    return explicit or str(payload.get("run_id") or DEFAULT_RUN_ID)


def _run_contract(repo: EmotionEvidenceRepository, run_id: str) -> dict[str, Any]:
    run = repo.get_run(run_id)
    if run is None:
        raise ValueError(f"emotion run is not initialized: {run_id}")
    return run


def _extractor_prompt_hash(extractor_id: str) -> str:
    if extractor_id == "deterministic-v1":
        return sha256_json(
            {
                "extractor": extractor_id,
                "source_sha256": sha256_file(ROOT / "models/emotion_workflow.py"),
            }
        )
    return model_prompt_hash()


def _extractor_annotator_id(
    extractor_id: str, *, prompt_hash: str | None = None
) -> str:
    """Version local-model rows without changing the operator-facing model name."""
    if extractor_id == "deterministic-v1":
        return extractor_id
    bound_prompt_hash = prompt_hash or _extractor_prompt_hash(extractor_id)
    return f"{extractor_id}@{bound_prompt_hash[:12]}"


def _ollama_model_digest(host: str, model: str, timeout: float) -> str:
    request = urllib.request.Request(f"{host.rstrip('/')}/api/tags")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    for row in payload.get("models") or []:
        if row.get("name") == model or row.get("model") == model:
            digest = str(row.get("digest") or "")
            if digest:
                return digest
    raise ValueError(f"cannot resolve installed Ollama digest for {model}")


def _profile_payload(profile: Any) -> dict[str, Any]:
    payload = profile.to_dict()
    payload.pop("is_validated", None)
    return payload


def command_manifest(args: argparse.Namespace) -> int:
    warm = _load_json(args.warm_manifest)
    warm_keys = [str(row["source_key"]) for row in warm.get("records") or []]
    skins = SkinRepository(args.db).list_skins(limit=None)
    manifest = build_cohort_manifest(skins, warm_keys, seed=args.seed)
    manifest["run_id"] = args.run_id
    validate_cohort_manifest(manifest)
    protocol_hash = sha256_file(args.protocol)
    result = {
        "run_id": args.run_id,
        "cohort_size": len(manifest["records"]),
        "records_sha256": manifest["records_sha256"],
        "protocol_sha256": protocol_hash,
        "roles": dict(Counter(row["cohort_role"] for row in manifest["records"])),
        "eras": dict(Counter(row["release_era"] for row in manifest["records"])),
        "output": str(args.output),
        "applied": bool(args.apply),
    }
    if args.apply:
        _write_json_atomic(args.output, manifest)
        EmotionEvidenceRepository(args.db).create_run(
            run_id=args.run_id,
            protocol_version=PROTOCOL_VERSION,
            protocol_hash=protocol_hash,
            cohort_hash=manifest["records_sha256"],
            manifest=manifest,
            observation_start=OBSERVATION_START,
            observation_end=OBSERVATION_END,
            annotation_schema_version=ANNOTATION_SCHEMA_VERSION,
            ethics_status="pending",
        )
        _write_text_atomic(
            args.output.parent / "protocol.md",
            args.protocol.read_text(encoding="utf-8"),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_stage_warm(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    validate_cohort_manifest(manifest)
    run_id = _manifest_run_id(manifest, args.run_id)
    allowed = {str(row["source_key"]) for row in manifest["records"]}
    payload = _load_json(args.input)
    evidence = sanitized_warm_evidence(
        payload,
        run_id=run_id,
        allowed_source_keys=allowed,
        source_artifact_sha256=sha256_file(args.input),
    )
    raw_item_count = len(evidence)
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in evidence:
        unique[(item["source_key"], item["platform"], item["external_id"])] = item
    evidence = list(unique.values())
    quarantine = Counter(item.get("quarantine_reason") or "accepted" for item in evidence)
    inserted = 0
    if args.apply:
        repo = EmotionEvidenceRepository(args.db)
        _run_contract(repo, run_id)
        repo.bind_input_hash(run_id, "warm_comments", sha256_file(args.input))
        for item in evidence:
            repo.add_evidence(item)
            inserted += 1
    print(
        json.dumps(
            {
                "run_id": run_id,
                "items": len(evidence),
                "source_items": raw_item_count,
                "deduplicated_items": raw_item_count - len(evidence),
                "processed": inserted,
                "status_counts": dict(sorted(quarantine.items())),
                "contains_raw_author_fields": False,
                "applied": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_record_ethics(args: argparse.Namespace) -> int:
    digest = sha256_file(args.record)
    if args.apply:
        EmotionEvidenceRepository(args.db).record_ethics_status(
            args.run_id, status=args.status, record_hash=digest
        )
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "status": args.status,
                "record": str(args.record),
                "record_sha256": digest,
                "applied": bool(args.apply),
            },
            indent=2,
        )
    )
    return 0


def _review_rows(
    repo: EmotionEvidenceRepository,
    manifest: dict[str, Any],
    *,
    max_per_skin: int,
    phase: str,
) -> list[dict[str, Any]]:
    split = review_skin_split(manifest["records"])
    phase_keys = [key for key, assigned in split.items() if assigned == phase]
    candidates = repo.list_review_candidates(str(manifest["run_id"]), phase_keys)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in candidates:
        grouped[str(row["source_key"])].append(row)
    output: list[dict[str, Any]] = []
    for source_key in phase_keys:
        rows = sorted(grouped.get(source_key, []), key=lambda row: row["content_hash"])
        for row in rows[:max_per_skin]:
            output.append(
                {
                    "review_id": str(row["content_hash"])[:16],
                    "evidence_id": row["evidence_id"],
                    "phase": phase,
                    "source_key": source_key,
                    "hero_name": row["hero_name"],
                    "skin_name": row["skin_name"],
                    "platform": row["platform"],
                    "parent_external_id": row["parent_external_id"],
                    "parent_title": row["parent_title"],
                    "published_at": row["published_at"],
                    "text": row["text"],
                    "relevance": "",
                    "aspects": "",
                    "polarities": "",
                    "actual_use": "",
                    "confidence": "",
                    "notes": "",
                }
            )
    return output


def command_review_pack(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    validate_cohort_manifest(manifest)
    run_id = _manifest_run_id(manifest, args.run_id)
    manifest["run_id"] = run_id
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, run_id)
    if args.phase == "locked" and run.get("model_selection_status") != "frozen":
        raise ValueError("locked review pack cannot open before model selection is frozen")
    rows = _review_rows(
        repo, manifest, max_per_skin=args.max_per_skin, phase=args.phase
    )
    skins = len({row["source_key"] for row in rows})
    expected_skins = 20 if args.phase == "development" else 10
    if args.apply:
        write_review_pack(args.output, rows)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "rows": len(rows),
                "skins_with_rows": skins,
                "phase": args.phase,
                "expected_review_skins": expected_skins,
                "output": str(args.output),
                "output_sha256": sha256_file(args.output) if args.apply else None,
                "applied": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if skins == expected_skins else 2


def _phase_by_source(manifest: dict[str, Any]) -> dict[str, str]:
    review = review_skin_split(manifest["records"])
    return {str(row["source_key"]): review.get(str(row["source_key"]), "production") for row in manifest["records"]}


def command_annotate_deterministic(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    validate_cohort_manifest(manifest)
    run_id = _manifest_run_id(manifest, args.run_id)
    repo = EmotionEvidenceRepository(args.db)
    _run_contract(repo, run_id)
    candidates = repo.list_annotation_candidates(run_id)
    phases = _phase_by_source(manifest)
    extractor_digest = sha256_file(ROOT / "models/emotion_workflow.py")
    prompt_hash = _extractor_prompt_hash("deterministic-v1")
    annotated = Counter()
    stored = 0
    for row in candidates:
        result = deterministic_annotation(
            source_key=str(row["source_key"]),
            hero_name=str(row["hero_name"]),
            skin_name=str(row["skin_name"]),
            text=str(row["text"]),
            mapping_scope=str(row["mapping_scope"]),
        )
        annotation = {
            **result,
            "run_id": run_id,
            "evidence_id": int(row["evidence_id"]),
            "annotator_id": "deterministic-v1",
            "annotator_kind": "model",
            "phase": phases[str(row["source_key"])],
            "extractor_digest": extractor_digest,
            "prompt_hash": prompt_hash,
        }
        validate_annotation(annotation)
        annotation["annotation_hash"] = annotation_hash(annotation)
        annotated[result["relevance"]] += 1
        if args.apply:
            repo.add_annotation(annotation)
            stored += 1
    print(
        json.dumps(
            {
                "run_id": run_id,
                "annotations": len(candidates),
                "stored": stored,
                "extractor_digest": extractor_digest,
                "prompt_hash": prompt_hash,
                "relevance": dict(annotated),
                "applied": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _ollama_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
        try:
            decoded = json.loads(body)
        except json.JSONDecodeError as exc:
            preview = body[:200].replace("\n", "\\n")
            raise ValueError(
                "Ollama returned a non-JSON HTTP response "
                f"(status={response.status}, bytes={len(body)}, preview={preview!r})"
            ) from exc
    if not isinstance(decoded, dict):
        raise ValueError(
            f"Ollama returned JSON type {type(decoded).__name__}; expected an object"
        )
    return decoded


def _normalize_model_annotation(
    result: dict[str, Any],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Reconcile redundant aspect fields without inventing polarity.

    ``aspects`` is the model's explicit membership decision. Some Ollama/Qwen
    structured responses nevertheless materialize every optional polarity
    property. Zero-valued unlisted properties are treated as decoder defaults;
    nonzero unlisted properties are explicit aspect-polarity decisions and are
    added to the aspect set. Missing labelled polarities and malformed extras
    remain invalid. Every applied reconciliation is returned for provenance.
    """
    aspects = result.get("aspects")
    polarities = result.get("polarities")
    if not isinstance(aspects, list) or not isinstance(polarities, dict):
        return result, ()
    labelled = set(aspects)
    missing = labelled - set(polarities)
    extras = set(polarities) - labelled
    if missing:
        return result, ()
    if not extras:
        return result, ()
    if any(
        aspect not in SUBJECTIVE_ASPECTS
        or not isinstance(polarities[aspect], (int, float))
        or isinstance(polarities[aspect], bool)
        or int(polarities[aspect]) != polarities[aspect]
        or not -2 <= int(polarities[aspect]) <= 2
        for aspect in extras
    ):
        return result, ()

    added = [
        aspect
        for aspect in SUBJECTIVE_ASPECTS
        if aspect in extras and int(polarities[aspect]) != 0
    ]
    dropped = [
        aspect
        for aspect in SUBJECTIVE_ASPECTS
        if aspect in extras and int(polarities[aspect]) == 0
    ]
    normalized_aspects = list(aspects) + added
    normalized = dict(result)
    normalized["aspects"] = normalized_aspects
    normalized["polarities"] = {
        aspect: int(polarities[aspect]) for aspect in normalized_aspects
    }
    notes = []
    if added:
        notes.append("added_nonzero_polarity_aspects=" + ",".join(added))
    if dropped:
        notes.append("dropped_zero_default_polarities=" + ",".join(dropped))
    return normalized, tuple(notes)


def _model_annotation(row: dict[str, Any], *, model: str, host: str, timeout: float) -> dict[str, Any]:
    user_prompt = canonical_json(
        {
            "target": {
                "source_key": row["source_key"],
                "hero_name": row["hero_name"],
                "skin_name": row["skin_name"],
            },
            "parent_title": row["parent_title"],
            "comment": row["text"],
        }
    )
    response = _ollama_json(
        f"{host.rstrip('/')}/api/chat",
        {
            "model": model,
            "stream": False,
            # Qwen's reasoning channel can consume the context window before it
            # emits the small structured label. This task needs only the label.
            "think": MODEL_THINK,
            "format": MODEL_RESPONSE_SCHEMA,
            "options": dict(MODEL_GENERATION_OPTIONS),
            "messages": [
                {"role": "system", "content": MODEL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        },
        timeout,
    )
    if response.get("error"):
        raise ValueError(
            f"Ollama {model} failed for evidence {row['evidence_id']}: "
            f"{response['error']}"
        )
    message = response.get("message") or {}
    content = str(message.get("content") or "").strip()
    if not content:
        raise ValueError(
            f"Ollama {model} returned empty structured content for evidence "
            f"{row['evidence_id']} (done_reason={response.get('done_reason')!r}, "
            f"eval_count={response.get('eval_count')!r}, "
            f"thinking_chars={len(str(message.get('thinking') or ''))})"
        )
    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Ollama {model} returned invalid structured content for evidence "
            f"{row['evidence_id']} (done_reason={response.get('done_reason')!r}, "
            f"content_preview={content[:200]!r})"
        ) from exc
    if not isinstance(result, dict):
        raise ValueError(
            f"Ollama {model} returned {type(result).__name__} content for evidence "
            f"{row['evidence_id']}; expected an object"
        )
    result, decoder_notes = _normalize_model_annotation(result)
    result["source_key"] = str(row["source_key"])
    result["notes"] = f"local Ollama {model}; prompt={model_prompt_hash()}"
    if decoder_notes:
        result["notes"] += "; decoder=" + "|".join(decoder_notes)
    try:
        validate_annotation(result)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Ollama {model} returned a contract-invalid annotation for evidence "
            f"{row['evidence_id']}: {exc}"
        ) from exc
    return result


def command_annotate_local(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    validate_cohort_manifest(manifest)
    run_id = _manifest_run_id(manifest, args.run_id)
    repo = EmotionEvidenceRepository(args.db)
    _run_contract(repo, run_id)
    prompt_hash = model_prompt_hash()
    annotator_id = _extractor_annotator_id(args.model, prompt_hash=prompt_hash)
    existing = {
        int(row["evidence_id"])
        for row in repo.list_annotations(run_id, annotator_id=annotator_id)
    }
    pending_candidates = [
        row
        for row in repo.list_annotation_candidates(run_id)
        if int(row["evidence_id"]) not in existing
        and not row.get("quarantine_reason")
        and row.get("mapping_scope") == "exact_skin"
        and not row.get("is_synthetic")
    ]
    candidates = pending_candidates[: args.limit or None]
    if not args.apply:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "model": args.model,
                    "annotator_id": annotator_id,
                    "prompt_hash": prompt_hash,
                    "candidates": len(candidates),
                    "pending_before_run": len(pending_candidates),
                    "network_requests_made": 0,
                    "stored": 0,
                    "applied": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    extractor_digest = _ollama_model_digest(args.host, args.model, args.timeout)
    phases = _phase_by_source(manifest)
    stored = 0
    for row in candidates:
        result = _model_annotation(
            row, model=args.model, host=args.host, timeout=args.timeout
        )
        annotation = {
            **result,
            "run_id": run_id,
            "evidence_id": int(row["evidence_id"]),
            "annotator_id": annotator_id,
            "annotator_kind": "model",
            "phase": phases[str(row["source_key"])],
            "extractor_digest": extractor_digest,
            "prompt_hash": prompt_hash,
        }
        annotation["annotation_hash"] = annotation_hash(annotation)
        repo.add_annotation(annotation)
        stored += 1
    print(
        json.dumps(
            {
                "run_id": run_id,
                "model": args.model,
                "annotator_id": annotator_id,
                "prompt_hash": prompt_hash,
                "extractor_digest": extractor_digest,
                "processed": len(candidates),
                "stored": stored,
                "remaining_after_run": max(0, len(pending_candidates) - stored),
                "applied": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_import_review(args: argparse.Namespace) -> int:
    rows = parse_review_pack(args.input)
    if not rows:
        raise ValueError("review import is empty")
    phases = {str(row["phase"]) for row in rows}
    if len(phases) != 1:
        raise ValueError("one review import must contain exactly one phase")
    import_phase = next(iter(phases))
    if import_phase not in {"development", "locked", "production_audit"}:
        raise ValueError(f"unsupported review phase: {import_phase}")
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, args.run_id)
    if import_phase == "locked" and run.get(
        "model_selection_status"
    ) != "frozen":
        raise ValueError("locked labels cannot be imported before model selection is frozen")
    candidates = {
        int(row["evidence_id"]): row
        for row in repo.list_annotation_candidates(args.run_id)
    }
    human_reviewers: dict[tuple[int, str], set[str]] = defaultdict(set)
    if args.kind == "adjudicated":
        for annotation in repo.list_annotations(args.run_id, annotator_kind="human"):
            human_reviewers[
                (int(annotation["evidence_id"]), str(annotation["phase"]))
            ].add(str(annotation["annotator_id"]))
    stored = 0
    for row in rows:
        evidence = candidates.get(int(row["evidence_id"]))
        if evidence is None or evidence["source_key"] != row["source_key"]:
            raise ValueError(f"review row is not bound to run evidence: {row['evidence_id']}")
        if args.kind == "adjudicated" and len(
            human_reviewers[(int(row["evidence_id"]), str(row["phase"]))]
        ) < 2:
            raise ValueError(
                f"adjudication requires two preserved human labels: {row['evidence_id']}"
            )
    if args.apply:
        repo.bind_input_hash(
            args.run_id,
            f"review:{import_phase}:{args.kind}:{args.reviewer_id}",
            sha256_file(args.input),
        )
    for row in rows:
        annotation = {
            **row,
            "run_id": args.run_id,
            "annotator_id": args.reviewer_id,
            "annotator_kind": args.kind,
        }
        annotation["annotation_hash"] = annotation_hash(annotation)
        if args.apply:
            repo.add_annotation(annotation)
            stored += 1
    print(
        json.dumps(
            {
                "run_id": args.run_id,
                "reviewer_id": args.reviewer_id,
                "kind": args.kind,
                "rows": len(rows),
                "stored": stored,
                "input_sha256": sha256_file(args.input),
                "applied": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def command_select_model(args: argparse.Namespace) -> int:
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, args.run_id)
    truth = repo.list_annotations(
        args.run_id, phase="development", annotator_kind="adjudicated"
    )
    results: dict[str, Any] = {}
    digests: dict[str, str] = {}
    for candidate_id in EXTRACTOR_IDS:
        annotator_id = _extractor_annotator_id(candidate_id)
        predictions = repo.list_annotations(
            args.run_id, phase="development", annotator_id=annotator_id
        )
        metrics = calibration_metrics(truth, predictions)
        metrics["minimum_n"] = MIN_DEVELOPMENT_JUDGMENTS
        metrics["sample_complete"] = metrics["n"] >= MIN_DEVELOPMENT_JUDGMENTS
        metrics["passed"] = bool(metrics["passed"] and metrics["sample_complete"])
        results[candidate_id] = metrics
        candidate_digests = {
            str(row.get("extractor_digest") or "") for row in predictions
        } - {""}
        candidate_prompts = {
            str(row.get("prompt_hash") or "") for row in predictions
        } - {""}
        if len(candidate_digests) == 1:
            digests[candidate_id] = next(iter(candidate_digests))
        if candidate_prompts and candidate_prompts != {
            _extractor_prompt_hash(candidate_id)
        }:
            metrics["passed"] = False
            metrics["prompt_mismatch"] = True
        if len(candidate_digests) != 1:
            metrics["passed"] = False
            metrics["digest_mismatch"] = True

    complete = [
        name
        for name in EXTRACTOR_IDS
        if results[name]["sample_complete"]
        and name in digests
        and not results[name].get("prompt_mismatch")
        and not results[name].get("digest_mismatch")
    ]
    if not complete:
        selected = None
    else:
        selected = max(
            complete,
            key=lambda name: (
                bool(results[name]["passed"]),
                results[name]["aspect_macro_f1"],
                results[name]["relevance_precision"],
                results[name]["relevance_recall"],
                results[name]["polarity_weighted_kappa"],
                -(results[name]["polarity_mae"] or 99.0),
                -EXTRACTOR_IDS.index(name),
            ),
        )
    summary = {
        "run_id": args.run_id,
        "development_truth_n": len(truth),
        "candidates": results,
        "selection_rule": "pass-first, aspect-F1, relevance, polarity, fixed candidate order",
        "selected": selected,
        "applied": bool(args.apply),
    }
    if selected and args.apply:
        repo.freeze_model_selection(
            args.run_id,
            model_name=selected,
            model_digest=digests[selected],
            prompt_hash=_extractor_prompt_hash(selected),
            metrics=summary,
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if selected else 2


def command_audit_pack(args: argparse.Namespace) -> int:
    manifest = _load_json(args.manifest)
    validate_cohort_manifest(manifest)
    run_id = _manifest_run_id(manifest, args.run_id)
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, run_id)
    if run.get("model_selection_status") != "frozen" or not run.get("model_name"):
        raise ValueError("production audit pack requires a frozen model selection")
    evidence = {
        int(row["evidence_id"]): row
        for row in repo.list_annotation_candidates(run_id)
    }
    predictions = repo.list_annotations(
        run_id,
        phase="production",
        annotator_id=_extractor_annotator_id(
            str(run["model_name"]), prompt_hash=str(run["prompt_hash"])
        ),
    )
    retained = []
    for prediction in predictions:
        row = evidence.get(int(prediction["evidence_id"]))
        if (
            row
            and prediction["relevance"] == "relevant"
            and row["mapping_scope"] == "exact_skin"
            and not row["is_synthetic"]
            and not row["quarantine_reason"]
        ):
            retained.append(row)
    retained.sort(
        key=lambda row: sha256_json(
            {"seed": args.seed, "content_hash": row["content_hash"]}
        )
    )
    sample_size = math.ceil(len(retained) * 0.10) if retained else 0
    rows = []
    for row in retained[:sample_size]:
        rows.append(
            {
                "review_id": str(row["content_hash"])[:16],
                "evidence_id": row["evidence_id"],
                "phase": "production_audit",
                "source_key": row["source_key"],
                "hero_name": row["hero_name"],
                "skin_name": row["skin_name"],
                "platform": row["platform"],
                "parent_external_id": row["parent_external_id"],
                "parent_title": row["parent_title"],
                "published_at": row["published_at"],
                "text": row["text"],
                "relevance": "",
                "aspects": "",
                "polarities": "",
                "actual_use": "",
                "confidence": "",
                "notes": "",
            }
        )
    if args.apply:
        write_review_pack(args.output, rows)
    print(
        json.dumps(
            {
                "run_id": run_id,
                "model": run["model_name"],
                "retained_production_annotations": len(retained),
                "audit_rows": len(rows),
                "fraction": 0.10,
                "seed": args.seed,
                "output": str(args.output),
                "output_sha256": sha256_file(args.output) if args.apply else None,
                "applied": bool(args.apply),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if rows else 2


def command_gate(args: argparse.Namespace) -> int:
    if args.gate == "calibration" and args.phase != "locked":
        raise ValueError("calibration gate must use the locked phase")
    if args.gate == "audit" and args.phase != "production_audit":
        raise ValueError("audit gate must use the production_audit phase")
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, args.run_id)
    if run.get("model_selection_status") != "frozen":
        raise ValueError("extractor must be frozen on development data before gating")
    if args.prediction_id != run.get("model_name"):
        raise ValueError("locked/audit prediction must use the frozen extractor")
    gate_status = run.get(f"{args.gate}_status")
    if args.apply and gate_status != "pending":
        raise ValueError(f"{args.gate} gate has already been evaluated")
    truth = repo.list_annotations(
        args.run_id, phase=args.phase, annotator_kind="adjudicated"
    )
    prediction_phase = "production" if args.gate == "audit" else args.phase
    predictions = repo.list_annotations(
        args.run_id,
        phase=prediction_phase,
        annotator_id=_extractor_annotator_id(
            args.prediction_id, prompt_hash=str(run["prompt_hash"])
        ),
    )
    if any(
        row.get("extractor_digest") != run.get("model_digest")
        or row.get("prompt_hash") != run.get("prompt_hash")
        for row in predictions
    ):
        raise ValueError("prediction annotations do not match the frozen extractor identity")
    metrics = calibration_metrics(truth, predictions)
    minimum_n = MIN_LOCKED_JUDGMENTS if args.gate == "calibration" else len(truth)
    metrics["minimum_n"] = minimum_n
    metrics["sample_complete"] = bool(minimum_n and metrics["n"] >= minimum_n)
    if args.gate == "audit":
        truth_by_id = {int(row["evidence_id"]): row for row in truth}
        prediction_by_id = {int(row["evidence_id"]): row for row in predictions}
        metrics["accepted_target_mapping_errors"] = sum(
            prediction_by_id[key]["relevance"] == "relevant"
            and truth_by_id[key]["relevance"] != "relevant"
            for key in set(truth_by_id) & set(prediction_by_id)
        )
    else:
        metrics["accepted_target_mapping_errors"] = 0
    metrics["passed"] = bool(
        metrics["passed"]
        and metrics["sample_complete"]
        and metrics["accepted_target_mapping_errors"] == 0
    )
    if args.apply:
        repo.set_review_gate(
            args.run_id,
            gate=args.gate,
            passed=bool(metrics["passed"]),
            metrics=metrics,
        )
    print(json.dumps({**metrics, "gate": args.gate, "applied": args.apply}, indent=2))
    return 0 if metrics["passed"] else 2


def command_validate(args: argparse.Namespace) -> int:
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, args.run_id)
    members = repo.list_cohort_members(args.run_id)
    candidates = {
        int(row["evidence_id"]): row
        for row in repo.list_annotation_candidates(args.run_id)
    }
    provisional_extractor = not bool(run.get("model_name"))
    model_id = str(run.get("model_name") or "deterministic-v1")
    model_rows = repo.list_annotations(
        args.run_id,
        annotator_id=_extractor_annotator_id(
            model_id,
            prompt_hash=(str(run["prompt_hash"]) if run.get("prompt_hash") else None),
        ),
    )
    adjudicated = repo.list_annotations(args.run_id, annotator_kind="adjudicated")
    selected = {int(row["evidence_id"]): row for row in model_rows}
    selected.update({int(row["evidence_id"]): row for row in adjudicated})
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence_id, annotation in selected.items():
        evidence = candidates.get(evidence_id)
        if evidence is not None:
            by_source[str(evidence["source_key"])].append({**evidence, **annotation})
    protocol_snapshot = _protocol_snapshot_path(args.run_id)
    if args.protocol is not None:
        protocol_path = args.protocol
    elif protocol_snapshot.exists():
        protocol_path = protocol_snapshot
    else:
        protocol_path = DEFAULT_PROTOCOL
    protocol_hash = sha256_file(protocol_path)
    if protocol_hash != run.get("protocol_hash"):
        raise ValueError(
            "protocol file hash differs from the run's frozen protocol "
            f"(path={protocol_path}, expected={run.get('protocol_hash')}, "
            f"actual={protocol_hash})"
        )
    if args.apply and not protocol_snapshot.exists():
        _write_text_atomic(
            protocol_snapshot, protocol_path.read_text(encoding="utf-8")
        )
    extractor_mismatch = not provisional_extractor and any(
        row.get("extractor_digest") != run.get("model_digest")
        or row.get("prompt_hash") != run.get("prompt_hash")
        for row in model_rows
    )
    profiles = []
    calibration_passed = (
        run.get("calibration_status") == "passed" and not extractor_mismatch
    )
    audit_passed = run.get("audit_status") == "passed"
    for member in members:
        source_key = str(member["source_key"])
        rows = by_source.get(source_key, [])
        forbidden = sorted(
            {
                str(row.get("input_class") or "")
                for row in rows
                if str(row.get("input_class") or "") != "public_comment"
            }
        )
        profile = aggregate_adjudicated_annotations(
            rows,
            run_id=args.run_id,
            source_key=source_key,
            protocol_hash=protocol_hash,
            calibration_passed=calibration_passed,
            audit_passed=audit_passed,
            forbidden_inputs=forbidden,
            published=False,
        )
        profiles.append(profile)
        if args.apply:
            repo.save_validation_result(profile)
    eligible = [profile for profile in profiles if profile.is_eligible_for_publication]
    reason_counts = Counter(
        reason for profile in profiles for reason in profile.validation_reasons
    )
    profile_payloads = sorted(
        (_profile_payload(profile) for profile in profiles),
        key=lambda row: row["source_key"],
    )
    recollection_priority = sorted(
        (
            {
                "source_key": profile.source_key,
                "relevant_comment_deficit": max(
                    0, 20 - profile.relevant_comment_count
                ),
                "author_deficit": max(0, 15 - profile.unique_author_count),
                "parent_deficit": max(0, 2 - profile.parent_document_count),
                "aspect_author_deficits": {
                    aspect: max(0, 5 - profile.aspect_author_counts.get(aspect, 0))
                    for aspect in profile.aspect_author_counts
                },
                "reasons": profile.validation_reasons,
            }
            for profile in profiles
            if not profile.is_eligible_for_publication
        ),
        key=lambda row: (
            -sum(row["aspect_author_deficits"].values()),
            -row["relevant_comment_deficit"],
            row["source_key"],
        ),
    )
    report = {
        "schema_version": 1,
        "run_id": args.run_id,
        "protocol_hash": protocol_hash,
        "protocol_path": str(protocol_path),
        "cohort_size": len(profiles),
        "eligible_count": len(eligible),
        "required_validated": int(run["required_validated"]),
        "release_ready": len(eligible) >= int(run["required_validated"]),
        "calibration_status": run["calibration_status"],
        "audit_status": run["audit_status"],
        "extractor_id": model_id,
        "extractor_provisional": provisional_extractor,
        "extractor_identity_matches": not extractor_mismatch,
        "reason_counts": dict(sorted(reason_counts.items())),
        "profiles_sha256": sha256_json(profile_payloads),
        "profiles": profile_payloads,
        "recollection_priority": recollection_priority,
        "applied": bool(args.apply),
    }
    if args.report and args.apply:
        _write_json_atomic(args.report, report)
        repo.bind_validation_artifact(args.run_id, sha256_file(args.report))
    print(
        json.dumps(
            {
                key: value
                for key, value in report.items()
                if key not in {"profiles", "recollection_priority"}
            },
            indent=2,
        )
    )
    return 0 if report["release_ready"] else 2


def command_publish(args: argparse.Namespace) -> int:
    repo = EmotionEvidenceRepository(args.db)
    run = _run_contract(repo, args.run_id)
    status = repo.latest_cohort_status() or {}
    preflight = {
        "run_id": args.run_id,
        "calibration_status": run["calibration_status"],
        "audit_status": run["audit_status"],
        "release_status": run["release_status"],
        "currently_published": status.get("validated_count", 0),
        "backup": str(args.backup),
        "validation_report": str(args.validation_report),
        "applied": bool(args.apply),
    }
    if not args.apply:
        print(json.dumps(preflight, indent=2))
        return 0
    if args.backup.exists():
        raise ValueError(f"refusing to overwrite existing backup: {args.backup}")
    if not args.validation_report.is_file():
        raise ValueError("validation report is missing")
    report_hash = sha256_file(args.validation_report)
    if report_hash != run.get("artifact_manifest_sha256"):
        raise ValueError("validation report hash differs from the run contract")
    validation_report = _load_json(args.validation_report)
    persisted_profiles = [
        _profile_payload(profile) for profile in repo.list_run_profiles(args.run_id)
    ]
    if validation_report.get("profiles_sha256") != sha256_json(persisted_profiles):
        raise ValueError("validation artifact and database result set disagree")
    with sqlite3.connect(args.db) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise ValueError(f"database integrity check failed: {integrity}")
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    source_hash = sha256_file(args.db)
    shutil.copy2(args.db, args.backup)
    if sha256_file(args.backup) != source_hash:
        raise ValueError("database backup hash mismatch")
    result = repo.publish_run(args.run_id)
    print(json.dumps({**preflight, **result, "backup_sha256": source_hash}, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    repo = EmotionEvidenceRepository(args.db)
    run = repo.get_run(args.run_id)
    if run is None:
        print(json.dumps({"run_id": args.run_id, "status": "missing"}, indent=2))
        return 2
    members = repo.list_cohort_members(args.run_id)
    candidates = repo.list_annotation_candidates(args.run_id)
    annotations = repo.list_annotations(args.run_id)
    compact_run = {
        key: value
        for key, value in run.items()
        if key not in {"manifest", "model_selection_metrics", "calibration_metrics", "audit_metrics"}
    }
    compact_run["input_hashes"] = run.get("input_hashes") or {}
    print(
        json.dumps(
            {
                "run": compact_run,
                "cohort_members": len(members),
                "evidence_items": len(candidates),
                "annotations": len(annotations),
                "public_status": repo.latest_cohort_status(),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    manifest = sub.add_parser("manifest", help="Build and optionally persist the fixed cohort")
    manifest.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    manifest.add_argument("--warm-manifest", type=Path, default=DEFAULT_WARM_MANIFEST)
    manifest.add_argument("--output", type=Path, default=DEFAULT_MANIFEST)
    manifest.add_argument("--run-id", default=DEFAULT_RUN_ID)
    manifest.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    manifest.add_argument("--seed", type=int, default=20260902)
    manifest.add_argument("--apply", action="store_true")
    manifest.set_defaults(func=command_manifest)

    stage = sub.add_parser("stage-warm", help="Sanitize and stage the warm-start comments")
    stage.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    stage.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    stage.add_argument("--input", type=Path, default=DEFAULT_WARM_COMMENTS)
    stage.add_argument("--run-id")
    stage.add_argument("--apply", action="store_true")
    stage.set_defaults(func=command_stage_warm)

    ethics = sub.add_parser(
        "record-ethics", help="Bind the privacy/ethics determination before collection"
    )
    ethics.add_argument("record", type=Path)
    ethics.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    ethics.add_argument("--run-id", default=DEFAULT_RUN_ID)
    ethics.add_argument(
        "--status", choices=("ready", "exempt", "not_applicable"), required=True
    )
    ethics.add_argument("--apply", action="store_true")
    ethics.set_defaults(func=command_record_ethics)

    review = sub.add_parser("review-pack", help="Create a blinded human review CSV")
    review.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    review.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    review.add_argument("--run-id")
    review.add_argument(
        "--phase", choices=("development", "locked"), default="development"
    )
    review.add_argument("--max-per-skin", type=int, default=20)
    review.add_argument("--output", type=Path, required=True)
    review.add_argument("--apply", action="store_true")
    review.set_defaults(func=command_review_pack)

    deterministic = sub.add_parser("annotate-deterministic", help="Run the local rule baseline")
    deterministic.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    deterministic.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    deterministic.add_argument("--run-id")
    deterministic.add_argument("--apply", action="store_true")
    deterministic.set_defaults(func=command_annotate_deterministic)

    local = sub.add_parser("annotate-local", help="Run a local Ollama evidence coder")
    local.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    local.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    local.add_argument("--run-id")
    local.add_argument("--model", choices=("qwen3.5:4b", "qwen3.5:27b"), required=True)
    local.add_argument("--host", default="http://127.0.0.1:11434")
    local.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Ollama HTTP timeout in seconds (default: 120; do not pass milliseconds)",
    )
    local.add_argument("--limit", type=int, default=0)
    local.add_argument("--apply", action="store_true")
    local.set_defaults(func=command_annotate_local)

    import_review = sub.add_parser("import-review", help="Import immutable human/adjudicated labels")
    import_review.add_argument("input", type=Path)
    import_review.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    import_review.add_argument("--run-id", default=DEFAULT_RUN_ID)
    import_review.add_argument("--reviewer-id", required=True)
    import_review.add_argument("--kind", choices=("human", "adjudicated"), required=True)
    import_review.add_argument("--apply", action="store_true")
    import_review.set_defaults(func=command_import_review)

    select = sub.add_parser(
        "select-model", help="Compare all extractors on development data and freeze one"
    )
    select.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    select.add_argument("--run-id", default=DEFAULT_RUN_ID)
    select.add_argument("--apply", action="store_true")
    select.set_defaults(func=command_select_model)

    audit_pack = sub.add_parser(
        "audit-pack", help="Create the blinded 10 percent production audit pack"
    )
    audit_pack.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    audit_pack.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    audit_pack.add_argument("--run-id")
    audit_pack.add_argument("--seed", type=int, default=20260902)
    audit_pack.add_argument("--output", type=Path, required=True)
    audit_pack.add_argument("--apply", action="store_true")
    audit_pack.set_defaults(func=command_audit_pack)

    gate = sub.add_parser("gate", help="Evaluate locked calibration or production audit")
    gate.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    gate.add_argument("--run-id", default=DEFAULT_RUN_ID)
    gate.add_argument("--gate", choices=("calibration", "audit"), required=True)
    gate.add_argument("--phase", choices=("locked", "production_audit"), required=True)
    gate.add_argument("--prediction-id", required=True)
    gate.add_argument("--apply", action="store_true")
    gate.set_defaults(func=command_gate)

    validate = sub.add_parser("validate", help="Aggregate profiles and test the 80/100 gate")
    validate.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    validate.add_argument("--run-id", default=DEFAULT_RUN_ID)
    validate.add_argument(
        "--protocol",
        type=Path,
        help="Explicit frozen protocol path; defaults to the run snapshot when present",
    )
    validate.add_argument("--report", type=Path)
    validate.add_argument("--apply", action="store_true")
    validate.set_defaults(func=command_validate)

    publish = sub.add_parser("publish", help="Publish only after every release gate passes")
    publish.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    publish.add_argument("--run-id", default=DEFAULT_RUN_ID)
    publish.add_argument("--backup", type=Path, required=True)
    publish.add_argument(
        "--validation-report",
        type=Path,
        default=Path("data/emotion_evidence/runs")
        / DEFAULT_RUN_ID
        / "validation-report.json",
    )
    publish.add_argument("--apply", action="store_true")
    publish.set_defaults(func=command_publish)

    status = sub.add_parser("status", help="Inspect one run without mutation")
    status.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    status.add_argument("--run-id", default=DEFAULT_RUN_ID)
    status.set_defaults(func=command_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return int(args.func(args))
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
