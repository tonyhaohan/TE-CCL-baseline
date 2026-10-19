# 六卡 ClosD：WH1234 三小时重跑

本轮已全部结束：9 OPTIMAL + 1 TIME_LIMIT（有可行调度）；四项 MCNF 参照均通过验收。最终四拓扑对比见 [../wh1234_final/comparison.md](../wh1234_final/comparison.md) 和 [HTML](../wh1234_final/comparison.html)。下文保留运行方法及启动时的资源/范围记录。


本轮将原六卡矩阵改为每项三小时重新求解，不覆盖旧 60 秒结果：3+3 与 3+2+1 的每来源块数均为 1/2/3/5/10，共 10 项 TECCL MILP，另跑四项同机 MCNF AG/AR 参照。`run.py` 保留独立源码快照，以已验证的八卡三小时入口为基础改为六卡输入；正在运行的八卡队列源码与任务不变。

每项 Gurobi TimeLimit=10800 秒，外层墙钟 11400 秒、RSS 16 GiB、Threads=4、Seed42；单队列内部串行。WH1234 当前资源允许本队列与八卡队列并行运行，两队列均使用 CPU，不占用 GPU。计算耗时会反映这次并行运行时的机器负载。

完整向量/AG 最终输出固定为 250000 bytes，六卡 AG 每来源初始为其 1/6，再拆 c 块，每块 250000/(6c) bytes。每方向物理链路 400 Gbps，每实际活跃 epoch 加 0.1 us；完整 AG 端到端时间乘二作为 AR 估算。使用原生 ClosD / ClosD321，原来的 GPU5→备用 GPU6 映射与逻辑分组保留。拓扑、容量、源码 SHA256 见 manifest。

远程目录 `~/mcnf-runs/issue277_teccl_6gpu_3h_20261018/`；tmux 会话 `teccl277_6gpu_3h_20261018`。目录内 TE-CCL 和 MCNF_tree 分开、未同步 .git；meta 保存环境、源码清单、启动命令与 smoke 证据。状态与逐项汇总在 `wh1234_3h/state.json`、`summary.json`；每项原生 status、incumbent、bound/gap、耗时、内存、实际有效参数、调度和日志保留在 inputs/results/logs 中。MCNF z 保存在服务器并重新载入做 1e-7 原空间验收。

激活 math 并将 TECCL 根和 MCNF src 加入 PYTHONPATH 后，在 fork 根目录运行：

```bash
python benchmarks/closd_6gpu_3h/run.py --selfcheck
python -u benchmarks/closd_6gpu_3h/run.py
python benchmarks/closd_6gpu_3h/report.py
```

输出目录拒绝覆盖。队列结束后自动生成 CSV/Markdown/HTML/PNG；运行中 report.py 只对已结束行生成部分结果，不补零或把超时称为最优。`check.py` 复用先前独立验收的六卡回放，并根据实际物理发送计活跃步；模型约束不变，MCNF 为 T=12 main-only，启动费为固定调度事后评估，未做硬件执行。

用户已将本轮六卡范围限制到 10 块。运行中的旧控制器内存列表保留 16 项（含 4 LP）；其中待执行的 15/20 块 worker 只写 `QUEUE / SKIPPED_SCOPE` 记录并退出，不创建或求解 MILP。报告只统计 14 项有效计算，原始状态及取消记录保留。旧控制器和当前 worker 不重启；修改前源码及清单留在远程 meta，范围变更及新源码哈希见 scope_change.json。

归档的控制台日志已移除许可证标识并将远程用户目录写为 `~`；环境中的解释器写为 `$CONDA_PREFIX/bin/python`，未脱敏原件保留在服务器。
