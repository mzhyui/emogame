# Emotion-evidence collection and review operations

This mutable runbook supplements the immutable protocol in
`docs/13-emotion-evidence-protocol.md`. Operational corrections belong here so
they do not change the protocol hash bound to an active evidence run.

An applied manifest command snapshots the protocol as
`data/emotion_evidence/runs/<run_id>/protocol.md`. For older staged runs, the
first successful applied validation creates the same snapshot after verifying
the source file against the already-frozen run hash. Later validation uses the
snapshot by default; `--protocol` remains an explicit override and must match
the frozen hash.

## Collector behavior

Only parents whose title or body contains the exact normalized skin name are
eligible for collection. Empty Weibo parents, empty Bilibili reply pages, and
videos with closed comment sections do not consume a parent slot. The collector
continues through the bounded search candidates until it fills the evidence cap,
uses all available productive parent slots, or exhausts exact-skin candidates.

Bilibili discovery checks two result pages for every locked aspect query so a
productive exact-skin parent is not lost behind the first result page. Bilibili
reply requests use `ps <= 20` and advance the platform-provided `next` cursor
until the assigned per-parent budget is reached.

## Minimal human-review handoff

Collection and blinded CSV generation need only Python 3.12 and the packages in
`requirements-emotion-review.txt`; Streamlit, Ollama, Torch, and the model stack
are not needed. Before applied Weibo collection, set `WEIBO_COOKIE` in the
untracked `.env` file. From the repository root:

```bash
python3 -m venv .venv-emotion-review
env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy \
  .venv-emotion-review/bin/pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-emotion-review.txt
.venv-emotion-review/bin/python scripts/collect_emotion_evidence.py --apply --retry-completed --report data/emotion_evidence/runs/20260902-current100-v1/collection-report-v2.json
.venv-emotion-review/bin/python scripts/run_emotion_evidence.py review-pack --phase development --output data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv
.venv-emotion-review/bin/python scripts/run_emotion_evidence.py review-pack --phase development --output data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv --apply
```

The first `review-pack` command is a non-mutating coverage check. It should
report `skins_with_rows: 20` before the applied command writes the CSV. Keep the
identity and evidence columns unchanged. Humans fill only these columns:

- `relevance`: `relevant`, `irrelevant`, or `uncertain`;
- `aspects`: pipe-separated locked names, such as
  `visual_appeal|in_game_feel`;
- `polarities`: the same aspects with integer values from $-2$ to $+2$, such as
  `visual_appeal:2|in_game_feel:1`;
- `actual_use`: `yes` or `no`;
- `confidence`: a decimal from `0` through `1`;
- `notes`: optional reviewer notes.

For `irrelevant` or `uncertain`, leave `aspects` and `polarities` empty. Make
separate copies for each reviewer; never edit or reuse an imported reviewer
original.

## Declared final-truth operation

When the project owner explicitly declares one completed review artifact to be
the final truth, import it with the distinct `final_truth` kind. This is an
opt-in single-source policy, not two-review adjudication, and the artifact hash
is bound once per run and phase:

```bash
.venv/bin/python scripts/run_emotion_evidence.py import-review \
  data/emotion_evidence/runs/20260902-current100-v1/development-v2.csv \
  --reviewer-id development-v2-final-truth \
  --kind final_truth \
  --apply
```

Model selection prefers declared final truth when present and requires at least
200 shared judgments under policy `single_human_final_truth_v1`. It still
reports the locked relevance, aspect, and polarity quality metrics. Declaring
the truth source does not make a failing extractor pass those metrics, does not
fill skins without exact evidence, and does not bypass locked calibration,
production audit, per-skin qualification, or publication gates.

To evaluate a local extractor only on evidence represented in the declared
truth artifact, use `annotate-local --phase development --truth-only`. The
operation remains resumable and does not annotate unrelated production rows.

Build the direct catalog report from the declared artifact with:

```bash
.venv/bin/python scripts/score_final_truth_emotion.py --apply
```

The report contains all 960 catalog skins. `observed_emotion_score` is a direct
summary of the labeled aspects and renormalizes only over aspects present in
the artifact. `complete_six_aspect_score` follows the locked six-aspect formula
and remains null if any aspect is absent. Skins with no truth rows or no
relevant truth stay null rather than being imputed as neutral.

## Dashboard scores for all available comments

The Streamlit dashboard applies this display precedence per skin:

1. declared human final truth for reviewed skins, including an explicit
   no-relevant result;
2. the frozen selected model over all exact-mapped, non-synthetic,
   non-quarantined comments in the active run;
3. a published RuleEngine score, when one exists;
4. blank when none of the above supplies an observed emotional dimension.

The comment-model path requires complete annotation coverage for every usable
comment belonging to that skin and exact agreement with the run's frozen model
digest and prompt hash. It calculates `observed_emotion_score` over the aspects
actually found and never substitutes neutral values for missing aspects.

These comment-model values are exploratory display scores when the selected
extractor has not passed its quality gate. Showing them does not change the
calibration, audit, release, or publication state. The detail page exposes the
model name, gate state, aspect coverage, and relevant/total comment counts.
