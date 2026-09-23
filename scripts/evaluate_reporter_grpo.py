#!/usr/bin/env python3
"""Run a deterministic, paired reporter-checkpoint evaluation.

This evaluator is deliberately diagnostic for the current GRPO run: the 960
rows in ``data/reporter_grpo.jsonl`` were used during training.  It compares
the SFT initializer and a GRPO checkpoint under identical prompts and decoding,
then applies the existing mechanical reporter verifier.  It does not claim
held-out or semantic quality.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path
from statistics import fmean

import torch
from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[1]
MINIMIND = ROOT / "minimind"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(MINIMIND) not in sys.path:
    sys.path.insert(0, str(MINIMIND))

from model.model_minimind import MiniMindConfig, MiniMindForCausalLM  # noqa: E402
from scripts.train_reporter_grpo import verify_report  # noqa: E402


METRIC_FIELDS = (
    "section",
    "heading_progress",
    "grounding",
    "evidence_caveat",
    "numeric",
    "business_anchor",
    "task_total",
    "task_maximum",
    "verifier_normalized",
)


def _jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"expected object records in {path}")
            rows.append(value)
    if not rows:
        raise ValueError(f"no records found in {path}")
    return rows


def _prepare_batch(tokenizer, records: list[dict], max_input_tokens: int):
    prompts = []
    states = []
    for index, record in enumerate(records):
        conversations = record.get("conversations")
        if not isinstance(conversations, list) or len(conversations) < 3:
            raise ValueError(f"record {index} has invalid conversations")
        prompts.append(
            tokenizer.apply_chat_template(
                conversations[:-1],
                tokenize=False,
                open_thinking=False,
                add_generation_prompt=True,
            )
        )
        states.append(json.loads(conversations[1]["content"]))
    raw_ids = tokenizer(prompts, add_special_tokens=False)["input_ids"]
    tokenizer.padding_side = "left"
    encoded = tokenizer(
        prompts,
        return_tensors="pt",
        truncation=True,
        max_length=max_input_tokens,
        padding=True,
        add_special_tokens=False,
    )
    infos = [
        {
            "input_tokens_raw": len(ids),
            "input_tokens": int(encoded["attention_mask"][row].sum().item()),
            "truncated": len(ids) > max_input_tokens,
        }
        for row, ids in enumerate(raw_ids)
    ]
    return states, infos, encoded


def _load_model(checkpoint: Path, tokenizer_dir: Path, device: str) -> tuple[MiniMindForCausalLM, AutoTokenizer]:
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_dir)
    config = MiniMindConfig(hidden_size=768, num_hidden_layers=8, use_moe=False)
    model = MiniMindForCausalLM(config)
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    if device.startswith("cuda"):
        model = model.half().to(device)
    else:
        model = model.float().to(device)
    return model, tokenizer


def _evaluate_model(
    *,
    model_name: str,
    checkpoint: Path,
    rows: list[dict],
    output_path: Path,
    tokenizer_dir: Path,
    device: str,
    max_input_tokens: int,
    max_new_tokens: int,
    batch_size: int,
    limit: int | None,
) -> dict:
    model, tokenizer = _load_model(checkpoint, tokenizer_dir, device)
    selected = rows[:limit] if limit else rows
    output_path.parent.mkdir(parents=True, exist_ok=True)
    metrics: dict[str, list[float]] = {name: [] for name in METRIC_FIELDS}
    lengths: list[int] = []
    input_lengths: list[int] = []
    truncated = 0
    errors = 0
    start = time.time()

    tokenizer.padding_side = "left"
    with output_path.open("w", encoding="utf-8") as output:
        for batch_start in range(0, len(selected), batch_size):
            batch = selected[batch_start:batch_start + batch_size]
            states, infos, encoded = _prepare_batch(tokenizer, batch, max_input_tokens)
            input_lengths.extend(info["input_tokens"] for info in infos)
            truncated += sum(int(info["truncated"]) for info in infos)
            encoded = {key: value.to(device) for key, value in encoded.items()}
            batch_error = None
            try:
                with torch.inference_mode():
                    generated = model.generate(
                        inputs=encoded["input_ids"],
                        attention_mask=encoded["attention_mask"],
                        max_new_tokens=max_new_tokens,
                        temperature=1.0,
                        top_p=1.0,
                        top_k=0,
                        do_sample=False,
                        repetition_penalty=1.0,
                        pad_token_id=tokenizer.pad_token_id,
                        eos_token_id=tokenizer.eos_token_id,
                    )
            except Exception as exc:  # retain every row identity on a batch failure
                batch_error = f"{type(exc).__name__}: {exc}"

            prompt_width = int(encoded["input_ids"].shape[1])
            for offset, (record, state, info) in enumerate(zip(batch, states, infos)):
                index = batch_start + offset
                try:
                    if batch_error:
                        raise RuntimeError(batch_error)
                    response_ids = generated[offset, prompt_width:]
                    eos = (response_ids == tokenizer.eos_token_id).nonzero(as_tuple=False)
                    if eos.numel():
                        response_ids = response_ids[: int(eos[0, 0]) + 1]
                    response = tokenizer.decode(response_ids.tolist(), skip_special_tokens=True)
                    parts = verify_report(state, response)
                    parts["verifier_normalized"] = (
                        parts["task_total"] / parts["task_maximum"] * 2.0 - 1.0
                    )
                    row = {
                        "index": index,
                        "model": model_name,
                        "skin_id": state.get("subject", {}).get("skin_id", ""),
                        "hero_name": state.get("subject", {}).get("hero_name", ""),
                        "skin_name": state.get("subject", {}).get("skin_name", ""),
                        "response": response,
                        "response_tokens": int(response_ids.numel()),
                        **info,
                        **parts,
                        "error": None,
                    }
                    lengths.append(int(response_ids.numel()))
                    for name in METRIC_FIELDS:
                        value = parts[name]
                        if value is not None:
                            metrics[name].append(float(value))
                except Exception as exc:  # retain row identity and continue the audit
                    errors += 1
                    row = {
                        "index": index,
                        "model": model_name,
                        "skin_id": state.get("subject", {}).get("skin_id", ""),
                        "hero_name": state.get("subject", {}).get("hero_name", ""),
                        "skin_name": state.get("subject", {}).get("skin_name", ""),
                        **info,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            completed = min(batch_start + batch_size, len(selected))
            if completed % 10 == 0 or completed == len(selected):
                elapsed = max(time.time() - start, 1e-6)
                print(
                    f"[{model_name}] {completed}/{len(selected)} "
                    f"errors={errors} elapsed={elapsed:.1f}s",
                    flush=True,
                )

    count = len(selected)
    summary = {
        "model": model_name,
        "checkpoint": str(checkpoint),
        "rows": count,
        "batch_size": batch_size,
        "successful_rows": count - errors,
        "errors": errors,
        "truncated_rows": truncated,
        "mean_input_tokens": fmean(input_lengths) if input_lengths else None,
        "mean_response_tokens": fmean(lengths) if lengths else None,
        "p95_response_tokens": sorted(lengths)[max(0, math.ceil(len(lengths) * 0.95) - 1)] if lengths else None,
        "cap_rate": sum(length >= max_new_tokens for length in lengths) / len(lengths) if lengths else None,
        "metrics": {name: fmean(values) if values else None for name, values in metrics.items()},
        "caveat_eligible": sum(1 for row in _jsonl(output_path) if row.get("evidence_caveat") is not None),
        "caveat_positive": sum(1 for row in _jsonl(output_path) if (row.get("evidence_caveat") or 0) > 0),
        "elapsed_seconds": time.time() - start,
    }
    del model
    if device.startswith("cuda"):
        torch.cuda.empty_cache()
    return summary


def _paired_summary(paths: dict[str, Path]) -> dict:
    names = list(paths)
    if len(names) != 2:
        return {"models": names, "paired": False, "reason": "exactly two models are required"}
    left_name, right_name = names
    left_rows, right_rows = _jsonl(paths[left_name]), _jsonl(paths[right_name])
    if len(left_rows) != len(right_rows):
        return {"models": names, "paired": False, "reason": "row counts differ"}
    deltas: dict[str, list[float]] = {name: [] for name in METRIC_FIELDS + ("response_tokens",)}
    mismatched = 0
    for left, right in zip(left_rows, right_rows):
        if left.get("index") != right.get("index"):
            mismatched += 1
            continue
        for name in deltas:
            if left.get(name) is not None and right.get(name) is not None:
                deltas[name].append(float(right[name]) - float(left[name]))
    return {
        "models": names,
        "baseline": left_name,
        "candidate": right_name,
        "paired": mismatched == 0,
        "mismatched_indices": mismatched,
        "mean_candidate_minus_baseline": {
            name: fmean(values) if values else None for name, values in deltas.items()
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=ROOT / "data" / "reporter_grpo.jsonl")
    parser.add_argument("--tokenizer", type=Path, default=MINIMIND / "model")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--sft", type=Path, default=MINIMIND / "out" / "full_sft_768.pth")
    parser.add_argument(
        "--grpo",
        type=Path,
        default=MINIMIND / "out" / "reporter_grpo_20260917T174040Z_768.pth",
    )
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    parser.add_argument("--max-input-tokens", type=int, default=768)
    parser.add_argument("--max-new-tokens", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--limit", type=int, default=None, help="diagnostic prefix limit")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(args.seed)
    rows = _jsonl(args.data)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    print(json.dumps({"rows": len(rows), "device": args.device, "limit": args.limit}, ensure_ascii=False), flush=True)
    model_paths = {"sft": args.sft, "grpo": args.grpo}
    summaries = {}
    output_paths = {}
    for name, checkpoint in model_paths.items():
        output_path = args.output_dir / f"{name}.jsonl"
        output_paths[name] = output_path
        summaries[name] = _evaluate_model(
            model_name=name,
            checkpoint=checkpoint,
            rows=rows,
            output_path=output_path,
            tokenizer_dir=args.tokenizer,
            device=args.device,
            max_input_tokens=args.max_input_tokens,
            max_new_tokens=args.max_new_tokens,
            batch_size=args.batch_size,
            limit=args.limit,
        )
    result = {
        "data": str(args.data),
        "tokenizer": str(args.tokenizer),
        "device": args.device,
        "max_input_tokens": args.max_input_tokens,
        "max_new_tokens": args.max_new_tokens,
        "batch_size": args.batch_size,
        "decoding": {"do_sample": False, "temperature": 1.0, "top_p": 1.0, "top_k": 0},
        "summaries": summaries,
        "paired": _paired_summary(output_paths),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result["paired"], ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
