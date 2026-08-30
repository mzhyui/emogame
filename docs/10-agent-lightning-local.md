# 10 — Agent Lightning 本地 Agentic RL 教程

## 目的与选择

本仓库以 Git submodule 固定了 [Microsoft Agent Lightning](https://github.com/microsoft/agent-lightning)：

```text
路径:       agent-lightning/
上游:       https://github.com/microsoft/agent-lightning.git
固定提交:   e43cbf289e92385e0589e4113fdcfdcb822aebb9
上游版本:   1.0.1
许可证:     MIT
```

选择日期为 2026-08-26。该项目当日约有 17.8k GitHub stars，提供把现有 Agent 接入 RL 的训练/服务分离架构，并把 Calc-X 列为本地控制器的一卡 POC。作为对照，OpenRLHF 当日约有 10.0k stars，但其快速训练示例以 8×A100 为起点。因此这里选择 Agent Lightning 作为“社区采用度高且最容易先跑通服务层”的 Agentic-RL 演示；这不是对所有 GitHub 项目作绝对排名。

官方资料：

- [上游 README](https://github.com/microsoft/agent-lightning)
- [安装说明](https://microsoft.github.io/agent-lightning/stable/00-installation/)
- [Calc-X 一卡示例](https://microsoft.github.io/agent-lightning/stable/50-example-calc-x/)

## 获取与提交约束

首次获取本仓库后，初始化所有子模块：

```bash
git submodule update --init --recursive
git submodule status agent-lightning
```

期望第二条命令输出的提交以 `e43cbf2` 开头。不要在未确认兼容性的情况下在 `agent-lightning/` 内切换分支或执行 `git pull`；父仓库记录的是上述可复现快照。

更新上游时，应在子模块内检查 release notes、运行本教程的验证项，再由父仓库只提交新的 gitlink，而不是复制上游源码。

## 本机事实与支持边界

2026-08-26 的实际探测结果如下。它们是本机快照，而不是上游的通用系统要求。

| 项目 | 实际结果 | 含义 |
|---|---|---|
| GPU | NVIDIA GeForce RTX 3080，20 GiB VRAM | 主机驱动可见，适合服务层与小规模试验 |
| NVIDIA 驱动 | 580.173.02 | 足以将 GPU 交给较新的 CUDA 容器 |
| Python | 3.12.3 | 满足上游 `>=3.12` 要求 |
| 主机 CUDA Toolkit | 12.0 | **不满足**上游 v1.0 的 CUDA 12.9/13.0 + FlashAttention 构建配方 |
| 内存 / 可用磁盘 | 31 GiB / 237 GiB | 容器和权重仍需预留空间 |
| Docker | 29.6.1，已注册 `nvidia` runtime | 可作为升级 CUDA 的本地路径；尚未把完整训练跑通 |

上游的 Calc-X 文档虽称“只需一张 GPU”，其配置表实际标的是 **1× A100 80GB** 和 `Qwen/Qwen2.5-1.5B-Instruct`。因此不要把“一张 GPU”误解为“任意 20 GiB 显卡都已支持完整 GRPO 训练”。本机已验证基础服务层和非 GPU 单元测试；没有宣称已经完成官方完整训练。

在主机或容器中，使用下面的命令确认 GPU 是否真正暴露给运行环境：

```bash
nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.free --format=csv,noheader
docker run --rm --gpus all nvidia/cuda:12.9.0-base-ubuntu24.04 \
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.free --format=csv,noheader
```

第二条命令会拉取公开 CUDA 基础镜像；只有它也成功列出 RTX 3080 后，才证明容器路径获得了 GPU。不要把 Docker runtime 已注册当作等价证据。

## 已验证的轻量本地部署

这一节不下载模型、不安装 `verl`，因此适合确认 Agent Lightning 的服务 API、包安装和控制器代码可在本机执行。项目指南优先使用 `uv`；本机没有 `uv` 时，以下 Python venv 等价地完成其基础依赖安装。

为避免继承环境中的 SOCKS 代理而使 `httpx` 报 `Missing dependencies for SOCKS support`，下载依赖和启动本地服务时显式清掉代理变量。包从清华 PyPI 镜像下载。

```bash
cd agent-lightning
python3 -m venv .venv
source .venv/bin/activate

env -u http_proxy -u https_proxy -u all_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    python -m pip install --upgrade pip \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-input

env -u http_proxy -u https_proxy -u all_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    python -m pip install -e . \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-input
```

启动服务并检查健康端点：

```bash
env -u http_proxy -u https_proxy -u all_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    .venv/bin/agl-server host=127.0.0.1 port=8182 key=local-dev

curl --noproxy '*' http://127.0.0.1:8182/healthz
# 期望: {"status":"ok"}
```

上述服务运行在前台；用一次 `Ctrl+C` 正常停止它。它不会下载或加载默认的 Qwen 模型，也不会开始训练。

运行不要求 GPU、模型、Ray 集群或 Kubernetes 的上游验证：

```bash
env -u http_proxy -u https_proxy -u all_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    .venv/bin/python -m pip install pytest \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple --no-input

env -u http_proxy -u https_proxy -u all_proxy \
    -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
    .venv/bin/python -m pytest \
    tests/test_package.py \
    tests/server/test_endpoints.py \
    tests/controller/test_k8s_reconciler.py -q
```

本机在固定提交上得到 `18 passed, 1 warning`。该 warning 来自 FastAPI/Starlette 对 `httpx` 测试客户端的弃用提示，不是测试失败。

## 运行官方 Calc-X 配方

只有在准备好 **CUDA 12.9 或 13.0** 的训练环境、可用的模型访问权限和足够显存后，才进行这一节。官方推荐通过 `uv` 和上游脚本安装相互锁定的 `verl`、vLLM、PyTorch、Ray 与 FlashAttention 版本；不要在上述轻量 venv 里随意追加这些包。

```bash
cd agent-lightning
# 安装 uv 后执行；使用团队批准的国内安装源或官方安装方式。
uv sync
source .venv/bin/activate
bash scripts/setup_verl.sh 0.8.0 cu130

uv pip install openai httpx sympy \
  "autogen-agentchat" "autogen-ext[openai]" \
  "mcp>=1.11.0,<2" mcp-server-calculator
uv run wandb login
```

从 [官方 Calc-X 数据集链接](https://drive.google.com/file/d/1FQMyKLLd6hP9dw9rfZn1EZOWNvKaDsqw/view?usp=sharing) 下载并解压后，目录必须为：

```text
examples/calc_x/data/train.parquet
examples/calc_x/data/test.parquet
examples/calc_x/data/test_mini.parquet
examples/calc_x/data/sample.jsonl
```

随后从子模块根目录启动官方本地控制器流程：

```bash
examples/calc_x/run_local.sh
```

这个脚本会启动 Ray、`agl-server`（8181）、本地 `agl-controller` 和训练入口。日志写入 `/tmp/`；停止时只按一次 `Ctrl+C` 并等待脚本清理。训练、模型权重、数据集和 W&B 凭据都是本地运行产物，不能提交到此仓库。

### RTX 3080 的探索性降配试跑

以下命令只是用于定位显存下限，不是经过上游支持或本机验证的“完整训练配置”。它把模型、序列长度、组内采样数和批量缩小；仍可能因 vLLM、FSDP 或 GRPO 显存不足失败。仅在上一节的 GPU 栈和 Calc-X 数据均已准备完成后运行：

```bash
cd agent-lightning/examples/calc_x
bash run_local.sh \
  --model Qwen/Qwen2.5-0.5B-Instruct \
  data.train_batch_size=4 \
  data.max_prompt_length=1024 \
  data.max_response_length=512 \
  actor_rollout_ref.rollout.n=2 \
  actor_rollout_ref.rollout.gpu_memory_utilization=0.35 \
  actor_rollout_ref.rollout.log_prob_micro_batch_size_per_gpu=1 \
  actor_rollout_ref.actor.ppo_micro_batch_size_per_gpu=1 \
  actor_rollout_ref.ref.log_prob_micro_batch_size_per_gpu=1 \
  trainer.total_epochs=1
```

用另一个终端监视真实峰值，而不是只看开始时的空闲显存：

```bash
nvidia-smi --query-gpu=memory.used,memory.free,utilization.gpu \
  --format=csv,noheader -l 2
```

若出现 OOM，先减少 `max_response_length`，再减少 `n` 与 `train_batch_size`；不要同时声称该小模型试跑与官方 A100 结果可比较。

## 容器化 CUDA 路径

由于本机 Toolkit 是 CUDA 12.0，容器是避免直接重装系统 CUDA 的首选路径。使用团队认可的 CUDA 13 开发镜像并挂载子模块，例如：

```bash
cd agent-lightning
docker run --rm --gpus all --ipc=host -it \
  -v "$PWD":/workspace/agent-lightning \
  -w /workspace/agent-lightning \
  nvidia/cuda:13.0.0-devel-ubuntu24.04 bash
```

进入容器后，先运行上一节的 `nvidia-smi` 容器验证，再按官方安装步骤安装 Python 3.12、`uv` 和 `scripts/setup_verl.sh 0.8.0 cu130`。镜像标签、PyTorch/verl 版本和模型哈希应写入实验日志。当前机器已确认 Docker 的 `nvidia` runtime 已注册；CUDA 12.9 镜像尚未在本次验收中完整拉取成功，所以不能将容器内训练标记为已验证。

## 验收清单与已知状态

- [x] 上游以 Git submodule 纳入，父仓库固定到 `e43cbf2`。
- [x] RTX 3080 在主机的非沙盒 GPU 查询中可见。
- [x] Python 3.12 隔离环境中的 Agent Lightning 基础服务健康检查通过。
- [x] 上游包、服务 API 和控制器测试：18 项通过。
- [x] Docker 和 `nvidia` runtime 被探测到。
- [ ] 完整 Calc-X/VERL 训练：未运行；官方标称 A100 80GB 且主机 CUDA 12.0 不匹配。
- [ ] 容器内 GPU 探测：需要先成功拉取 CUDA 镜像后执行。

该清单刻意把“服务层已验证”和“训练效果已验证”分开：前者可以作为本地集成起点，后者必须以真实训练日志、显存曲线、固定数据/模型版本和可复现评估结果为准。

## 下一步

完成 CUDA 13 容器内 GPU 探测后，先用小数据和上述探索性配置验证一轮完整生命周期，再决定是否需要 80GB 级显卡运行官方 Calc-X 配方。需要把情绪价值评估 Agent 接入训练时，先定义可审计的奖励函数、离线评测集与停止条件，再替换 Calc-X 的 Agent 和数据；不要仅以一次训练曲线作为效果证据。
