# 11 — Agentic RL 方法综述与 Agent Lightning 相关工作

> 范围：本文以 Agent Lightning 为中心，梳理 10 篇与多轮 LLM Agent 强化学习直接相关的代表性工作。内容截至 2026-08-27；除已明确发表在会议的工作外，均应视为预印本，不能把不同任务、基座模型或评测协议的分数直接横向比较。

配套演示：[PPTX](11-agentic-rl-method-survey.pptx)；可再生成的[幻灯片源文件](11-agentic-rl-method-survey-slides.md)。

## 1. 结论先行

**Agentic RL 的核心难题不是单独选择 PPO 或 GRPO，而是把真实 Agent 的执行过程变成可学习、可追溯、可复现的交互数据。** 对一个多轮 Agent，学习对象是如下闭环：

```text
任务 + 历史上下文 + 工具观测
              ↓
        LLM policy
              ↓
推理 / 工具调用 / 委派 / 最终回答
              ↓
环境反馈 + 验证器 + 奖励
              ↓
轨迹切分与归因 → 策略更新
```

与单轮推理 RL 相比，Agentic RL 必须额外处理五件事：

1. **状态和动作的边界**：一次工具调用、一次子 Agent 委派或一次上下文压缩是否是一个动作？
2. **奖励可信度**：最终答案正确不等于过程可靠；格式、引用、工具参数、成本和安全性都可能需要验证。
3. **长轨迹归因**：终局奖励如何分配给很早以前的检索、计划和工具选择？
4. **探索与训练数据**：没有足够多样且有信息量的失败轨迹，梯度更新没有学习信号。
5. **训练系统接入**：生产 Agent 通常不应为了训练而重写执行逻辑，且每条训练样本必须保留版本和奖励来源。

Agent Lightning 的突出价值主要在第 3 和第 5 点：它尝试将 Agent 执行与训练解耦，以统一执行数据接口收集轨迹，再通过分层的 LightningRL 做训练转移和归因 [8]。

## 2. 工作定义与范围边界

本文中的 Agentic RL 指：策略 $\pi_\theta$ 在多轮环境交互中选择文本、工具调用、工作流分支或委派动作，并以环境反馈优化参数或明确的可训练资源。形式上，一条轨迹可写作 $\tau=(s_1,a_1,o_1,\ldots,s_T,a_T,o_T)$，目标是最大化验证后的期望回报 $\mathbb{E}_{\tau\sim\pi_\theta}[R(\tau)]$。

下列相邻方向很重要，但不应混为一谈：

- **Agent SFT / trajectory imitation**：从专家轨迹学习；它提供冷启动，但不等于通过在线环境反馈优化策略。
- **Verbal reinforcement / reflection**：把失败总结写回提示词或记忆而不更新模型权重。Reflexion 是经典基线，不是梯度型 Agentic RL [11]。
- **单轮 reasoning RL**：只优化一个完成序列；若没有真实工具观测与多轮决策，不足以代表 Agentic RL。
- **传统 MARL**：关注多决策体博弈与协调；LLM 多 Agent 工作流还面对文本上下文、工具接口和训练–执行表示不一致的问题。

## 3. 十篇代表性相关工作

| # | 工作 | 核心方法 | 对 Agent Lightning 的启示 |
|---|---|---|---|
| 1 | [WebGPT (2021)][1] | 在浏览器环境中用人类示范和偏好反馈训练问答 Agent，要求输出支持答案的引用。 | 将“浏览—证据—回答”作为一个优化对象，是后续外部工具 Agent 的重要前身。 |
| 2 | [WebRL (2024)][2] | 从失败尝试生成自演化课程，配合 outcome-supervised reward model 和自适应在线 RL。 | 任务稀缺、奖励稀疏与策略漂移必须一起处理。 |
| 3 | [Search-R1 (2025)][3] | 仅用 RL 学习多轮搜索查询；以检索 token masking 稳定训练，并使用结果级奖励。 | 让“何时、搜什么”成为学习策略，而不是固定 prompt。 |
| 4 | [DeepResearcher (2025)][4] | 在真实开放网络中端到端训练研究 Agent，而非仅在固定 RAG 语料库中训练。 | 真实环境的噪声、网页变化和工具失败应进入训练与评测。 |
| 5 | [ReTool (2025)][5] | 让模型在自然语言推理中实时调用代码解释器，并以任务结果奖励学习调用时机与方式。 | 工具调用应是可观测、可验证、可归因的决策单元。 |
| 6 | [ToolRL (2025)][6] | 用 GRPO 研究工具选择、参数、格式和正确性等细粒度奖励设计。 | 将互相纠缠的粗粒度奖励拆开，通常更利于稳定学习。 |
| 7 | [RAGEN / StarPO (2025)][7] | 提出轨迹级多轮 Agent RL；通过轨迹筛选、critic 和解耦 clipping 缓解训练崩溃。 | 多轮环境下，直接搬用单轮 PPO/GRPO 可能不稳定。 |
| 8 | [Agent Lightning (2025)][8] | 统一执行数据接口、训练–Agent 解耦架构、分层 LightningRL 与信用分配模块。 | 重点是把任意 Agent 的实际运行转成可训练数据，支持复杂工作流和多 Agent。 |
| 9 | [Agent-R1 (2025/2026)][9] | 以 step-level transition 代替不断增长的单一 token 序列，并分离工作流、环境与优化接口。 | 与 Agent Lightning 共同反对把长 Agent 轨迹简单拼接为单段语言模型上下文。 |
| 10 | [A Practitioner's Guide to Multi-turn Agentic RL (2025)][10] | 系统考察 environment、reward、policy 三个支柱，以及 PPO、GRPO、RLOO 与 SFT/RL 配比。 | 实验设计应共同调优环境难度、奖励稀疏度和优化器，而非孤立选算法。 |

### 3.1 互补的非梯度基线

Reflexion 将环境反馈转成自然语言反思，写入 episodic memory，以后续试次的上下文来改善行为，而非更新模型权重 [11]。Agent-R 则用 MCTS 从失败路径附近构造修订轨迹，并进行迭代自训练 [12]。两者适合做低成本自改进或冷启动对照；它们不能替代 Agent Lightning 所针对的在线、参数化多轮 RL。

## 4. 方法分类与综合比较

| 设计轴 | 主要选择 | 代表工作 | 工程含义 |
|---|---|---|---|
| 学习目标 | 整个 Agent policy、工具使用子策略、提示词/记忆/路由资源 | Agent Lightning、ToolRL、ReTool | 先冻结无关组件，只优化具有明确动作边界的组件。 |
| 环境 | 固定语料、模拟网页、实时网页、代码解释器、文本世界 | Search-R1、WebRL、DeepResearcher、ReTool、RAGEN | 训练环境越真实，复现实验和可靠验证也越困难。 |
| 奖励 | 最终成功、偏好模型、执行验证、格式/参数/成本子奖励、逐步 shaping | WebGPT、ToolRL、WebRL | 奖励必须可审计，且不能只奖励“看起来合理”的文本。 |
| 归因单元 | 完整序列、被 mask 的 token、step/turn、span/segment、层级节点 | Search-R1、Agent-R1、Agent Lightning | 长轨迹系统应明确奖励分配给哪个可执行决策。 |
| 探索数据 | 人类示范、自生成课程、多样 rollout、真实交互 | WebGPT、WebRL、RAGEN | 成功与失败样本都要有覆盖；单一贪心轨迹无法支持稳健 RL。 |
| 优化 | PPO、GRPO、RLOO、层级变体、SFT 到 RL 的日程 | ToolRL、RAGEN、Practitioner's Guide | 优化器与奖励稀疏度、轨迹长度和初始 SFT 质量相互耦合。 |
| 系统架构 | 耦合式训练 loop；观测驱动的训练–执行解耦 | Agent Lightning、Agent-R1 | 分离可减少对既有 Agent 的侵入，但 trace schema 必须精确定义。 |

## 5. Agent Lightning 的方法拆解

Agent Lightning 的贡献应被理解为一个**面向现有 Agent 的训练接口和归因框架**，而不是只提出一个新的偏好优化损失。

### 5.1 执行与训练解耦

Agent 保持原有运行方式，例如使用 LangChain、OpenAI Agents SDK、AutoGen 或自定义实现；运行时的 LLM 调用、工具调用、观测和资源更新被采集到统一数据层。训练器再读取这些轨迹，返回更新后的模型权重或提示词等资源 [8]。这使 Agent 逻辑不必绑死在某个 RL runner 中。

### 5.2 统一数据接口

一个可训练的 trace 至少应记录：

- `task_id`、输入、上下文和 Agent/workflow 版本；
- 动作前状态、模型/子 Agent 标识、采样参数与动作；
- 工具名称、参数、工具版本、观测和异常；
- 终止原因、总奖励、局部奖励、验证器版本和奖励来源；
- 策略版本、数据选择规则、重放或过滤规则。

缺少这些字段时，后续无法区分“模型提升”“工具变化”“prompt 漂移”或“奖励器变化”，也不能可靠重放训练样本。

### 5.3 分层信用分配

对于嵌套工作流，终局回报 $R(\tau)$ 不能自动说明哪个决策带来成功。Agent Lightning 的 LightningRL 将执行过程分解为更小的训练 transition，并引入 credit assignment module，以处理动态工作流和多 Agent 场景 [8]。Agent-R1 则以 step-level transition 作为基础抽象 [9]。二者共同指出：把所有历史、推理和工具观测直接拼为一个 token 序列，会造成上下文增长与 rollout/training 表示不匹配。

### 5.4 与相邻方法的差异

| 问题 | Agent Lightning | Agent-R1 | ToolRL / ReTool | WebRL / DeepResearcher |
|---|---|---|---|---|
| 主贡献 | 训练系统解耦与层级归因 | step-level 轨迹表示与模块化接口 | 工具策略与奖励设计 | 网页环境、课程和真实交互 |
| 主要优化对象 | 可选模型或资源，可定位到部分 Agent | step/turn 级可兼容优化 | 工具选择和调用策略 | 端到端网页 Agent |
| 最大风险 | trace 语义不完整或奖励来源不透明 | step 切分不合理 | 子奖励被投机优化 | 环境非平稳和评测不可复现 |

## 6. 关键方法审查

### 6.1 奖励：准确不等于可靠

终局正确性是最便宜的信号，但它无法定位失败原因。ToolRL 的结果支持更细粒度的工具奖励设计：工具选择、参数、格式和正确性不应被一个不可解释的总分替代 [6]。不过，过度的 dense reward 会把优化压力转移到容易投机的代理指标，例如无意义的长推理、更多工具调用或奖励模型偏好的措辞。

实践上，应优先使用：

1. 可执行的端到端 verifier；
2. 无效工具调用、超预算、无证据引用和安全违规的显式惩罚；
3. 经验证与最终任务成功相关的有限 shaping reward；
4. 与训练奖励器独立的 hold-out 评测。

### 6.2 归因：长时程的真正瓶颈

若仅把一个最终分数广播给全部 token，早期的规划、检索和工具选择会得到高度噪声的梯度。Search-R1 通过检索 token masking 稳定其训练 [3]；Agent-R1 将动作–观测交互建模为 step transition [9]；Agent Lightning 更进一步面向工作流层级切分轨迹 [8]。适当的归因粒度应与实际可执行动作相匹配，而不能仅由 prompt 拼接方式决定。

### 6.3 探索与稳定性：不能只增加 rollout 数

WebRL 从失败中生成新任务，推动课程随能力边界演化 [2]。RAGEN 在多轮环境中观察到训练后期崩溃，并报告较高任务/初始状态多样性、适中的交互粒度和更频繁采样有助于泛化；其稳定化版本使用过滤、critic 和更灵活的梯度塑形 [7]。因此，数据质量、轨迹多样性和奖励信噪比通常先于更大 batch 或更长训练时间。

### 6.4 真实环境：收益与代价并存

DeepResearcher 将 Agent 放在开放网络中训练，而非假定固定语料库包含全部信息 [4]。这增加了生态有效性，但网页变化、搜索排序、访问失败和内容注入也让结果更难重放。生产训练应将网页快照、工具返回、错误码、时间戳和评测版本存档，而不能只存最终文本。

## 7. 局限与研究空白

1. **奖励黑客与规格错配**：最终用户价值往往不可完全自动验证。
2. **环境与工具非平稳**：同一策略在不同日期、API 版本或检索排序下可能表现不同。
3. **离策略数据漂移**：旧轨迹的 policy、prompt、工具 schema 和奖励定义可能已经失效。
4. **多 Agent 的因果归因**：最终成功无法自然区分 planner、retriever、coder、critic 或 router 的边际贡献。
5. **成本不应是附属指标**：成功率要与 token、工具调用次数、墙钟时间、外部 API 成本和失败严重度一起报告。
6. **评测外推不足**：网页、代码和文本世界的高分并不自动意味着通用 Agent 能力提升。

## 8. 面向本仓库的最小可证伪实验路线

本仓库已经固定 Agent Lightning 子模块，具体本地安装、GPU 边界和 Calc-X POC 请见 [文档 10](10-agent-lightning-local.md)。在将其用于情绪价值评估 Agent 前，建议按以下顺序推进：

1. 先为当前 Agent 加 trace，不先做 RL；确认每个工具调用和奖励都可重放。
2. 选择单一、低风险、可执行验证的子任务，例如结构化字段抽取或证据一致性检查。
3. 建立 SFT-only、prompt-only 和 RL 三组基线，使用完全相同的 Agent scaffold、预算和 hold-out 集。
4. 初版只用端到端验证奖励，加无效调用与成本惩罚；不要一开始引入大量主观子奖励。
5. 在离线 sandbox 验证后，再逐步接入真实网络或外部服务。
6. 报告成功率、校准/证据指标、平均 token、工具调用数、成本、失败类型和置信区间。
7. 用新工具 schema、更长轨迹、受扰动观测和成本上限做分布外测试。
8. 多 Agent 系统先逐角色优化，再实验联合训练；否则无法解释收益来自哪个组件。

## 9. 阅读顺序

建议先读 WebGPT 了解“环境交互 + 人类反馈”的起点，再读 ToolRL、ReTool 和 Search-R1 了解工具奖励与训练单位；随后读 RAGEN 掌握多轮稳定性，最后将 Agent Lightning 与 Agent-R1 放在一起阅读，以理解工程接口与 credit assignment 的差异。WebRL、DeepResearcher 和 Practitioner’s Guide 适合在设计课程、真实环境评测和优化器消融时查阅。

## 参考文献

[1]: https://arxiv.org/abs/2112.09332 "Nakano et al. (2021), WebGPT"
[2]: https://arxiv.org/abs/2411.02337 "Qi et al. (2024), WebRL"
[3]: https://arxiv.org/abs/2503.09516 "Jin et al. (2025), Search-R1"
[4]: https://arxiv.org/abs/2504.03160 "Zheng et al. (2025), DeepResearcher"
[5]: https://arxiv.org/abs/2504.11536 "Feng et al. (2025), ReTool"
[6]: https://arxiv.org/abs/2504.13958 "Qian et al. (2025), ToolRL"
[7]: https://arxiv.org/abs/2504.20073 "Wang et al. (2025), RAGEN"
[8]: https://arxiv.org/abs/2508.03680 "Luo et al. (2025), Agent Lightning"
[9]: https://arxiv.org/abs/2511.14460 "Cheng et al. (2025/2026), Agent-R1"
[10]: https://arxiv.org/abs/2510.01132 "Wang and Ammanabrolu (2025), A Practitioner's Guide to Multi-turn Agentic Reinforcement Learning"
[11]: https://papers.neurips.cc/paper_files/paper/2023/hash/1b44b878bb782e6954cd888628510e90-Abstract-Conference.html "Shinn et al. (2023), Reflexion"
[12]: https://arxiv.org/abs/2501.11425 "Yuan et al. (2025), Agent-R"

---

研究辅助声明：本文由 AI 辅助完成检索、来源核验、综合和起草。各项方法性表述均指向所列原始论文；在用于实验立项或论文引用前，应由研究者阅读原文、确认版本与复现实验设置。
