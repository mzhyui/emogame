# 18 — 本地 Agent 全流程（Python + Ollama）

[`scripts/run_local_agent.py`](../scripts/run_local_agent.py) 将
[06 — 智能体执行架构](06-agent-architecture.md) 中的六个节点连接成可执行的
LangGraph 工作流。输入是用户问题和唯一皮肤 `source_key`；资料来自本地 SQLite
目录、已有社区信号/证据档案与下载好的图片；图片分析和中文报告均调用 Ollama。

```text
用户问题 + source_key
  → Collector：SQLite 目录、图片路径、社区信号、同英雄对照皮肤
  → VLM Analyzer：本地图片 → Ollama L1 分类 → Ollama L2 视觉评分
  → Feature Engineer：官方字段 + VLM + 已有信号 → 33 维特征与缺失标记
  → Model Inferer：现有 RuleEngine → 运营评分、六项分数及来源
  → Business Analyzer：SalesAdvisor → 运营建议、价格证据缺口
  → Report Generator：固定系统模板 + 用户问题 + 上游结果 → Ollama 中文报告
  → report.md + state.json + chart_data.json + 提示词和调用记录
```

## 环境与启动

使用仓库 Python 3.12+ 虚拟环境及 `requirements.txt`。需要已有的
`data/wzry_skins/skins.sqlite3`，图片路径默认相对于仓库根目录解析。
脚本不会启动爬虫、下载图片、拉取模型、训练模型或写回源数据库。

```bash
# Ollama 已运行时无需重复启动
ollama serve

# 在另一个终端确认已安装模型
ollama list

# 执行完整流程；模型名称必须与本机 ollama list 一致
.venv/bin/python scripts/run_local_agent.py \
  --source-key 106-03 \
  --question '分析小乔天鹅之梦的视觉卖点、定价证据缺口和运营建议，并比较本地同英雄皮肤。'
```

省略 `--question` 时，在交互终端输入分析需求。完全不带参数时，先提示输入皮肤
搜索词，再输入问题。管道或自动任务中必须显式提供选择参数和 `--question`。

```bash
# 按皮肤名称查询。多个匹配会列出候选，并要求改用 --source-key。
.venv/bin/python scripts/run_local_agent.py \
  --search '天鹅之梦' --question '哪些结论有本地证据支持？'

# 离线验证所有确定性节点与报告提示词，不调用 Ollama
.venv/bin/python scripts/run_local_agent.py \
  --source-key 106-03 --question '分析证据缺口' --dry-run

# 没有本地图片时，只使用目录/社区资料生成报告
.venv/bin/python scripts/run_local_agent.py \
  --source-key 106-03 --question '比较目录与社区证据' --skip-vlm

# 指定其他本地目录、图片根目录和运行参数
.venv/bin/python scripts/run_local_agent.py \
  --db /path/to/skins.sqlite3 --asset-root /path/to/catalog-project \
  --source-key 106-03 --question '给出运营建议' \
  --ollama-host http://127.0.0.1:11434 \
  --vision-model qwen3.5:4b --text-model qwen3.5:4b \
  --timeout 180 --attempts 3 --strict-vlm \
  --output outputs/local_agent/my-run
```

脚本默认以本地 `http://127.0.0.1:11434` 为端点，也读取进程环境中的 `OLLAMA_HOST`；
`--ollama-host` 优先。HTTP 客户端禁用环境代理，避免 localhost 请求经过外部代理。
视觉与报告默认都使用 `qwen3.5:4b`，可分别覆盖。图执行禁用外部 LangSmith tracing。
这里不会自动读取 `.env`。相对 `--db`、`--asset-root`、`--output` 路径相对于当前目录；
省略时的数据库、图片根目录与输出根目录均相对于仓库。

## 节点、数据与实现边界

| 节点 | 实现 | 输出与边界 |
| --- | --- | --- |
| Collector | `SkinRepository`、`MarketSignalRepository`、`EmotionEvidenceRepository` | 优先保留最新证据档案及未发布/未审核状态；无档案时读已有汇总信号。只读本地数据，不采集实时趋势。 |
| VLM Analyzer | `agents/local_ollama.py` + 现有 L1/L2 提示词和严格校验器 | 同一张图片分别做分类和视觉评价；只访问配置的 Ollama，不调用 AutoDL L3。 |
| Feature Engineer | `FeatureBuilder`、`OfficialFeatureMapper`、`SkinFeatureVector` | 按固定顺序保留全部 33 个字段，缺失值为 `null`，记录来源、覆盖率和图片 SHA-256。 |
| Model Inferer | `RuleEngine` | 复用现有六方面运营评分，保留 observed/estimated 来源；尚无文档提出的训练后集成预测器。 |
| Business Analyzer | `SalesAdvisor` | 复用规则建议；人民币建议区间为 `null`，官方价格保留原文及单位。 |
| Report Generator | `agents/prompts.py` 固定模板 + 原始用户问题 + 有界上游资料 | 生成六节中文报告；检查 JSON 结构并排除已知的错误官方归因用语，文字仍需事实核验。 |

现有 `RuleEngine` 使用目录与社区信号计算运营分数，**不消费 33 维视觉向量**。
VLM 结果供特征产物、五组特征解读和报告使用，不会被伪装成社区评价或训练后的
溢价预测。`chart_data.json` 明确记录 `vlm_used_in_rule_score=false`。

五个特征组是 `aesthetic`、`belonging`、`showing_off`、`collection`、`surprise`。
产物包含各组原始特征和覆盖率；覆盖率不代表情绪评分。
`five_dimension_scores=null`，六方面运营分数单独放在 `operational_aspects` 中。
脚本不会把点券价格直接填入人民币花费字段，也不会从壁纸推断已验证的局内手感。

对照皮肤来自同英雄目录，先按相同品质排序，再按 `source_key` 排序；默认最多三个，
可用 `--competitors 0` 关闭。这是可复查的目录参照，不是视觉相似度检索。
紧凑 `skin_id` 可能重复，因此选择、关联与输出始终以 `source_key` 为主。

## 固定提示词如何使用

- 图片：复用 `vlm/prompts.py` 的 `L1_PROMPT` 和 `L2_PROMPT`，图片作为原生
  `/api/chat` 的 `messages[].images` 参数传入。
- 报告：`agents/prompts.py` 定义版本号、固定系统提示词、用户模板和六节 JSON schema。
  用户问题被 JSON 编码后插入用户模板，上游资料作为独立 JSON 数据块插入；不会修改系统规则。
- 六节保留架构文档的标题：情绪溢价总览、五维度雷达解读、定价区间建议、风险提示、
  竞品参照、运营策略建议。缺失的分数、人民币区间和竞品必须说明资料不足。
- 温度为 `0`，种子为 `42`，禁用 thinking 输出，默认上下文为 `16384`；报告生成上限
  `2400` token，可用 `--num-ctx` 和 `--report-tokens` 调整。这些设置不构成跨硬件复现保证。

请求接口见 [Ollama Chat API](https://docs.ollama.com/api/chat)，
图状态与路由见 [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)。

## 错误处理与产物

每个模型调用最多尝试三次（`--attempts 1..3`，含第一次）。连接失败、HTTP 错误、
空回复、截断输出、JSON/字段校验失败均不能作为有效结果。
完整 Markdown 代码围栏中的 JSON 可解包后校验；不会截取或修补残缺 JSON。
重试使用固定纠错提示词并附上具体校验失败原因。有限的用语检查不能保证整篇报告事实正确。
默认每次请求超时 180 秒，以容纳本地模型冷启动；这不是架构文档中尚未验证的延迟目标。

图片不存在或 VLM 失败时，默认继续生成目录/社区报告，视觉字段保持 `null`，最终状态
为 `degraded`；`--strict-vlm` 则直接进入错误处理节点。报告失败或其他节点失败时，
状态为 `failed`，退出码为 `1`。成功、降级但有报告、离线检查的退出码为 `0`，调用方
仍应检查 `state.json.status`，避免把 `degraded` 或 `dry_run` 当成全流程成功。

每条路由只使用条件边，不会同时走普通边与错误边。节点产物执行后立即保存；完成时
写入最终状态。输出目录必须是新目录，防止覆盖之前的运行。

默认产物位于 Git 已忽略的 `outputs/local_agent/<UTC时间戳>/`：

| 文件 | 内容 |
| --- | --- |
| `config.json` | 模型、端点、超时、参考日期、提示词版本及开始时间 |
| `stages/*.json` | 每个已执行节点的输出、状态和耗时；失败时也保留已有节点 |
| `state.json` | 完整最终状态、输入问题、身份、上游数据与错误列表 |
| `report_prompt.json` | 实际使用的系统/用户消息；`--dry-run` 也会生成 |
| `ollama_calls.jsonl` | 每次实际模型请求、回复、耗时与错误；图片 base64 替换为其 SHA-256 |
| `report.md` | 中文模型报告草稿；离线/失败时明确标注未生成报告 |
| `chart_data.json` | 五组特征/覆盖率与六方面运营分数，分开保存 |

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -p test_local_agent_pipeline.py -v
```

测试使用临时 SQLite 和模拟 Ollama HTTP，覆盖六节点执行顺序、数据库只读、重复 ID
选择、VLM 降级/严格失败、未发布证据来源、空/截断输出、三次重试以及既有产物保护。
实际模型与当前环境的运行证据见对应的 `progress/` 记录；单次成功不等于模型质量验证。
