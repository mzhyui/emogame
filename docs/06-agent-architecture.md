# 06 — 智能体执行架构

## 多智能体协作系统

基于 **LangGraph** 构建有状态的多智能体工作流，6 个 Agent 协作完成端到端评估。

```
                        ┌──────────────────────┐
                        │   Orchestrator Agent  │
                        │   (主管智能体)          │
                        │   - 任务分解与调度       │
                        │   - 状态管理            │
                        │   - 异常处理            │
                        └──────────┬───────────┘
                                   │
           ┌───────────────────────┼───────────────────────┐
           │                       │                       │
    ┌──────┴──────┐         ┌──────┴──────┐         ┌──────┴──────┐
    │ Collector   │         │  Analyzer   │         │  Reporter   │
    │ Agent       │         │  Agent      │         │  Agent      │
    │             │         │             │         │             │
    │ - 数据采集   │         │ - VLM 触发  │         │ - LLM 报告  │
    │ - 爬虫调度   │         │ - 特征工程  │         │ - 策略建议  │
    │ - 数据清洗   │         │ - 模型推理  │         │ - 可视化数据│
    └──────┬──────┘         └──────┬──────┘         └──────┬──────┘
           │                       │                       │
           └───────────────────────┼───────────────────────┘
                                   │
                        ┌──────────┴───────────┐
                        │   Shared State        │
                        │   (Pydantic Models)    │
                        └──────────────────────┘
```

## Agent 职责

| Agent | 触发条件 | 输入 | 输出 | 超时 |
|-------|----------|------|------|------|
| **Collector** | 用户提交评估请求 | skin_id, hero_name | raw_data (多源字典) | 30s |
| **VLM Analyzer** | Collector 完成 | raw_data.image_paths | vlm_features | 10s/img |
| **Feature Engineer** | VLM Analyzer 完成 | vlm_features + market + community | feature_vector (33 维) | 5s |
| **Model Inferer** | Feature Engineer 完成 | feature_vector | premium_result | 2s |
| **Business Analyzer** | Model Inferer 完成 | premium + features | pricing + risks + competitors | 10s |
| **Report Generator** | Business Analyzer 完成 | 全部上游结果 | LLM 自然语言报告 | 15s |

## LangGraph 工作流定义

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, Annotated
import operator

class AgentState(TypedDict):
    # 输入
    skin_id: str
    hero_name: str
    game_genre: str          # MOBA / FPS / RPG / Gacha

    # 中间产物
    raw_data: dict           # Collector 输出
    vlm_features: dict       # VLM Analyzer 输出
    feature_vector: dict     # Feature Engineer 输出
    premium_result: dict     # Model Inferer 输出
    business_analysis: dict  # Business Analyzer 输出
    report: str              # Report Generator 输出

    # 控制
    errors: Annotated[list, operator.add]
    current_stage: str


def create_evaluation_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    # 添加节点
    workflow.add_node("collect_data", collector_node)
    workflow.add_node("vlm_analyze", vlm_node)
    workflow.add_node("feature_engineer", feature_engineer_node)
    workflow.add_node("model_inference", model_inference_node)
    workflow.add_node("business_analyze", business_analysis_node)
    workflow.add_node("generate_report", report_generation_node)
    workflow.add_node("error_handler", error_handler_node)

    # 定义边
    workflow.set_entry_point("collect_data")
    workflow.add_edge("collect_data", "vlm_analyze")
    workflow.add_edge("vlm_analyze", "feature_engineer")
    workflow.add_edge("feature_engineer", "model_inference")
    workflow.add_edge("model_inference", "business_analyze")
    workflow.add_edge("business_analyze", "generate_report")
    workflow.add_edge("generate_report", END)

    # 条件边
    workflow.add_conditional_edges(
        "vlm_analyze",
        lambda s: "error_handler" if s["errors"] else "feature_engineer",
    )

    return workflow.compile()
```

## Agent 节点实现

```python
async def collector_node(state: AgentState) -> AgentState:
    crawler = get_crawler_manager()
    state["raw_data"] = await crawler.collect_all(
        skin_id=state["skin_id"], hero_name=state["hero_name"]
    )
    state["current_stage"] = "data_collected"
    return state

async def vlm_node(state: AgentState) -> AgentState:
    try:
        vlm = get_vlm_pipeline()
        state["vlm_features"] = await vlm.analyze(
            state["raw_data"]["image_paths"]["wallpaper_big"]
        )
    except Exception as e:
        state["errors"].append(f"VLM error: {e}")
    state["current_stage"] = "vlm_done"
    return state

async def feature_engineer_node(state: AgentState) -> AgentState:
    fe = get_feature_pipeline()
    vector = await fe.build_vector(
        vlm_features=state["vlm_features"],
        market_data=state["raw_data"]["market"],
        community_data=state["raw_data"]["community"],
    )
    state["feature_vector"] = vector.to_dict()
    state["current_stage"] = "features_ready"
    return state

async def model_inference_node(state: AgentState) -> AgentState:
    predictor = get_ensemble_predictor()
    state["premium_result"] = predictor.predict(state["feature_vector"])
    state["current_stage"] = "inference_done"
    return state

async def business_analysis_node(state: AgentState) -> AgentState:
    analyzer = get_business_analyzer()
    state["business_analysis"] = await analyzer.analyze(
        premium_result=state["premium_result"],
        feature_vector=state["feature_vector"],
        hero_name=state["hero_name"],
    )
    return state

async def report_generation_node(state: AgentState) -> AgentState:
    llm = get_llm_client()
    state["report"] = await llm.generate_report(
        premium=state["premium_result"],
        business=state["business_analysis"],
        skin_id=state["skin_id"],
    )
    state["current_stage"] = "completed"
    return state
```

## LLM 报告生成 System Prompt

```
你是游戏商业化分析师，专精于MOBA游戏虚拟物品定价策略。

报告结构：
1. **情绪溢价总览** (2-3句总结)
2. **五维度雷达解读** (每个维度1-2句分析)
3. **定价区间建议** (给出具体人民币区间及理由)
4. **风险提示** (列出2-3条业务风险)
5. **竞品参照** (与同类皮肤对比)
6. **运营策略建议** (2-3条可执行建议)

要求：数据驱动，语气专业但不失亲和，每条建议可落地执行，使用中文输出。
```

## Agent Tools 定义

```python
from langchain.tools import tool

@tool
def search_hero_skins(hero_name: str) -> list[dict]:
    """查询指定英雄的所有皮肤列表"""

@tool
def query_skin_price(skin_name: str) -> dict:
    """查询指定皮肤的历史定价和当前价格"""

@tool
def get_vlm_analysis(skin_id: str) -> dict:
    """触发 VLM 对皮肤图片进行视觉分析"""

@tool
def fetch_community_sentiment(hero_name: str, days: int = 30) -> dict:
    """获取社区情感数据（近N天讨论量、正面/负面比例）"""

@tool
def compare_similar_skins(skin_id: str, top_k: int = 5) -> list[dict]:
    """查找同类皮肤并返回对比数据"""

@tool
def calculate_premium(features: dict) -> dict:
    """输入特征向量，返回情绪溢价指数和五维度分"""

@tool
def get_market_trends(game_genre: str) -> dict:
    """获取特定游戏品类的市场趋势数据"""
```

## 错误处理与重试

```
                    ┌──────────┐
                    │  Node    │
                    │  Execute │
                    └────┬─────┘
                         │
                    ┌────▼─────┐     yes     ┌──────────┐
                    │  Error?  │────────────►│  Retry   │
                    └────┬─────┘             │  (max 3) │
                         │ no                └────┬─────┘
                    ┌────▼─────┐                  │
                    │  Next    │◄─────────────────┘
                    │  Node    │  (success)
                    └──────────┘
                         │
                    (3 failures)
                         │
                    ┌────▼─────┐
                    │  Error   │
                    │  Handler │──► 记录日志 + 降级 + 通知用户
                    └──────────┘
```

## 报告智能体 GRPO 训练

报告生成智能体用 `scripts/train_reporter_grpo.py` 训练，底层是 `minimind/trainer/train_grpo.py`
（git submodule）。脚本有两个模式：

```bash
# 1. 把 reporter_states.jsonl 压缩成 RLAIFDataset 提示词
.venv/bin/python3 scripts/train_reporter_grpo.py \
    data/reporter_states.jsonl data/reporter_grpo.jsonl --prepare

# 2. 启动训练（其余参数原样透传给 train_grpo.py）
.venv/bin/python3 scripts/train_reporter_grpo.py launch \
    --verifier-only --num_generations 4 --batch_size 2
```

### 训练几何：为什么必须在 `minimind/trainer/` 下运行

minimind 的所有默认路径都是相对 `minimind/trainer/` 写的：`../out`（权重读写）、
`../model`（tokenizer）、`../../internlm2-1_8b-reward`（奖励模型）。在仓库根目录运行会
同时打断这三条路径 —— 这是 2026-09-17 那次 GRPO 运行在第一步之前就失败的原因。
`launch` 模式自己 `chdir` 到该目录，调用者不需要记住这件事。

基础权重放在 `minimind/out/full_sft_768.pth`（来自 modelscope `gongjy/minimind-3-pytorch`）。
注意 `init_model` 读权重的 `save_dir` 是硬编码默认值，不受 `--save_dir` 影响，因此
权重读取位置和 GRPO 输出位置必然是同一个目录，不提供覆盖选项。

### 奖励注入：不改 submodule

`train_grpo.py` 既没有 reward hook 也没有 `main()`（入口是顶层 `if __name__ == "__main__"`），
无法被 import 后调用。`launch` 模式因此在执行前替换
`trainer.trainer_utils.LMForRewardModel` 上的两个属性，再用
`runpy.run_path(..., run_name="__main__")` 执行原脚本，submodule 保持干净：

| 替换 | 目的 |
|------|------|
| `get_score` | 混合 `verify_report()`（章节覆盖 + skin_id 落地）与奖励模型分数 |
| `__init__` | 仅 `--verifier-only` 时置空，跳过加载奖励模型 |
| `lm_checkpoint` | 把断点续训状态从 `../checkpoints` 改写到 `out/` |

`get_score` 必须装成**函数**而不是可调用对象：只有函数在实例属性查找时会被绑定，
可调用对象会以 `get_score(messages, response)` 被调用并因参数个数不符而崩溃。

`lm_checkpoint` 的改写是必要的：`train_grpo` 在保存和恢复两处都硬编码
`save_dir='../checkpoints'`，而 minimind 的 `.gitignore` 只忽略 `out` 不忽略
`checkpoints`，不改写的话每次训练都会在 submodule 里留下约 650MB 未跟踪文件。
读写一起改写，因此 `--from_resume 1` 仍然可用。

### `--verifier-only`

内置的 internlm2-1.8B 奖励模型无法在当前环境运行：它的 remote code 面向
transformers 4.41（`rope_scaling["type"]`、`DynamicCache.from_legacy_cache`、
`DynamicCache.to_legacy_cache`），而本环境是 transformers 5.10.2；强行打补丁后
前向可以跑通但**返回 NaN**（静默污染而非报错）。因此默认推荐 `--verifier-only`，
奖励完全来自可审计的 `verify_report()`。

需要注意：GRPO 是组内相对优势，当同一组的 2–4 条生成都拿不到任何章节标题时，
verifier 奖励在该组内是常数、梯度为零，此时学习信号只来自长度/重复惩罚等通用项。
verifier 要等策略能偶尔写出章节标题后才开始贡献梯度。

### 提示词预算

`train_grpo.py` 用 `input_ids[:, -max_seq_len:]` **从左侧**截断提示词（默认 768）。
源状态记录约 1974 token，压缩前 100% 被截断，会丢掉 system prompt 和大部分状态。
压缩后中位数约 632 token、最大 753，全部落在窗口内。压缩规则见
`compact_state()` 顶部的 `_DROPPED_PRICING_KEYS` 注释块 —— 每个被丢弃的字段都注明
了「哪个更便宜的字段已经表达了同一事实」。

### 运行证据

| 产物 | 位置 |
|------|------|
| GRPO 权重 | `minimind/out/grpo_768.pth` |
| 逐样本奖励审计 | `minimind/out/grpo_reward_audit_<UTC>.jsonl` |
| stdout | 建议 `screen` + `tee` 落盘（上次运行的证据只存在于 screen 回滚缓冲，机器重启后全部丢失） |

审计文件按 UTC 时间戳命名，避免重跑覆盖或混入上一次运行的记录。

## 下一步

- → [07 — 商业价值分析](07-business-analysis.md)
