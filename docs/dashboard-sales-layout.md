# Portfolio sales-dashboard layout

Run from the repository root with `.venv/bin/streamlit run app.py`.

The overview adapts the three-KPI row, blue Plotly charts, sidebar filters and
two-column composition of [Sven-Bo/streamlit-sales-dashboard](https://github.com/Sven-Bo/streamlit-sales-dashboard).
The reference submodule is pinned at `d436bc8c4fbc6424828adccad792b4f46c2e8a84`.
Its supermarket workbook and executable app remain reference assets. To view the
original demo separately, run `../.venv/bin/streamlit run app.py` from
`streamlit-sales-dashboard/` with its Excel dependencies installed.

## Metrics and filtering

- The primary KPIs are CNY attributed revenue, mean full value score, and revenue
  per skin with numeric CNY revenue. Each reports its denominator. Zero and negative
  cash values are retained; missing values are blank and excluded from averages.
- Full scores use the current scorer: observed dimensions plus catalog estimates.
  The new frontend does not turn these into independently validated emotion scores.
- Search, exact hero, quality, coverage and dates use the shared sidebar state.
  Release events follow the selected skins. Game-wide revenue remains background
  context for the selected period, labeled separately from skin attribution.
- Timeline currencies use separate charts; release counts have a separate axis.
  Missing revenue dates remain gaps. Scatter points require both numeric values.
- Rankings expose score status, confidence intervals, cash confidence, attribution
  method and source key. Coverage and scoring details are available below the charts.

## Structured analysis interface

`dashboard.analysis.build_query_spec(filters, group_by, metrics)` produces a
JSON-serializable specification. `execute_query_spec(db_path, spec)` validates it
before calling the read-only portfolio query and returns `query`, grouped `rows`,
and the contributing `source_keys`. The application supplies the trusted database
path separately. This is the interface for future agent-generated queries.

```json
{
  "dataset": "skin_portfolio",
  "filters": {"hero_name": "赵云", "period_start": "2026-01-01", "period_end": "2026-12-31"},
  "metrics": ["skin_count", "mean_score", "cash_total", "cash_mean"],
  "group_by": "quality",
  "evidence_scope": "current_portfolio"
}
```

Supported grouping fields are `hero_name` and `quality`. Filters correspond to
`DashboardFilters`; dates are ISO strings or null. Unknown keys, scopes, metrics,
invalid dates and reversed ranges are rejected. `current_portfolio` explicitly
means the current scoring and cash-resolution policy; no alternate evidence scope
is implied. Grouped outputs carry scored and numeric-CNY coverage counts.

The “分析问题” expander uses this specification to show grouped comparisons and
download CSV plus query/source JSON. It uses deterministic controls; no LLM service,
credentials, generated Python, or SQL execution is introduced.

## Verification

```bash
.venv/bin/python -m unittest discover -s tests -p test_dashboard_sales_layout.py
.venv/bin/python -m unittest discover -s tests
.venv/bin/python -m py_compile app.py dashboard/*.py
```

Existing pages and `app.py` helper imports remain supported. The original
submodule has no declared license at this revision; the integration implements
the layout in domain code and leaves the upstream source intact.
