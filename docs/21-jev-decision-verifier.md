# 21 — JEV 判别式验证器（decision-model-preview）

> 用千问AI平台决策模型（`POST /compatible-mode/v1/systemone`）为本地 Agent 报告加一层判别式验证：
> **J**udge（决策模型判定）/ **E**valuator（标注集度量）/ **V**erifier（校准阈值放行）。
> 本文记录已实测的可行性边界与据此确定的架构。

状态：**设计与可行性已实测；未接入 pipeline，未训练任何模型，无标注集，无准确率声明。**
证据：`outputs/systemone/smoke-20261004-v2.json`、`outputs/systemone/jev-demo/`（均为 Git 忽略目录），
接口实测记录见 `progress/2026-10-04-systemone-decision-model-smoke.md`。

## 1. 角色划分（沿用 docs/20 术语）

| 角色 | 由谁承担 | 边界 |
| --- | --- | --- |
| Judge | `decision-model-preview`：一次前向返回 `choice`/`noul`/`score` 与概率，不生成文本 | 判别式验证器，不是 docs/20 §7 的生成式 judge；输出只有概率，没有理由 |
| Verifier | `agents/decision_verifier.py`：极性规则 + 校准阈值 → `pass` / `escalate` / `fail`，并写审计 | 阈值来自标注集，**未校准时只能 escalate**，不能自动放行 |
| Evaluator | 标注 dev set + `evaluate` / `sweep_thresholds` | judge 自己的 confidence 不能证明其准确率，只有人工标签能（docs/20 §1） |

## 2. 现状缺口

`agents/prompts.py::validate_report` 只检查六节 JSON 结构、非空字符串和四个黑名单词，
其 docstring 自己声明 “structural checks are not factual review”；`generate_report` 生成的
报告头部也写着 “文字结论尚未经人工事实核验”。`report_spec()` 的 12 项检查逐条对应
`REPORT_SYSTEM_PROMPT` 的规则与 docs/20 §"Useful labeled cases" 的错误类别：
官方归因、来源错配、点券/人民币单位换算、假设当实测、过期当当前、算术不一致、
仅凭壁纸断言局内表现、编造五维度分数、竞品越界、缺口披露（正向要求）、依据类型（choice）、严重度（score）。

## 3. 决定架构的四条实测结论

**(a) 文档级判定不可用，必须降到 claim 级。** 同一份 spec 分别以“整篇报告 + 证据 JSON”和
“单句 + 该句所需的证据字段”提问（`scripts/probe_verifier_granularity.py`）：

| 检查项 | report 级 clean / defect | Δ | claim 级 clean / defect | Δ |
| --- | --- | --- | --- | --- |
| `gameplay_overclaim` | 0.77 / 0.04 | **−0.73（反向）** | 0.03 / 0.86 | +0.83 |
| `fabricated_radar_scores` | 0.03 / 0.33 | +0.30（不足） | 0.05 / 0.80 | +0.75 |
| `currency_unit_error` | 0.00 / 0.60 | +0.60 | 0.00 / 0.94 | +0.94 |
| 合计 | 1/3 可分，1 项反向 | | **3/3 可分，无反向** | |

合规报告里那句 “图片模型判断**不能**替代局内手感观测” 被判为违规（0.77），
说明文档级提问退化成了**不识别否定式的关键词触发**。因此验证单元是原子 claim，不是报告。

**(b) 批量只对词法型检查安全，关系型检查会塌。** 把 6 条 claim 放进一个请求（平台文档推荐的
`comments`/`c1..cN` 批量模式，`outputs/systemone/jev-demo/batched-claims.json`）：
`fabricated_radar_scores` 的违规概率从单条 0.80 掉到 0.07（Δ 由 +0.75 变 +0.05），
把证据字段内联进每条 item 也没有修复（0.06）。词法型两项在批量下仍保持 Δ=+0.75。
结论：**需要跨字段前提推理的检查（若 X=null 却给出 Y）逐条单请求；只有同构的词法检查才可批量。**

**(c) confidence 与批量组成绑定。** 同一条评论、同一 criteria，单独发 `confidence=0.63`，
放进 10 条批量后 `0.89`（smoke 实测）。`Thresholds` 因此强制绑定 `batch_size` 与 `spec_hash`，
形状不一致直接拒绝执行，而不是沿用旧阈值。

**(d) 不能用文档里的阈值。** 官方审核示例的 #8 样本，文档标 `spam`/0.69，实测 `pass`/0.89。
所有阈值必须来自本项目自己的校准工件（含 `spec_hash`、`batch_size`、`dev_set_id`、`calibrated_at`）。

**成本（实测）**：报告级 12 问 1 请求 = 111 ms / 1292 token；claim 级单条 ≈ 50–70 ms / 120–250 token；
6 条批量 = 71 ms / 757 token。12 条 claim 逐条验证 ≈ 1 s / ≈2–3k token，
相对本地 Ollama 报告生成（`--timeout 180`、2400 token 上限）可忽略。

## 4. 架构（proposed）

```
report sections + evidence
  → claim 抽取（句级切分）+ 证据绑定（每 claim 只带能判定它的字段）
  → 分型：词法型可批量 / 关系型单请求            ← 结论 (a)(b)
  → systemone judge（noul / choice / score）
  → 校准阈值 gate：pass / escalate / fail        ← 结论 (c)(d)
  → escalate 才调用生成式 judge（Ollama，必要时 L3）  ← docs/19 计算分配
  → 审计（request_id、spec_hash、state_hash、token、latency、阈值版本）
```

集成点：`agents/local_pipeline.py::generate_report` 中 `validate_report` 之后，`sections` 与
`evidence` 都已在作用域内；状态由 `completed` / `degraded` 扩展为
`completed` / `needs_review`(escalate) / `rejected`(fail)，产物与重试逻辑不变，
审计写 `systemone_calls.jsonl`，与既有 `ollama_calls.jsonl` 并列。
同一客户端也适用于 `vlm_analyze` 的 L1/L2 载荷与 `collect_data` 的证据来源打标——
那是短输入分类，正是 smoke 测出 32/32 的工作区间。

**未解决且是天花板**：claim 抽取的召回率决定整个验证器上限，漏抽的 claim 永远不会被检查
（docs/20 §1 coverage 与 selection 的区分）。抽取方案（确定性切分+字段绑定 vs 生成式抽取）
本身需要单独度量，不能假定。

## 5. 校准协议（Evaluator）

1. dev set：docs/20 列出的错误类别 + **正确报告** + **恰当表达不确定的报告**（否则验证器可以靠全部拒绝“达标”）；
   每类至少 20 条 claim，必须包含**关键词相同但语义合规**的对照句（第 3(a) 节里那些触发误报的句子）。
2. `sweep_thresholds(..., budget=false_accept 上限)`：网格步长 0.01（服务端概率就是两位小数量化），
   目标是**正确判定条数最多**而非自动化率最高，避免“全部拒绝”赢得 sweep。
3. 工件绑定 `spec_hash` + `batch_size` + `dev_set_id` + `calibrated_at`；
   改措辞、改选项、改批量大小、换网关或模型版本都必须重新校准。
4. 报告指标：`false_accept_rate`、`false_reject_rate`、`auto_rate`、`accuracy_on_resolved`、escalate 的下游成本。
5. 若与生成式 judge 集成，按 docs/20 §8 的告警：不同 prompt 不等于独立错误过程，
   必须在留出决策上度量两者是否抓到不同的错误，再决定是否集成。

## 6. 阶段计划

| 阶段 | 内容 | 通过条件 |
| --- | --- | --- |
| P0（已完成） | 客户端、spec、gate、evaluator、离线测试、可行性实测 | `tests/test_decision_verifier.py` 36 项通过；3 类缺陷 claim 级可分 |
| P1 | claim 抽取 + 证据绑定（先确定性方案） | 在 ≥20 份报告上度量抽取召回率，漏抽案例入档 |
| P2 | 标注 dev set + 阈值校准 | 产出校准工件，`false_accept_rate` ≤ 预算，且 `auto_rate` > 0 |
| P3 | 接入 `generate_report`（flag 后，仅 escalate/记录） | 审计工件齐全；不自动 reject，直到 P2 指标达标 |
| P4 | escalate → 生成式 judge 分层 | 度量分层是否抓到判别式漏掉的错误 |
| P5 | 对抗/注入子集 | 未通过前，该 gate 只是质量提示，不是安全边界 |

## 7. 明确不声明

- smoke 的 32/32 是**短文本单事实分类**的准确率，已实测**不能**外推到报告验证（第 3(a) 节）。
- 没有训练 ORM/PRM，没有声称报告正确率提升；本文只给出可执行的设计与已测边界。
- 注入抗性未测：被验证文本来自用户问题 + 本地证据，属于不可信输入，`state.note` 只是缓解不是防护。
- 第 3 节数字来自 2 份合成报告、3 类缺陷、单次运行，是**可行性探针**，不是准确率估计。
- 未修改 pipeline；本地提交与离线复核见 `progress/2026-10-10-verifier-closeout-vue-deployment.md`，不代表完成校准或生产验证。
