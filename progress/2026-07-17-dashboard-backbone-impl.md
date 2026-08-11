# Dashboard Backbone — Implementation Summary

> 实现自 `progress/2026-07-16-dashboard-backbone-plan.md`。
> 状态：初版骨架已落地，但页面级/语义缺陷在 `progress/2026-07-17-dashboard-amendment-plan.md`
> 中被记录并修复。**本文档下方「2026-07-17 修订」记录的验收结果才是当前有效状态；
> 修订前的「已通过验收」表述不适用。**

## 目标达成

把单皮肤审计工作台 `app.py` 重构为 4 页 Streamlit 多页门户：

```
EmoGame 分析看板
├── 组合总览（默认，app.py）
├── 皮肤探索      (pages/皮肤探索.py)
├── 皮肤详情      (pages/皮肤详情.py)
└── 数据工作台    (pages/数据工作台.py)
```

前三个页面只做只读分析；模拟、CSV 导入、人工证据录入、校准开关、数据审计、JSON 全部迁入「数据工作台」。

## 核心口径落实（硬性约束）

- 现金价值 / 感知性价比 / 情绪评分三者独立，未合成总价值分。
- 情绪分只来自 `RuleEngine.evaluate().evaluation_score`；`official_prior_score` 仅作标签，不进排行。
- 现金优先级固定：`manual_exact` > `manual_estimated` > `csv_release_window_uplift`（复用 `CashValueRepository.resolve`）。
- 仅 CNY 可换算记录计入组合归因收入（USD 记录保留在详情但排除于总额）。
- 缺失值保持 `None`，不按 0、不用元数据估算。
- 只有 `validation_status == 'evidence_validated'` 进情绪排行。

## 新增/修改文件

- `dashboard/models.py` — `DashboardFilters` / `PortfolioSkinRow` / `SkinDashboardDetail` / `PortfolioSummary`。
- `dashboard/query.py` — 批量只读查询层，复用现有 repo/service，不重写评估或现金优先级逻辑。
- `dashboard/filters.py` — 共享侧边栏，写入固定 `st.session_state` 键（跨页同步；主分析流程不暴露 DB 路径）。
- `dashboard/format.py` / `components.py` / `charts.py` — 格式化、状态标签、空状态、图表组件。
- `dashboard/workbench_render.py` — 从 `app.py` 迁出的工作台渲染逻辑，导入 `app` 的 helper（无循环依赖，因 `app` 仅在函数内 lazy import `workbench_render`）。
- `app.py` — 改为组合总览 + `st.navigation`；保留 `build_payload` / `import_cash_value_upload` / `save_manual_cash_value` / `load_signals` / `apply_calibration` 等被测试引用的函数（签名不变）。
- `pages/皮肤探索.py` / `pages/皮肤详情.py` / `pages/数据工作台.py` — 三个页面。
- `tests/test_dashboard_models.py` — 8 个新增单测。

## 验收结果

- `python3 -m unittest discover -s tests` → **170/170 通过**（含现有 `test_streamlit_app.py`、`test_cash_value.py`；兼容性红线保持）。
- 新增单测覆盖：读模型合并、现金优先级、FX 仅 CNY 计入、日期/缺失过滤、未验证不进排行、缺失值排序。
- Streamlit 冒烟：4 个页面均 HTTP 200、无 traceback。
- 真实数据核对（符合首版边界）：**960 皮肤、0 已验证情绪分（排行榜为空，未用先验补齐）、49 现金记录、¥1,118,977 可换算 CNY 归因收入、默认期间 2025-07-15 → 2026-07-15（止于最新收入日）**。

## 已知边界 / 后续（初版；部分已被 2026-07-17 修订取代）

- ~~组合/探索页不逐皮肤跑 `RuleEngine`~~ → 修订后已改为逐皮肤经 `FeatureBuilder`+`RuleEngine`
  评估，仅 `validation_status == evidence_validated` 计入。
- 工作台的 helper 仍从 `app` 导入（修订后移除了不存在的 `app.evidence_table` 导入）。
- 仅王者荣耀 + 本地 SQLite；不接在线 VLM/LLM；不跨游戏比较；不数据库迁移。

---

## 2026-07-17 修订（dashboard amendment）

> 依据 `progress/2026-07-17-dashboard-amendment-plan.md`。修复初版骨架未被根单测覆盖的
> 页面级/语义缺陷。

### 已修复行为

1. **工作台运行时**：删除 `dashboard/workbench_render.py` 对不存在符号 `app.evidence_table`
   的导入，改用模块内本地 `evidence_table`；所有 tab 正常渲染。
2. **情绪证据边界**：`dashboard/query.py` 用真实 `RuleEngine` 门控（覆盖率 ≥ 0.5，
   `evidence_validated`）；`emotion_score` / `perceived_value` 仅对已验证行填充。新增
   `MarketSignalRepository.get_opinion_signals`（不投射现金字段、不建表）避免现金派生
   `sales_volume` 注入情绪评估。详情页在证据不足时不给出综合情绪分。
3. **期间/币种一致性**：`period_start`/`period_end` 贯穿总览/探索/详情；现金按窗口经
   `CashValueRepository.resolve` 解析；结束早于开始时报错（`DashboardFilters.is_valid_period`）。
   USD 排除于 CNY 总额但保留审计；上线事件在收入时间线上可见绘出。
4. **只读失败关闭**：读路径移除全部 `ensure_schema`，缺文件/缺表返回空态且不建库建表。
5. **排序/空态**：`dashboard.format.sort_missing_last` 使缺失值恒排最后；探索页零结果不再
   渲染可用的下钻选择器/按钮。

### 验收结果（2026-07-17，venv）

- `python3 -m py_compile app.py dashboard/*.py pages/*.py` → 通过。
- `python3 -m unittest discover -s tests` → **181/181 通过**（170 原有 + 5 新读模型单测
  + 6 新 AppTest 页面测试）。
- `python3 -m unittest tests.test_dashboard_pages` → 覆盖四页无异常渲染，回归工作台
  ImportError、零结果下钻、缺失/部分库失败关闭。
- Streamlit 真库冒烟：`streamlit run app.py` 启动无 traceback，进程稳定存活；四页经
  `AppTest` 真库渲染均无异常。
- 真实数据快照：**960 皮肤、49 现金记录、0 已验证情绪行（信号表为空，排行为空、不用先验补齐）、
  365 收入日**。

### 剩余证据限制

- `market_signal_records` 当前为空 → 无任何皮肤通过情绪门控（符合预期）。启用排行前需先按
  P1「情绪证据」采集并持久化足够 aspect 证据。
- 元数据/图片回填与在线 VLM 仍为 P1/P2 待办，未在本次修订实现。
- 冷启动数据库初始化器保留为独立 P2 交付，读路径不代其建库。
