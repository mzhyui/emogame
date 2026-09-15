# PPO 算法详解：从 Slide 11 到 Agentic RL 实现

> 配套材料：`ppt/agentic-rl-method-and-implementation-roadmap.pptx` 第 11 页。
>
> 目标：把该页的“冻结旧策略 → rollout → reward → GAE → minibatch update → hold-out eval”展开为可实现、可检查、可复现的 PPO 训练说明。

## 1. 一句话理解 PPO

Proximal Policy Optimization（PPO）是一类 **on-policy、Actor–Critic** 策略梯度算法。它先用当前策略的冻结快照采集一批轨迹，再对同一批数据做若干轮小批量更新；更新时通过 clipped surrogate objective 限制“继续推动策略远离采样策略”的收益，从而降低一次更新过猛导致训练崩溃的风险。

PPO 的核心不是“把新旧策略的每个概率比值硬性锁在区间内”，而是：

- 用旧策略 $\pi_{\theta_{\mathrm{old}}}$ 产生本轮训练数据；
- 估计每个动作相对当前基线的优势 $\hat A_t$；
- 比较新旧策略对已采样动作给出的概率；
- 当这个变化已经超过 $[1-\epsilon,1+\epsilon]$ 且变化方向有利于当前目标时，不再从更大的变化中获得额外目标收益；
- 同时训练价值函数，并监控 KL、clip fraction、熵、价值拟合和真实任务指标。

原始 PPO 由 Schulman 等人在 2017 年提出，其主要工程动机是以比 TRPO 更简单的方式支持多轮 minibatch 更新。GAE 则为 Actor–Critic 提供低方差、可调偏差的优势估计。PPO 论文和 GAE 论文分别见文末参考资料。

## 2. 先区分四个容易混淆的模型

Slide 11 沿用了 LLM/RLHF 场景常见的 Actor–Critic–Reward–Reference 四件套。它们职责不同：

| 组件              | 记号                            | 是否训练 | 职责                                                            |
| ----------------- | ------------------------------- | -------: | --------------------------------------------------------------- |
| Actor / policy    | $\pi_\theta(a\mid s)$         |       是 | 根据状态产生动作，是最终要改进的策略                            |
| Old policy        | $\pi_{\theta_{\mathrm{old}}}$ | 每轮冻结 | 产生本轮 rollout，并提供`logp_old`；用于 PPO importance ratio |
| Critic / value    | $V_\phi(s)$                   |       是 | 预测从状态出发的期望回报，作为优势估计的 baseline               |
| Reward / verifier | $R$ 或 $r_\omega$           | 通常冻结 | 对环境转移、工具执行或完整结果给出训练奖励                      |
| Reference policy  | $\pi_{\mathrm{ref}}$          |     冻结 | 在 RLHF/LLM 训练中约束策略不要偏离某个 SFT/基线模型太远         |

关键边界如下：

1. **Critic 不是 reward model。** Critic 估计“当前策略下未来还能得到多少回报”；reward model 或 verifier 定义“这一步或这条轨迹得多少分”。
2. **Old policy 不是 reference policy。** $\pi_{\theta_{\mathrm{old}}}$ 通常每个 PPO iteration 都更新；$\pi_{\mathrm{ref}}$ 往往跨很多 iteration 保持冻结。
3. **Reference policy 不是原始 PPO 的必需组件。** 它是 RLHF/LLM 应用中常见的行为锚点；标准控制任务可以只有环境奖励、Actor 和 Critic。
4. **PPO 仍是 on-policy。** 对同一批新轨迹做多个 epoch 不等于可以无限重放历史数据。策略明显变化后，旧 batch 应失效。

## 3. 数学问题定义

将环境写成 MDP 或 POMDP。在时间步 $t$：

- 状态或策略可见上下文为 $s_t$；
- Actor 采样动作 $a_t\sim\pi_\theta(\cdot\mid s_t)$；
- 环境返回奖励 $r_t$、下一状态 $s_{t+1}$ 和终止信号；
- 一条轨迹为 $\tau=(s_0,a_0,r_0,\ldots,s_T)$。

目标是最大化折扣回报：

$$
J(\theta)=\mathbb E_{\tau\sim\pi_\theta}\left[\sum_{t=0}^{T-1}\gamma^t r_t\right],
$$

其中 $\gamma\in[0,1]$ 是 discount factor。

策略梯度的基本形式是：

$$
\nabla_\theta J(\theta)
\approx
\mathbb E_t\left[\nabla_\theta\log\pi_\theta(a_t\mid s_t)\hat A_t\right].
$$

$\hat A_t>0$ 表示动作 $a_t$ 比状态 $s_t$ 下的策略平均表现更好，应提高其概率；$\hat A_t<0$ 表示它更差，应降低其概率。

普通策略梯度直接重复更新容易让新策略快速偏离产生数据的旧策略。PPO 用新旧动作概率比和 clipped objective 控制这一过程。

## 4. PPO 一轮训练的完整数据流

```text
冻结本轮旧策略 πold
        ↓
用 πold 与环境并发交互，记录完整 rollout
        ↓
固定 reward/verifier 计算逐步奖励和终局奖励
        ↓
用旧 value 预测、bootstrap mask、γ、λ 计算 GAE 与 return target
        ↓
只在有效动作位置归一化 advantage
        ↓
打乱数据，做 K 个 epoch 的 minibatch PPO 更新
        ↓
监控 KL、clip fraction、value loss、entropy 和梯度
        ↓
在冻结的 hold-out 环境与 verifier 上比较候选策略和旧策略
        ↓
通过门槛才发布；否则回退或调参后重新采样
```

每一轮 rollout 必须和以下 manifest 绑定：

- `policy_version` / checkpoint hash；
- `environment_version` 和工具 schema；
- `reward_version`、奖励分量和权重；
- `reference_policy_version`，若启用 reference KL；
- prompt、workflow、sampling 参数和随机种子；
- tokenizer 版本、action mask 规则和截断规则。

如果这些版本在一个 batch 内混杂，`logp_old`、优势、奖励和重放语义就不再可靠。

## 5. 第一步：冻结 old policy 并采集 rollout

在 iteration 开始时，将当前 Actor 记作旧策略：

$$
\theta_{\mathrm{old}}\leftarrow\theta.
$$

用 $\pi_{\theta_{\mathrm{old}}}$ 与环境交互，对每个被优化的动作至少保存：

```text
state / context
action
reward components
terminated / truncated / bootstrap mask
logp_old = log πold(action | state)
value_old = Vold(state)
action_mask
episode_id / step_id
policy, environment, reward, reference versions
```

`logp_old` 应在 rollout 时保存或由同一个冻结 checkpoint 精确重算。不能在 Actor 已更新后，用新参数冒充旧策略重算它。

### 5.1 Agentic RL 的动作边界

Agent 系统必须先定义什么是动作：

- token；
- 一次完整的结构化工具调用；
- 一次路由、委派、计划或停止决策；
- 一个 workflow node 的输出。

环境返回的搜索结果、数据库行、网页正文、工具错误和其他 observation **不是策略动作**，不应因为被拼进上下文就自动进入 policy loss。若使用 token-level PPO，应给 observation token 设置 `action_mask = 0`。

如果把完整工具调用视作一个动作，其 log-probability 在概率意义上通常是生成 token 的 log-probability 之和：

$$
\log\pi_\theta(a_t\mid s_t)
=
\sum_{j\in\mathrm{action}(t)}\log\pi_\theta(x_j\mid x_{<j},s_t).
$$

将这个和改成 token 平均值会改变优化目标和长度权重；可以作为工程选择，但必须显式记录并做消融。

## 6. 第二步：奖励、终止与 bootstrap

### 6.1 奖励分解

Agentic RL 的训练奖励可写成：

$$
r_t
=
w_{\mathrm{success}}r_t^{\mathrm{success}}
+w_{\mathrm{quality}}r_t^{\mathrm{quality}}
-w_{\mathrm{cost}}c_t
-w_{\mathrm{risk}}p_t.
$$

建议分别保存每个分量，不只保存加权总分。硬安全失败、越权写入或错误数据库更新不应被语言流畅度等软奖励抵消。

### 6.2 `terminated` 与 `truncated` 不能混用

定义 bootstrap mask $b_t$：

- 真正进入吸收终止状态时，$b_t=0$；
- 轨迹只是因采样长度、时间预算或 batch 边界被截断，且仍有合法下一状态时，$b_t=1$，需要用 $V(s_{t+1})$ bootstrap；
- 如果超时本身就是任务失败，按环境定义把它作为终止，并明确失败奖励。

把所有 `done` 都设成不 bootstrap，会系统性低估被时间限制截断的状态价值。

## 7. 第三步：用 GAE 计算优势和回报目标

先用冻结的旧 value 计算 TD residual：

$$
\delta_t
=
r_t+\gamma b_tV_{\phi_{\mathrm{old}}}(s_{t+1})
-V_{\phi_{\mathrm{old}}}(s_t).
$$

GAE 从后向前递推：

$$
\hat A_t
=
\delta_t+\gamma\lambda b_t\hat A_{t+1}.
$$

等价地：

$$
\hat A_t^{\mathrm{GAE}(\gamma,\lambda)}
=
\sum_{l=0}^{T-t-1}
(\gamma\lambda)^l
\left(\prod_{k=0}^{l-1}b_{t+k}\right)
\delta_{t+l},
$$

其中空乘积按 1 处理。若数据已按单条 episode 切开，也可省略显式 mask 乘积，并把求和上限直接截在该 episode 的终点。

value target 通常取：

$$
\hat G_t=\hat A_t+V_{\phi_{\mathrm{old}}}(s_t).
$$

$\lambda$ 控制偏差—方差折中：较小的 $\lambda$ 更依赖 Critic、方差较小但可能偏差更大；$\lambda$ 接近 1 时更接近长程 Monte Carlo return、偏差较小但方差更大。

### 7.1 优势归一化

常见做法是在本轮有效动作上归一化：

$$
\tilde A_t=\frac{\hat A_t-\mu_A}{\sigma_A+\varepsilon_A}.
$$

归一化统计量只应使用 `action_mask = 1` 的位置。分布式训练必须明确统计量是 per-minibatch、per-worker 还是全局 batch；三者会产生不同的优化结果。

### 7.2 三步轨迹的手算例子

设奖励为 $[0,0,1]$，旧价值为 $[0.4,0.5,0.6]$，最后一步真实终止，$\gamma=0.99$、$\lambda=0.95$。则：

$$
\delta_2=1-0.6=0.4,
$$

$$
\delta_1=0+0.99\times0.6-0.5=0.094,
$$

$$
\delta_0=0+0.99\times0.5-0.4=0.095.
$$

令 $\gamma\lambda=0.9405$，从后往前得到：

$$
\hat A_2=0.4,
$$

$$
\hat A_1=0.094+0.9405\times0.4\approx0.4702,
$$

$$
\hat A_0=0.095+0.9405\times0.4702\approx0.5372.
$$

对应 value targets 约为 $[0.9372,0.9702,1.0]$。这个例子适合写成 GAE 单元测试。

## 8. 第四步：PPO clipped surrogate objective

对 rollout 中已采样的动作，定义 importance ratio：

$$
r_t(\theta)
=
\frac{\pi_\theta(a_t\mid s_t)}
{\pi_{\theta_{\mathrm{old}}}(a_t\mid s_t)}
=
\exp\left(
\log\pi_\theta(a_t\mid s_t)-\log\pi_{\theta_{\mathrm{old}}}(a_t\mid s_t)
\right).
$$

PPO-Clip 最大化：

$$
L^{\mathrm{CLIP}}(\theta)
=
\mathbb E_t\left[
\min\left(
r_t(\theta)\hat A_t,
\operatorname{clip}(r_t(\theta),1-\epsilon,1+\epsilon)\hat A_t
\right)
\right].
$$

若训练代码使用梯度下降，则 policy loss 为：

$$
L_{\mathrm{policy}}=-L^{\mathrm{CLIP}}.
$$

### 8.1 正优势时

若 $\hat A_t>0$，提高该动作概率有利。当 $r_t>1+\epsilon$ 时，clipped 分支不再奖励进一步提高概率。

例如 $\hat A_t=2$、$r_t=1.30$、$\epsilon=0.20$：

$$
\min(1.30\times2,1.20\times2)=2.40.
$$

超过 $1.20$ 的部分没有带来更多 surrogate objective 收益。

### 8.2 负优势时

若 $\hat A_t<0$，降低该动作概率有利。当 $r_t<1-\epsilon$ 时，clipped 分支不再奖励进一步降低概率。

例如 $\hat A_t=-2$、$r_t=0.70$、$\epsilon=0.20$：

$$
\min(0.70\times(-2),0.80\times(-2))=-1.60.
$$

在这个区域，目标使用被截断的常数分支，不再鼓励把该动作概率降得更低。

### 8.3 Clipping 不保证什么

Clipping 是 surrogate objective 的保守修正，不是逐样本的硬概率约束，也不提供 TRPO 式的严格单调改进保证。其他样本、共享参数、Critic 误差和多次 optimizer step 仍可能让整体策略发生较大变化。因此必须额外监控 KL，并可设置 early stop。

一个简单的采样近似是：

$$
\widehat D_{\mathrm{KL-old}}
=
\mathbb E_{a_t\sim\pi_{\mathrm{old}}}\left[
\log\pi_{\mathrm{old}}(a_t\mid s_t)-\log\pi_\theta(a_t\mid s_t)
\right].
$$

有限 batch 上这个估计有噪声，甚至可能轻微为负；它应被用于趋势监控和预先定义的 early-stop 规则，而不是被解释为精确距离。

## 9. 第五步：Critic、entropy 与总损失

### 9.1 Critic loss

最基本的 value loss 是：

$$
L_{\mathrm{value}}
=
\frac{1}{2}\mathbb E_t\left[
\left(V_\phi(s_t)-\hat G_t\right)^2
\right].
$$

一些实现还会对 value update 做 clipping：

$$
V_{\mathrm{clip}}(s_t)
=
V_{\phi_{\mathrm{old}}}(s_t)
+\operatorname{clip}\left(
V_\phi(s_t)-V_{\phi_{\mathrm{old}}}(s_t),
-\epsilon_v,
+\epsilon_v
\right),
$$

$$
L_{\mathrm{value}}^{\mathrm{clip}}
=
\frac{1}{2}\mathbb E_t\left[
\max\left(
(V_\phi(s_t)-\hat G_t)^2,
(V_{\mathrm{clip}}(s_t)-\hat G_t)^2
\right)
\right].
$$

Value clipping 是实现变体，不是理解 PPO-Clip policy objective 的前提。启用时应把 $\epsilon_v$ 和旧 value 一起纳入配置与日志。

### 9.2 Entropy bonus

策略熵为：

$$
H(\pi_\theta(\cdot\mid s_t))
=
-\sum_a\pi_\theta(a\mid s_t)\log\pi_\theta(a\mid s_t).
$$

在最小化损失的写法中，entropy bonus 以负号加入，以减缓策略过早变得确定：

$$
L_{\mathrm{PPO}}
=
L_{\mathrm{policy}}
+c_vL_{\mathrm{value}}
-c_H\mathbb E_t[H_t].
$$

### 9.3 RLHF / LLM 的 reference KL

在 LLM 场景中，常加入相对冻结参考模型 $\pi_{\mathrm{ref}}$ 的 KL 控制：

$$
L_{\mathrm{total}}
=
L_{\mathrm{policy}}
+c_vL_{\mathrm{value}}
-c_H\mathbb E_t[H_t]
+\beta_{\mathrm{ref}}L_{\mathrm{KL-ref}}.
$$

另一种实现会把 sample-level KL penalty 放进训练奖励：

$$
r_t^{\mathrm{train}}
=
r_t^{\mathrm{task}}
-\beta_{\mathrm{ref}}
\left(
\log\pi_{\mathrm{old}}(a_t\mid s_t)
-\log\pi_{\mathrm{ref}}(a_t\mid s_t)
\right).
$$

两种路线的梯度语义并不完全相同。实现必须说明 KL 是 reward shaping、显式 loss，还是两者的经过设计的组合；不要在不知情时把同一个约束重复计算两次。

## 10. 完整 PPO 伪代码

```text
input:
    actor πθ
    critic Vφ
    environment E
    reward/verifier R
    optional frozen reference πref
    γ, λ, policy clip ε, value coefficient cv, entropy coefficient cH
    optional reference-KL coefficient βref and target old-policy KL

repeat for iteration = 1, 2, ...:
    # A. Freeze the behavior policy for this iteration
    θold ← copy(θ)
    φold ← copy(φ) or cache all rollout-time values
    freeze policy/environment/reward/reference manifest

    # B. Collect fresh on-policy trajectories
    batch ← []
    while valid_action_count(batch) < rollout_budget:
        reset one or more environments
        while episode not ended and budget remains:
            observe state/context st
            sample action at ~ πθold(. | st)
            execute at in environment
            receive observation, reward components, terminated, truncated
            save:
                st, at, logp_old, value_old,
                reward components, bootstrap mask,
                action mask, episode/step ids, all version ids

    reject batch if versions or masks are inconsistent

    # C. Finalize rewards and compute advantages
    optionally add one clearly specified reference-KL mechanism
    for each trajectory, moving backward:
        δt ← rt + γ * bootstrap_maskt * Vφold(st+1) - Vφold(st)
        Ât ← δt + γ * λ * bootstrap_maskt * Ât+1
        Ĝt ← Ât + Vφold(st)
    normalize Â over valid actor-action positions only

    # D. Reuse only this fresh batch for a bounded number of epochs
    for epoch = 1..K:
        shuffle trajectories or valid action indices
        for minibatch B in batch:
            logp_new ← log πθ(a | s)
            value_new ← Vφ(s)
            entropy ← H(πθ(. | s))

            ratio ← exp(logp_new - logp_old)
            unclipped ← ratio * Â
            clipped ← clip(ratio, 1-ε, 1+ε) * Â

            policy_loss ← -masked_mean(min(unclipped, clipped))
            value_loss ← masked_value_loss(value_new, Ĝ, value_old)
            entropy_bonus ← masked_mean(entropy)
            ref_kl_loss ← configured_reference_kl_if_enabled()

            total_loss ← policy_loss
                         + cv * value_loss
                         - cH * entropy_bonus
                         + βref * ref_kl_loss

            zero gradients
            backpropagate total_loss
            clip gradient norm if configured
            optimizer step for actor and critic

        compute approximate KL(πold || πθ)
        if KL exceeds the predeclared stopping threshold:
            stop further epochs on this batch

    discard the batch for policy optimization

    # E. Evaluate independently
    evaluate candidate and old policy on frozen hold-out tasks
    report task success, reward components, cost, safety, and optimization metrics
    promote only if all predeclared gates pass
```

## 11. 一个接近实现的 minibatch 核心

下面只展示数学核心，不绑定某个深度学习框架：

```python
log_ratio = logp_new - logp_old
ratio = exp(log_ratio)

surrogate_1 = ratio * advantage
surrogate_2 = clip(ratio, 1.0 - clip_eps, 1.0 + clip_eps) * advantage
policy_loss = -masked_mean(minimum(surrogate_1, surrogate_2), action_mask)

value_loss = 0.5 * masked_mean((value_new - return_target) ** 2, value_mask)
entropy_bonus = masked_mean(entropy, action_mask)

loss = policy_loss + value_coef * value_loss - entropy_coef * entropy_bonus
loss = loss + reference_kl_coef * reference_kl_loss  # only if explicitly configured
```

实现时不要让 `logp_old`、`value_old` 或 advantage 参与反向传播；它们是本轮 rollout 的冻结数据。

## 12. LLM 与 Agentic RL 中的张量和 mask

对长度为 $B\times L$ 的 token batch，常见张量为：

| 字段               | 形状示例                      | 用途                                        |
| ------------------ | ----------------------------- | ------------------------------------------- |
| `input_ids`      | $[B,L]$                     | prompt、历史、动作和 observation 的拼接输入 |
| `attention_mask` | $[B,L]$                     | 排除 padding                                |
| `action_mask`    | $[B,L]$                     | 只标记由被训练 Actor 产生的动作 token       |
| `logp_old`       | $[B,L]$                     | 旧策略在有效动作 token 上的 log-probability |
| `logp_ref`       | $[B,L]$                     | 可选参考模型 log-probability                |
| `value_old`      | $[B,L]$ 或 $[B,T_{step}]$ | rollout 时的 Critic 预测                    |
| `rewards`        | $[B,L]$ 或 $[B,T_{step}]$ | token-level 或 step-level 奖励              |
| `advantages`     | 同 policy unit                | GAE 或已对齐到动作单元的优势                |
| `returns`        | 同 value unit                 | Critic target                               |

必须明确 token、step 和 trajectory 三种粒度如何映射：

- 如果 reward 在完整工具执行后才出现，要定义它归属于哪个 decision step；
- 如果一个 step 包含多个生成 token，要定义优势是广播到所有动作 token，还是在 step log-probability 上优化；
- 如果 Critic 每个 token 预测 value，但奖励按 step 给出，要定义中间 token 的 transition 与 mask；
- 如果工具 observation 很长，不要仅因它占用 token 就让它产生 policy gradient。

## 13. 建议记录的训练指标

### 13.1 优化器内部指标

| 指标                     | 解释                                   | 异常信号                                                 |
| ------------------------ | -------------------------------------- | -------------------------------------------------------- |
| `policy_loss`          | clipped policy objective 的负值        | 单独看绝对值意义有限，应结合其他指标                     |
| `value_loss`           | Critic 与 return target 的误差         | 持续增大通常说明奖励尺度、bootstrap 或 Critic 拟合有问题 |
| `entropy`              | 策略随机性                             | 过快下降可能表示策略塌缩                                 |
| `approx_kl_old`        | 新策略相对本轮行为策略的变化           | 突然升高表示更新过猛或 batch/old log-prob 不一致         |
| `reference_kl`         | 策略相对长期参考模型的偏移             | 只在启用 reference policy 时有意义                       |
| `clip_fraction`        | $r_t$ 落在 clip 区间外的有效动作占比 | 很高表示大量样本进入截断区，继续多 epoch 的收益有限      |
| `ratio_mean/quantiles` | 新旧动作概率比的分布                   | 初次 update 前不接近 1 是严重正确性错误                  |
| `adv_mean/std`         | 优势统计                               | 归一化后应符合配置；异常常来自 mask 或 reward            |
| `explained_variance`   | Critic 对 return 方差的解释程度        | 长期接近 0 或为负说明 Critic 没有提供有效 baseline       |
| `gradient_norm`        | 更新尺度                               | 尖峰可能来自极端 reward、ratio 或数值不稳定              |

常用 explained variance 定义为：

$$
1-\frac{\operatorname{Var}(\hat G_t-V_\phi(s_t))}
{\operatorname{Var}(\hat G_t)}.
$$

### 13.2 任务和系统指标

训练 reward 上升不能替代真实评测。至少同时报告：

- hold-out 任务成功率和置信区间；
- 每个 reward component 的分布；
- token 数、工具调用数、墙钟时间和外部 API 成本；
- 无效工具参数、执行异常、超时和重试次数；
- 安全违规、越权写入和不可恢复失败；
- 输出长度、停止原因和 trajectory 长度；
- 与 old policy、SFT/prompt-only baseline 的配对比较。

## 14. 超参数：把它们当作起始点，不是标准答案

| 超参数                   | 常见起始思路                                            | 主要影响                       |
| ------------------------ | ------------------------------------------------------- | ------------------------------ |
| $\gamma$               | 长任务常从$0.99$ 附近开始                             | 对远期奖励的重视程度           |
| $\lambda$              | 常从$0.95$ 附近开始                                   | GAE 偏差—方差折中             |
| policy clip$\epsilon$  | 常从$0.1$–$0.2$ 搜索                               | surrogate objective 的保守程度 |
| PPO epochs$K$          | 小规模控制任务常用数个 epoch；大模型先从更少 epoch 开始 | 样本复用与 policy drift        |
| minibatch size           | 以有效动作数和轨迹多样性定义，不只看序列条数            | 梯度方差与显存                 |
| $c_v$                  | 需结合 reward/value 尺度调节                            | Critic 相对 Actor 的训练强度   |
| $c_H$                  | 从很小值或 0 开始做消融                                 | 探索和策略熵                   |
| target KL                | 应从当前任务的试运行分布确定                            | epoch early stop 与更新稳定性  |
| $\beta_{\mathrm{ref}}$ | LLM 场景按 hold-out 质量与 reference KL 联合调节        | 行为锚定与 reward 优化的权衡   |
| max grad norm            | 作为数值保护，并记录实际触发比例                        | 极端梯度控制                   |

对大模型或长 Agent 轨迹，最有效的“调参”经常不是换一个 $\epsilon$，而是修复动作 mask、奖励尺度、trajectory 切分、旧策略版本或 Critic target。

## 15. 上线前的正确性测试

### 15.1 必须通过的单元测试

1. **Ratio identity**：任何 optimizer step 之前，用同一 checkpoint 重算时，所有有效动作的 $r_t$ 应接近 1。
2. **Clip signs**：分别构造 $\hat A>0$ 和 $\hat A<0$，验证上下界两侧的 objective 与梯度方向。
3. **GAE hand case**：复现第 7.2 节三步轨迹的 $\delta$、$\hat A$ 和 $\hat G$。
4. **Terminal vs truncation**：真终止不 bootstrap，时间截断按环境契约 bootstrap。
5. **Padding/action mask**：改变 padding 或 observation 长度不应改变有效动作 loss。
6. **Stale batch rejection**：policy/environment/reward manifest 不匹配时拒绝训练。
7. **Reward decomposition**：总奖励必须等于带版本权重的分量组合。
8. **Deterministic replay**：固定环境快照和随机种子时，trace 能被重放并得到一致 verifier 结果。

### 15.2 集成测试

- 在极小的确定性环境中确认 PPO 能学到已知最优动作；
- 关闭 policy update 时，rollout 和评测结果不应因 Critic 更新改变；
- 关闭 task reward 时，策略不应凭空提高 hold-out 成功率；
- 交换 reward/verifier 版本时，系统必须产生新的数据 lineage；
- 对 old policy、候选 policy 使用相同任务、预算、工具版本和随机化协议评测。

## 16. 常见失败模式与诊断

| 现象                              | 优先检查                                          | 常见原因                                     |
| --------------------------------- | ------------------------------------------------- | -------------------------------------------- |
| 第一次 update 前 ratio 不接近 1   | checkpoint、tokenizer、sampling mask、logp 实现   | `logp_old` 不是由真实 behavior policy 产生 |
| KL 和 clip fraction 突然升高      | 学习率、epoch 数、优势尺度、重复样本              | 更新过猛或 advantage/outlier 未控制          |
| Reward 上升但 hold-out 成功率下降 | reward components、verifier、数据泄漏             | reward hacking 或训练评测共用奖励器          |
| Value loss 爆炸                   | return 尺度、终止 mask、bootstrap、value clipping | target 错误或 reward 尺度突变                |
| Explained variance 长期为负       | state 信息、Critic 容量、target 对齐              | Critic 比常数 baseline 更差                  |
| Entropy 快速归零                  | entropy 系数、采样温度、奖励稀疏度                | 策略过早塌缩到单一行为                       |
| 长 observation 带来异常大梯度     | action mask                                       | 工具/检索 observation 被误当成动作训练       |
| 多 epoch 后训练突然恶化           | approximate KL、batch 新鲜度                      | 同一 batch 复用过度，已远离 on-policy 条件   |
| 工具调用更多但任务不更好          | cost reward、局部 shaping                         | 策略在投机调用次数或格式分                   |
| 分布式 worker 结果不一致          | policy/reward/env hash、归一化范围                | rollout 版本混杂或统计量未同步               |

## 17. Agentic RL 的具体例子

假设一个数据 Agent 要回答“某个时间区间内哪类皮肤的价值分数增长最快”：

| Step | 状态$s_t$          | 动作$a_t$         | 环境 observation  |                                     奖励示例 |
| ---- | -------------------- | ------------------- | ----------------- | -------------------------------------------: |
| 0    | 用户请求、表结构摘要 | 选择查询计划        | 计划被记录        |                                            0 |
| 1    | 计划、允许的工具列表 | 生成结构化 SQL 调用 | DB 返回结果或错误 |           合法参数$+0.05$，危险写入 $-1$ |
| 2    | 查询结果、预算余量   | 验证时间范围和排序  | verifier 返回断言 |                            断言通过$+0.10$ |
| 3    | 全部证据             | 提交最终答案和证据  | 终局任务 verifier | 正确$+1$，无证据 $-0.3$，超预算 $-0.1$ |

训练时：

- SQL 文本或结构化调用是动作；数据库返回行是 observation；
- 终局 reward 通过 GAE 向早期计划与工具选择传播；
- 局部 verifier 提供低歧义 shaping，但不能掩盖终局失败；
- PPO 只更新 Actor 生成的动作单元；
- hold-out 评测使用冻结数据库快照、schema、预算和独立 verifier。

这体现了 Slide 11 的核心：PPO 并不自动解决 Agent 的动作定义、reward 可信度或信用分配。只有 trace 契约正确，clipped update 才具有明确含义。

## 18. 最小可执行验收清单

一次 PPO 实验在被解释为“策略改进”前，应同时满足：

- [ ] rollout 来自唯一且已记录的 $\pi_{\mathrm{old}}$；
- [ ] policy、environment、tool、reward 和 reference 版本均被冻结并可追踪；
- [ ] observation、padding 和不可训练 token 被 action mask 排除；
- [ ] GAE 对 terminated/truncated 的处理通过手算测试；
- [ ] 首次 update 前 ratio 接近 1；
- [ ] 每轮报告 old-policy KL、reference KL、clip fraction、entropy 和 value explained variance；
- [ ] 同一 batch 的复用 epoch 有上限，并配置 KL early stop；
- [ ] reward components、成本和安全失败独立报告；
- [ ] hold-out 任务不参与 reward/超参数选择；
- [ ] 候选策略在相同环境和预算下优于 old policy 与非 RL baseline；
- [ ] 发布门槛基于任务成功、成本和安全，而不是只基于训练 reward。

## 19. 与 Slide 11 的逐项对应

| Slide 11 元素             | 本文展开位置                                                           |
| ------------------------- | ---------------------------------------------------------------------- |
| `冻结 πold`            | 第 2、5 节：区分 old policy 与 reference policy，冻结 rollout manifest |
| `并发 rollout`          | 第 4、5 节：on-policy 采集、动作边界和 trace 字段                      |
| `verifier / reward`     | 第 6 节：奖励分解、终止和 bootstrap                                    |
| `GAE advantage`         | 第 7 节：TD residual、GAE、return target 和手算例子                    |
| `K 个 minibatch update` | 第 8–11 节：ratio、clip、value、entropy、KL 和伪代码                  |
| `hold-out eval`         | 第 13、15、18 节：独立任务指标、测试和发布门槛                         |
| `实现检查`              | 第 12–16 节：mask、监控、超参数和故障诊断                             |

## 20. 参考资料与来源边界

1. John Schulman et al., [Proximal Policy Optimization Algorithms](https://arxiv.org/abs/1707.06347), 2017。PPO 的原始算法来源。
2. John Schulman et al., [High-Dimensional Continuous Control Using Generalized Advantage Estimation](https://arxiv.org/abs/1506.02438), 2015。GAE 的原始来源。
3. Long Ouyang et al., [Training Language Models to Follow Instructions with Human Feedback](https://arxiv.org/abs/2203.02155), 2022。SFT、reward model 与 PPO 组成的 LLM RLHF 实例。
4. 本仓库 `ppt/Agentic RL全流程技术分析与总结（两万字） - 知乎.pdf` 二节 2.3，以及由其重组生成的 `ppt/agentic-rl-method-and-implementation-roadmap.pptx` 第 11 页。

本文对原始 PPO、LLM/RLHF 扩展和 Agentic RL 工程建议作了明确区分。关于 trace manifest、工具 observation mask、独立 hold-out gate 和诊断指标的内容属于面向本项目的实现建议，不应被误写为 PPO 原论文中的定理或默认配置。
