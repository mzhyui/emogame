#!/usr/bin/env python3
"""Render one premium-pilot visual radar chart per skin into an HTML report."""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
from pathlib import Path
import sys
from typing import Any

import plotly.graph_objects as go
from PIL import Image, ImageOps


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.premium_pilot import load_signed_uplifts


DIMENSIONS = (
    ("model_detail", "Model detail"),
    ("effect_quality", "Effect quality"),
    ("color_scheme", "Color scheme"),
    ("composition", "Composition"),
    ("uniqueness", "Uniqueness"),
    ("costume_design", "Costume design"),
    ("background_quality", "Background"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create one seven-axis visual radar chart per scored skin."
    )
    parser.add_argument("scores", type=Path, help="Path to scores.jsonl")
    parser.add_argument(
        "--output",
        type=Path,
        help="Output HTML path (default: <scores directory>/radar_plots.html)",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data/wzry_skins/skins.sqlite3",
        help="Skin database containing signed revenue uplift outcomes",
    )
    return parser.parse_args()


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"line {line_number} is not a JSON object")
            rows.append(row)
    if not rows:
        raise ValueError(f"no score rows found in {path}")
    return rows


def revenue_percentile_scores(
    rows: list[dict[str, Any]], db_path: Path
) -> tuple[dict[str, float], dict[str, float]]:
    """Map available signed uplift outcomes to average-rank scores on 0–10."""
    source_keys = {str(row.get("source_key") or "") for row in rows}
    source_keys.discard("")
    raw_values = load_signed_uplifts(db_path, source_keys)
    ordered = sorted(raw_values.items(), key=lambda item: (item[1], item[0]))
    if not ordered:
        return raw_values, {}
    if len(ordered) == 1:
        return raw_values, {ordered[0][0]: 10.0}

    scores: dict[str, float] = {}
    start = 0
    while start < len(ordered):
        end = start + 1
        while end < len(ordered) and ordered[end][1] == ordered[start][1]:
            end += 1
        average_position = (start + end - 1) / 2.0
        score = round(10.0 * average_position / (len(ordered) - 1), 2)
        for index in range(start, end):
            scores[ordered[index][0]] = score
        start = end
    return raw_values, scores


def radar_values(
    row: dict[str, Any], revenue_scores: dict[str, float]
) -> tuple[list[str], list[float]]:
    vlm = row.get("vlm")
    if not isinstance(vlm, dict):
        raise ValueError(f"{row.get('source_key', '<unknown>')} has no VLM object")

    labels: list[str] = []
    values: list[float] = []
    for field, label in DIMENSIONS:
        source_field = "vlm_composition" if field == "composition" else field
        raw_value = vlm.get(field, vlm.get(source_field))
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ValueError(
                f"{row.get('source_key', '<unknown>')} has no numeric {field}"
            )
        value = float(raw_value)
        if not 0.0 <= value <= 10.0:
            raise ValueError(
                f"{row.get('source_key', '<unknown>')} has {field}={value}, "
                "outside the 0-10 radar scale"
            )
        labels.append(label)
        values.append(value)

    media_raw = row.get("media_score")
    if media_raw is None:
        media_value = 0.0
    elif isinstance(media_raw, bool) or not isinstance(media_raw, (int, float)):
        raise ValueError(
            f"{row.get('source_key', '<unknown>')} has a non-numeric media_score"
        )
    else:
        media_value = float(media_raw) / 10.0
        if not 0.0 <= media_value <= 10.0:
            raise ValueError(
                f"{row.get('source_key', '<unknown>')} has media_score={media_raw}, "
                "outside the 0-100 source scale"
            )

    source_key = str(row.get("source_key") or "")
    labels.extend(["Social media", "Revenue score"])
    values.extend([media_value, revenue_scores.get(source_key, 0.0)])
    return labels, values


def radar_figure(
    row: dict[str, Any],
    raw_revenue: dict[str, float],
    revenue_scores: dict[str, float],
) -> go.Figure:
    labels, values = radar_values(row, revenue_scores)
    closed_labels = [*labels, labels[0]]
    closed_values = [*values, values[0]]
    hero = str(row.get("hero_name") or "Unknown hero")
    skin = str(row.get("skin_name") or "Unknown skin")
    premium = row.get("perceived_premium_score")
    visual = row.get("visual_score")
    media_raw = row.get("media_score")
    source_key = str(row.get("source_key") or "")
    revenue_raw = raw_revenue.get(source_key)
    media_note = f"{float(media_raw):.1f}" if media_raw is not None else "0 (missing)"
    revenue_note = (
        f"{revenue_scores[source_key]:.2f} (raw uplift {revenue_raw:,.1f})"
        if source_key in revenue_scores and revenue_raw is not None
        else "0 (missing)"
    )

    figure = go.Figure(
        go.Scatterpolar(
            r=closed_values,
            theta=closed_labels,
            fill="toself",
            fillcolor="rgba(86, 118, 244, 0.25)",
            line={"color": "#5676f4", "width": 2},
            marker={"color": "#3554cf", "size": 6},
            hovertemplate="%{theta}: %{r:.1f}/10<extra></extra>",
        )
    )
    figure.update_layout(
        title={"text": f"{hero} — {skin}", "x": 0.5, "xanchor": "center"},
        annotations=[
            {
                "text": (
                    f"Visual: {visual} / Premium: {premium}<br>"
                    f"Media: {media_note} / Revenue: {revenue_note}"
                ),
                "x": 0.5,
                "y": -0.12,
                "xref": "paper",
                "yref": "paper",
                "showarrow": False,
                "font": {"size": 12, "color": "#5b6475"},
            }
        ],
        polar={
            "radialaxis": {
                "range": [0, 10],
                "dtick": 2,
                "tickfont": {"size": 10},
                "gridcolor": "#d8deea",
            },
            "angularaxis": {"gridcolor": "#d8deea"},
            "bgcolor": "#ffffff",
        },
        height=450,
        margin={"l": 70, "r": 70, "t": 70, "b": 75},
        paper_bgcolor="#ffffff",
        showlegend=False,
    )
    return figure


def image_preview(row: dict[str, Any]) -> str | None:
    """Return a compact, embedded JPEG preview for a score row's local image."""
    raw_path = row.get("image_path")
    if not raw_path:
        return None
    image_path = Path(str(raw_path))
    if not image_path.is_absolute():
        image_path = ROOT / image_path
    if not image_path.is_file():
        return None

    try:
        with Image.open(image_path) as source:
            preview = ImageOps.exif_transpose(source).convert("RGB")
            preview.thumbnail((720, 480), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            preview.save(buffer, format="JPEG", quality=82, optimize=True)
    except (OSError, ValueError):
        return None
    payload = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{payload}"


def render_report(
    rows: list[dict[str, Any]], output: Path, source: Path, db_path: Path
) -> tuple[int, int]:
    raw_revenue, revenue_scores = revenue_percentile_scores(rows, db_path)
    cards: list[str] = []
    for index, row in enumerate(rows):
        hero = str(row.get("hero_name") or "")
        skin = str(row.get("skin_name") or "")
        source_key = str(row.get("source_key") or "")
        search_text = html.escape(f"{hero} {skin} {source_key}".lower(), quote=True)
        preview_uri = image_preview(row)
        if preview_uri is None:
            preview = '<div class="preview missing">Image unavailable</div>'
        else:
            alt = html.escape(f"{hero} — {skin}", quote=True)
            preview = (
                f'<img class="preview" loading="lazy" src="{preview_uri}" '
                f'alt="{alt}">'
            )
        chart = radar_figure(row, raw_revenue, revenue_scores).to_html(
            full_html=False,
            include_plotlyjs=True if index == 0 else False,
            config={"displaylogo": False, "responsive": True},
        )
        cards.append(
            f'<section class="card" data-search="{search_text}">{preview}{chart}</section>'
        )

    source_label = html.escape(str(source))
    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Premium pilot skin radar charts</title>
  <style>
    body {{ margin: 0; padding: 24px; background: #f3f5f9; color: #172033;
            font-family: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    header {{ max-width: 1500px; margin: 0 auto 20px; }}
    h1 {{ margin: 0 0 8px; font-size: 26px; }}
    p {{ margin: 4px 0 14px; color: #5b6475; }}
    input {{ width: min(460px, 100%); box-sizing: border-box; padding: 10px 12px;
             border: 1px solid #cbd3e1; border-radius: 8px; font-size: 15px; }}
    main {{ max-width: 1500px; margin: auto; display: grid;
            grid-template-columns: repeat(auto-fit, minmax(390px, 1fr)); gap: 16px; }}
    .card {{ min-width: 0; overflow: hidden; background: white; border: 1px solid #e1e6ef;
             border-radius: 12px; box-shadow: 0 2px 8px rgba(28, 39, 60, 0.06); }}
    .preview {{ display: block; width: 100%; height: 250px; object-fit: contain;
                background: #111827; border-bottom: 1px solid #e1e6ef; }}
    .preview.missing {{ box-sizing: border-box; padding-top: 110px; color: #aab2c0;
                        text-align: center; }}
    .card[hidden] {{ display: none; }}
  </style>
</head>
<body>
  <header>
    <h1>Premium pilot radar charts ({len(rows)} skins)</h1>
    <p>Nine axes on a fixed 0–10 scale. Media uses media_score / 10. Revenue is
       the within-cohort percentile rank of signed uplift. Missing media and revenue
       values are shown as zero; revenue remains validation-only. Source: {source_label}</p>
    <input id="filter" type="search" placeholder="Filter by hero, skin, or source key">
  </header>
  <main>{''.join(cards)}</main>
  <script>
    const filter = document.getElementById('filter');
    filter.addEventListener('input', () => {{
      const query = filter.value.trim().toLowerCase();
      document.querySelectorAll('.card').forEach(card => {{
        card.hidden = query && !card.dataset.search.includes(query);
      }});
    }});
  </script>
</body>
</html>
"""
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(document, encoding="utf-8")
    media_count = sum(
        isinstance(row.get("media_score"), (int, float))
        and not isinstance(row.get("media_score"), bool)
        for row in rows
    )
    return media_count, len(raw_revenue)


def main() -> None:
    args = parse_args()
    output = args.output or args.scores.with_name("radar_plots.html")
    rows = load_rows(args.scores)
    media_count, revenue_count = render_report(rows, output, args.scores, args.db)
    print(
        f"Wrote {len(rows)} radar charts to {output} "
        f"(media available: {media_count}; revenue available: {revenue_count})"
    )


if __name__ == "__main__":
    main()
