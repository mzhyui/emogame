# 2026-07-17 — 看板骨架 + 修订 落地总结

> 汇总自 `2026-07-16-dashboard-backbone-plan.md`、
> `2026-07-17-dashboard-amendment-plan.md`、`2026-07-17-dashboard-backbone-impl.md`
> 以及工作树未提交改动（`git diff` + 未跟踪 `dashboard/`、`pages/`、`tests/`）。

## 一句话

单皮肤审计工作台 `app.py` 重构为 4 页 Streamlit 门户（组合总览 + 探索 + 详情 + 数据工作台），
并修复了初版未被根测试覆盖的页面级/语义缺陷。所有读路径失败关闭、现金与情绪严格分离。

## 落地内容

### 结构重构（dashboard 包 + pages）
- `dashboard/`：`models.py` / `query.py` / `filters.py` / `format.py` / `components.py` / `charts.py` / `workbench_render.py`。
- `pages/`：`皮肤探索.py` / `皮肤详情.py` / `数据工作台.py`；`app.py` 改为组合总览 + `st.navigation`。
- 兼容性红线保留：`build_payload` / `import_cash_value_upload` / `save_manual_cash_value` / `load_signals` / `apply_calibration` 签名不变，`app.py` 净减 ~511 行。

### 硬性口径
- 现金价值 / 感知性价比 / 情绪评分三者独立，不合成总价值。
- 情绪分只来自 `RuleEngine.evaluate()`，覆盖率 ≥ 0.5 且 `validation_status == evidence_validated` 才计。
- 现金优先级固定 `manual_exact > manual_estimated > csv_release_window_uplift`，仅 CNY 计组合归因收入（USD 留审）。
- 缺失值保持 `None`，不填 0、不用元数据估算。

### 修订修复（amendment）
- 工作台删除不存在的 `app.evidence_table` 导入 → 所有 tab 渲染。
- 新增 `MarketSignalRepository.get_opinion_signals`：只读、不投射现金字段、不建表，杜绝现金派生 `sales_volume` 注入情绪。
- 期间贯穿三页 + 币种一致性；反向期间报错；上线事件时间线可见。
- 读路径移除全部 `ensure_schema`，缺库/缺表返回空态不建库建表。
- `format.sort_missing_last` + 零结果探索页不渲染下钻按钮。

### 配套改动
- `.env.example`：新增 `WEIBO_COOKIE` 说明，统一从环境变量读取 cookie。
- `crawlers/weibo_skin_comment_crawler.py`：cookie 改读 `WEIBO_COOKIE`（不再读 `weiboSpider/.secret`）。
- `README`：由 TODO 改为带勾选背log（P0 看板正确性已全勾、P1/P2 待办）。
- `docs/07-business-analysis.md`：定价映射表重排。

## 验收

- `python3 -m py_compile app.py dashboard/*.py pages/*.py` → 通过。
- `python3 -m unittest discover -s tests` → **181/181 通过**（170 原有 + 5 模型单测 + 6 页面 AppTest）。
- Streamlit 真库冒烟：四页 HTTP 200 / `AppTest` 渲染无异常，进程稳定。
- 真实数据快照：960 皮肤、49 现金记录、0 已验证情绪行、365 收入日。

## 未提交状态

上述全部为工作树未提交改动（`git status` 显示 `M .env.example README app.py ...dashboard pages tests`）。
剩余证据限制（信号表为空 → 排行未启用；元数据/图片回填、在线 VLM/冷启动初始化器）见 P1/P2 待办，
本次未实现。
