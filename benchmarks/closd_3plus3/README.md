# TECCL：3+3 两节点 ClosD

本次只将 AllGather 纳入对比；按用户指定，`AllReduce 估算时间 = 2 × AllGather 时间`。没有运行 ReduceScatter，也没有求解 AllReduce。

Fork：<https://github.com/tonyhaohan/TE-CCL-baseline>；上游 `microsoft/TE-CCL`，基于 `c128bf241e5b8bf6ce816b2fe5b8000baacb7c44`。关联 [MCNF issue #277](https://github.com/tonyhaohan/MCNF_tree/issues/277)。适配和结果目前保存在本地分支 `codex/issue277-closd-smoke`，随当前分支版本化。

## 拓扑与比较口径

逐边核对当前 MCNF `create_topology("closd", params)`，配置为 `n_groups=2, gpus_per_node=3, n_planes=1, high_switches_per_plane=1, gpu_low_bandwidth=1, low_high_bandwidth=1`。

- GPU：0–2 与 3–5；LRS：6、7；HRS：8。GPU 连接本地 LRS，两个 LRS 连接 HRS。
- 9 个节点、16 条有向边，各方向独立容量 1，GPU-LRS 与 LRS-HRS 均为 1。
- 本次用 1 GB/s 表示归一化带宽，alpha=0；每 GPU 初始 AllGather 数据 1/6 GB，最终输出含本地数据共 1 GB。时间按此归一化解释，不代表真实硬件测量。
- `switch_copy=false`：交换机不复制数据；GPU 可复制。
- `switch_to_gpu_link_on=true`：显式计入下行链路传输时间。
- AllGather 使用上游 `TOTAL_DEMAND=2` 目标、`ONE_SHOT=1`，求解限制 60 秒、MIP gap=0。默认 `PAPER=3` 目标侧重累计到达进度，不能混淆其目标值与完成时间。

## 结果

| 每来源块数 | 每块 GB | epoch 时长 | epoch 数 | AllGather 模型时间 | AllReduce 二倍估算 | 调度合成用时 s |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1/6 | 1/6 | 8 | 1.333333 | 2.666667 | 0.0693 |
| 2 | 1/12 | 1/12 | 12 | 1.000000 | 2.000000 | 0.6773 |

两个模型均为 Gurobi OPTIMAL。两项输出通过独立物理边回放：GPU 转发因果、交换机上一 epoch 输入、无交换机复制、每边每 epoch 容量，以及 30/60 个接收需求。仅比较了这两种分块，不声称块数 2 是全局最佳粒度。

`summary.json` 是汇总；`manifest.json` 保存源码 SHA、文件哈希、MCNF 配置、逐边拓扑和环境。`results/allgather_validation.json` 保存回放结果。上游输出的 `5-Algo_Bandwidth` 漏乘 `num_chunks`，本次未修改该显示字段，汇总根据实际输出大小重算（分别 0.75、1.0 GB/s）。合成用时为上游 `Solver_Time`，包括模型构建，不是通信时间。

## 输入及运行

在已具备 Gurobi 许可证、gurobipy 和 numpy 的 conda math 环境中，本次用 `python -m pip install --no-deps -e . argcomplete` 安装本地入口；没有安装无关的 seaborn 或旧 Python 的 dataclasses backport。上游通用安装方式为 `pip install .`。

在 fork 根目录运行：

```bash
conda activate math
teccl solve --input_args benchmarks/closd_3plus3/inputs/allgather_c1.json
teccl solve --input_args benchmarks/closd_3plus3/inputs/allgather_c2.json
python benchmarks/closd_3plus3/check_allgather.py
```

JSON 分为三部分：

- `TopologyParams`：拓扑名、chassis、`chunk_size`（GB）和 `alpha`（秒）；本次 ClosD 固定为两组各三卡。
- `GurobiParams`：时间限制（小时）、容差、日志和 MIP gap。
- `InstanceParams`：collective、`num_chunks`、epoch 粒度与数量、目标、交换机语义和输出文件名。

需要手动选择等大 chunk 的数量和大小，满足 `每来源数据量 = num_chunks × chunk_size`。不需要给每个 chunk 手写路由或传输步骤，也不需要输入真实张量：AllGather 需求由程序自动生成，源 s 的块 c 要送到其余所有 GPU。优化器自动决定何时沿哪条边发送；它不会自动搜索最合适的块数。也可以用 `num_epochs=-1` 让上游估计时间范围，本次用显式 horizon 保持小规模和可复现。

## AllReduce 支持与本次决定

公开代码 `Collective` 枚举只有 `ALLGATHER=1` 和 `ALLTOALL=2`，scheduler 也只有对应 formulation。没有 AllReduce、ReduceScatter 或阶段组合的 CLI 入口。[论文 §8](https://cs.stanford.edu/~keithw/sigcomm2024/sigcomm24-final601-acmpaginated.pdf) 讨论通过 AllToAll + AllGather 间接构造 AllReduce；公开实现没有自动完成本地 reduction 与阶段依赖的接口。

本次按用户要求不做 ReduceScatter；AllReduce 列仅为二倍估算。它不提供直接 AllReduce 的最优性证明，也不计 reduction 计算成本。

## 必要的导出修正与保留证据

上游 AllGather 导出器的 `find_flow` 缓存仅包含 `(source, switch, chunk, epoch)`。在无复制模式下，两个不同下游发送可能错误复用同一输入，导致 JSON 重复占用物理边；原优化约束不受影响。修正只为无复制交换机加入下游发送身份作为缓存键，重复追踪同一发送仍复用、不同发送分别消耗输入。回归检查覆盖无复制与复制两种语义，并重放两项完整输出。

`results/*_upstream_export.json` 保留修正前输出，检查脚本确认它们无法通过无复制回放。修正后模型时间不变。求解日志与控制台记录在 `logs/`；日志中的本机目录和许可证标识已脱敏。

在用户缩小范围前，AllToAll 的两次诊断已经结束：`TOTAL_DEMAND` 优化成功但导出断言失败，`PAPER` 默认目标可以导出。保留 `alltoall*` 输入、日志及已有输出作为诊断证据；未独立验证、不纳入对比，后续没有继续运行。未修改 AllToAll 求解或导出代码。


## 默认物理口径与七点分块对比（2026-10-15）

最新结果见 [physical_comparison/comparison.md](physical_comparison/comparison.md)、[HTML](physical_comparison/comparison.html)、[CSV](physical_comparison/comparison.csv) 和 [JSON](physical_comparison/comparison.json)。本节取代上文专项归一化数字作为默认端到端对比入口，原始结果保留。

固定每卡完整向量 0.25 MB（AllGather 每来源初始 0.25/6 MB），每条有向边 400 Gbps，每实际活跃 epoch 加 0.1 us。块数是每来源的块数；每块大小为 250000/(6c) bytes，总量不随 c 改变。1/2/3/5 块原生模型 OPTIMAL；10/15/20 块冷启动 60 秒无 incumbent，带经回放验证的初始解再优化 60 秒，返回可行上界。

本次依据等带宽、等块和无空 epoch 的已验证调度进行物理回放，不重复求解相同整数模型；合成耗时引用此前真实运行。TECCL 的 alpha 是传播延迟，本次每活跃步费在评估层显式添加。MCNF 为 T=12 main-only 调度，不声称针对启动费重新优化。

复现：先按 `run_comparison.py` 完成冷启动与 `--warmstart`（输出目录须为新目录，保留已有证据），再在 conda math 中运行 `evaluate_physical.py`，并将当前 MCNF checkout 的 `src` 加入 PYTHONPATH。完整输入、调度、原空间 z、状态和脱敏日志保存在 `chunk_sweep/`、`chunk_sweep_warmstart/`。所有改动随当前分支版本化。


3+2+1 坏卡五点结果见 [../closd_3plus2plus1/README.md](../closd_3plus2plus1/README.md)。历史 manifest 中 scheduler.py 的 SHA256 对应本目录 `source_snapshot/scheduler.py.gz` 解压后的原始内容；后续仅增加 ClosD321 注册，旧结果保持原样。
