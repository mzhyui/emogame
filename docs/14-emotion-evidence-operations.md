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
