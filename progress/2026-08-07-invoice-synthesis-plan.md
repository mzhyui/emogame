# Invoice Data Synthesis Workflow — Anchored Plan

> 日期: 2026-08-07 · 状态: 已锚定，Claude 执行中
> 上游: `data/cash_value.py`（app_revenue_daily 基线、归因概念）、`data/wzry_skins/skins.sqlite3`（960 皮肤目录）

## 1. Objective

构建一条**种子化、可复现**的合成发票（invoice）数据生成工作流：为任意 7 天至 6+ 个月窗口生成**逐皮肤×逐日聚合**的发票记录（销量、收入、均价），逐日总量与真实 `app_revenue_daily` iPhone 收入基线对账，用于端到端模拟 invoice ↔ heroskin 分析管线。

## 2. Success criteria

- `python scripts/synthesize_invoices.py --preset 7d|30d|90d|180d`（或 `--start/--end`）一次命令生成完整数据集并落库。
- 逐日 `SUM(gross_revenue)` 与 `app_revenue_daily.estimated_revenue` 偏差 ≤ 1%（舍入口径见 §6）。
- 行数 = 960 × 窗口天数；`online_date` 之后才允许出现正销量。
- 同一 seed 重复运行生成逐字节一致的数据（确定性校验通过）。
- 分析就绪：`--summary`/`--export-csv` 输出逐皮肤窗口聚合（units/revenue/ASP/days_active），可直接与评分表 join（compare_score_sales 风格）。

**Minimum acceptable result**：7d 预设可运行、逐日对账偏差 ≤1%、校验脚本 + pytest 通过。

## 3. Assumptions（已声明，未再询问）

- 发票口径 = **付费交易**；免费获取皮肤记 units=0、revenue=0（保留行）。
- 对账基线仅 iPhone 平台（现有唯一收入事实）；`channel` 列恒为 `iphone`。
- 货币跟随 `revenue_import_batches.currency`（当前为 CNY 口径）。
- `online_date` 缺失的皮肤视为窗口起始已上架（基础权重）。
- `price_text` 解析失败时使用 quality 档位默认价目表。
- **合成数据绝不写入证据表**（`market_signal_records`/`opinion_evidence_items`/`skin_value_records`），只进独立合成表，带 batch/seed 溯源。

## 4. Scope

- 新模块 `data/invoice_synthesizer.py`：`SyntheticInvoiceRepository`（ensure_schema 风格）+ `InvoiceSynthesizer` 服务。
- 新表（`skins.sqlite3`）：`synth_invoice_batches`（批次溯源）+ `synthetic_invoice_daily`（逐日发票聚合）。
- CLI `scripts/synthesize_invoices.py`：窗口/种子/预设/dry-run/summary/export。
- 校验：`tests/test_invoice_synthesizer.py` + 对账报告。

## 5. Out of scope

- 交易级（逐笔订单）数据、玩家维度、Android/其他渠道。
- Dashboard 页面（本轮明确不做）。
- 修改真实 `app_revenue_daily` / 证据表。
- 将合成分数接入 `calibrate_sales_score` 训练（后续任务）。

## 6. Architecture / methodology

**Top-down 对账分配**：逐日取真实收入总额 → 按皮肤权重分配收入份额 → `units = floor(share / price)` → 残差用最大余数法回补，使逐日合计逼近基线。

权重模型（seeded）：
- `w = quality_weight × recency_boost × hero_popularity_prior × lognormal_noise`
- `recency_boost`：上架后 0–14 天发售尖峰（对限定/高品质更强），随时间指数衰减到长尾基础值。
- 周末/节假日周内季节性乘子（收入侧）。
- 价格：`price_text` 解析（点券/元）→ CNY；失败回退 quality 档默认价；偶发限时折扣事件（seeded）。

确定性：`numpy.random.default_rng(seed)`；seed、窗口、参数 JSON 全量写入 `synth_invoice_batches`。

**表结构**（`synthetic_invoice_daily`）：
`invoice_id, synth_batch, source_key, invoice_date, units_sold, unit_price, gross_revenue, discount_pct, quality, release_age_days, created_at`，UNIQUE(`synth_batch, source_key, invoice_date`)。

窗口约束：必须落在 `app_revenue_daily` 覆盖范围内（365 天，止于 2026-07-15）；越界直接报错并打印可用范围；`--end` 缺省 = 基线最后一日。

## 7. Milestones

| # | 内容 | 退出条件 |
|---|------|----------|
| M1 | 数据模型 + 生成器：两张表、repository、synthesizer、CLI 核心（--preset/--start/--end/--seed/--dry-run） | 7d 预设生成落库，行数 = 960×7，无 online_date 前正销量 |
| M2 | 对账 + 校验：最大余数残差回补、逐日对账报告、pytest（对账≤1%、确定性、行数、零上架前销量） | `pytest` 全绿；180d 运行偏差报告 ≤1% |
| M3 | 分析就绪：`--summary` 逐皮肤聚合、`--export-csv`、与 score join 示例、progress 记录与 memory 更新 | summary 输出可与评分表 join；文档落地 |

依赖：M1 → M2 → M3 串行。执行者：Claude（本轮）。

## 8. Required resources and dependencies

- 现有依赖足够：Python 3.12 + numpy + pandas + sqlite3（标准库）。
- 读取：`skins.sqlite3`（skins 表）、`app_revenue_daily`、`revenue_import_batches`。无外部服务。

## 9. Risks and mitigations

| 风险 | 缓解 |
|------|------|
| units×price 舍入导致逐日合计偏离基线 | 最大余数法在收入层面回补残差；偏差阈值进校验 |
| 窗口超出基线覆盖（365 天） | 硬错误 + 打印可用日期范围；缺省 end = 基线末日 |
| `price_text` 格式混杂（点券/元/限时/免费） | 解析器 + quality 档默认价回退；免费皮肤记 0 收入 |
| `online_date` 缺失 | 视为窗口起始已上架（已声明假设） |
| 合成数据污染真实证据链 | 独立表 + synth_batch/seed 溯源；禁止写入证据表 |
| 180d × 960 ≈ 17.3 万行体积 | SQLite 无压力；批量 executemany 写入 |

## 10. Validation criteria

1. `pytest tests/test_invoice_synthesizer.py` 全绿。
2. `--preset 180d --dry-run --report` 显示逐日偏差 ≤1%、总行数、Top 皮肤。
3. 相同 seed 两次运行的行内容哈希一致。
4. 抽查：任一皮肤在 `online_date` 之前无正销量；发售尖峰可见。

## 11. Next actions

1. ✅ 计划锚定（本文件）
2. ▶ M1：实现 repository + synthesizer + CLI
3. M2：残差回补 + 校验测试
4. M3：summary/export + 文档收尾
