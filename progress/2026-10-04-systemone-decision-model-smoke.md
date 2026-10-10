# systemone 决策模型分类能力实测（decision-model-preview）

- Date: `2026-10-04`
- Project: `emogame`
- Task type: `verification` (live API smoke test, no product code changed)
- Status: `done` — classification capability verified; three doc-vs-server discrepancies recorded
- Evidence state: `verified` (transcripts below)

## What was added

| Path | Purpose |
| --- | --- |
| `scripts/smoke_systemone_decision_model.py` | Live smoke test: 10 scenarios × N repeats, schema + invariant validation, `choice` accuracy vs known labels, determinism comparison, contract probes, JSON transcript |
| `tests/test_systemone_decision_model.py` | Offline unit tests (30, mocked, no network) covering the validator, gateway selection, determinism comparator and scenario definitions |
| `outputs/systemone/smoke-20261004-v2.json` | Full request/response transcript of the passing run (gitignored) |
| `outputs/systemone/smoke-20261004-v2.log` | Human-readable receipt of the same run (gitignored) |
| `outputs/systemone/smoke-20261004.json` | Earlier run kept for contrast: it retains the 4 hard failures diagnosed below |

## Endpoint selection (the blocking finding)

The API reference page documents `https://trial.cn-beijing.maas.aliyuncs.com`, but that host rejects the
repository credential with `401 InvalidApiKey`. The key in `.env` is a **Token Plan** key
(`sk-sp-` prefix, 115 chars, `sha256[:8]=98e0271e`), and the platform's `api-key` doc states that
`sk-sp-` keys must not be used against the generic gateway. The working endpoint is the one in
`token-plan/best-practices/decision-model.md`:

```
https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/systemone
```

Probed and rejected: `trial.cn-beijing.maas.aliyuncs.com` (401 InvalidApiKey),
`dashscope.aliyuncs.com/compatible-mode` (401 invalid_api_key, `/v1/systemone` 404),
`maas.qianwenaiapi.com` (404 on `/v1/systemone`), `platform.qianwenai.com` (static docs host, 405).
The script now selects the gateway from the key prefix (`sk-sp-` → token-plan, `sk-ws-` → platform,
else trial) and can be overridden with `--base-url` / `SYSTEMONE_BASE_URL`.

## How to re-run

```bash
.venv/bin/python scripts/smoke_systemone_decision_model.py --repeats 2 \
    --json outputs/systemone/smoke-$(date +%Y%m%d).json     # exit 0 = all hard checks pass
.venv/bin/python scripts/smoke_systemone_decision_model.py --list
.venv/bin/python -m unittest tests.test_systemone_decision_model   # offline, no network
```

## Results (run of 2026-10-04, 22 HTTP calls, 20 scenario + 2 probe)

- `choice` classification: **32/32 asserted labels correct** (16 asserted questions × 2 repeats).
  Covers 2-way, 3-way, 4-way and 6-way option sets, an `other` fallback bucket, and a 10-question
  single-request batch.
- `noul`: 14/14 asserted sides correct. `score`: 10/10 asserted values inside the expected band.
- Determinism: identical labels, scores, confidences and probability vectors across repeated
  identical calls for all 10 scenarios (0 drift).
- Latency: server-reported 40.0 / 52.8 / 87.2 ms (min / median / max); client wall 0.084–0.172 s.
  The 10-question batch cost 87.2 ms and 838 input tokens, i.e. sub-linear per question.
- Billing: 4848 input tokens total; no output tokens reported. No answer carried a generated-text
  field, matching the documented "不生成文本" contract.
- The API-reference example reproduces exactly: `department=billing` (p=0.93/0.07),
  `escalate` P(yes)=1.00, `severity=2.25` with `{0:0, 1:0.01, 2:0.73, 3:0.26}`.

## Discrepancies and caveats

1. **Documented `score` level range is not enforced.** Docs say 2–255 levels; a 1-level `score`
   question returned `200` with `score=0.0`, `confidence=1.0` and `input_tokens=0` (no forward pass
   billed). An undeclared question `type` *is* rejected (`400 InvalidParameter`, pydantic-style tag
   error listing `noul|choice|score`), so validation exists but is incomplete.
2. **Published confidences for the moderation sample do not reproduce.** All 9 asserted labels match
   the doc's output table, but the doc's one 转人工 case (#8, 带链接的技术分享) came back `pass`
   with `p(pass)=0.94`, `confidence=0.89` instead of `spam` / 0.69. Other items also differ
   (#1 doc 1.00 → 0.84; #10 doc 1.00 → 0.81). Do not treat the doc's confidence values as a
   calibration reference; re-measure before picking a routing threshold.
3. **Batch composition changes per-item confidence.** The same #8 comment and criteria returned
   `pass` `p=0.81` / `confidence=0.63` when sent alone versus `p=0.94` / `confidence=0.89` inside the
   10-item batch — the label held, the confidence moved by +0.26. Any `confidence >= 0.9` auto-action
   rule (as in the doc's Slash Command example) is therefore batch-size dependent.
4. **Probabilities are quantised to 2 decimals.** Every returned probability was a multiple of 0.01,
   and 6 answers summed to 0.99 or 1.01. The validator allows `0.005 × levels` deviation and records
   it as an observation; downstream thresholding cannot resolve differences below 0.01.
5. **`confidence` is not `top-1 p`.** Equal in 18/38 `choice` answers, otherwise lower
   (`top1_p - confidence` median +0.010, max +0.180). Treat it as a separately calibrated field.
6. **`noul` answers carry no `probabilities` object** (only `P(yes)`), matching the reference example
   but not the shared `Answer` schema, which lists `probabilities` for all types.
7. **Question wording dominates `noul` results.** An earlier ambiguous question
   ("是否表现出购买或已购买的付费意愿信号") returned P(yes)=0.32 for a comment containing 已经入手了 and
   0.67 for 再也不买了 — the model was matching literal purchase mentions, not intent. Splitting it
   into `mentions_purchase` (0.94 / 0.98 yes) and `future_paid_intent` (1.00 yes / 0.00 no) produced
   clean, expected separations. Prefer one narrow judgment per `noul`.
8. Forced-choice without a fallback bucket silently absorbs out-of-taxonomy input: a 错别字 ticket was
   classified `technical` (confidence 0.79) when only `billing|technical` were offered, and `content`
   (confidence 1.00) once that option existed.

## Not covered (left unresolved)

- Prompt-injection / adversarial `state` resistance (the doc's stated motivation for using a decision
  model instead of a chat model) — untested.
- Scale limits: >16 questions per request, 255 `choice` options, 255 `score` levels, 65536-token
  `state` truncation behaviour — untested.
- Rate limits, concurrency, error-retry semantics, and non-`sk-sp-` key families (no such credential
  available here).
- Single account, single region, one run per configuration; results describe this gateway and key on
  2026-10-04, not a standing guarantee about the model.
