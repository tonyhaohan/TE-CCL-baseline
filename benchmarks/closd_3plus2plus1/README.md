# TECCL：3+2+1 坏卡 ClosD

沿用 [MCNF issue #277](https://github.com/tonyhaohan/MCNF_tree/issues/277)。完整结果见 [Markdown](physical_comparison/comparison.md)、[HTML](physical_comparison/comparison.html)、[CSV](physical_comparison/comparison.csv)、[JSON](physical_comparison/comparison.json)。

原 GPU5 失效，备用物理 host 的 GPU6 替换。原 MCNF 物理组 `[0,1,2]/[3,4]/[6]`，逻辑组 `[0,1,2]/[3,4,6]`；活动编号压紧后，备用 GPU6 映射为 GPU5，三个 LRS=6/7/8，HRS=9。六个活动 GPU、10 节点、18 条有向边；备用 host 有完整上行，所有容量为 1。生成器规则与节点映射、原始/压紧配置及逐边等价证据见 `manifest.json`。失败原槽位不承担需求或流量。

固定完整向量 0.25 MB，AG 每来源初始 0.25/6 MB，每来源 c 块，各块为 250000/(6c) bytes；每边 400 Gbps，每实际活跃物理 epoch 加 0.1 us。求解使用上游 TOTAL_DEMAND / ONE_SHOT、无交换机复制且下行计费、H=6c+6、四线程、Seed42、每项优化上限 60 秒，进程 RSS 上限 6 GiB、墙钟上限 150 秒。新拓扑在 TECCL 原生 scheduler 工厂注册为 `ClosD321`，未修改 MILP 数学约束。

1/2/3 块 OPTIMAL；5 块 TIME_LIMIT 且有可行解；10 块冷启动 TIME_LIMIT 无解，使用已回放通过的 5 块调度拼接作初始解，再优化 60 秒得到可行上界，未改善种子且未证明最优。完整失败、冷启动、重试证据分别保留在 `chunk_sweep/`、`chunk_sweep_warmstart/`。求解日志脱敏副本为 `logs/*.solver.txt`，MCNF 保存的 z 在 `mcnf_artifacts/`。

TECCL 当前最好为 2 块，AG=6.7167 us、AR 二倍估算=13.4333 us。MCNF 相同物理/逻辑分组、HWC、T=12 main-only，AG=5.6792 us、直接 AR=9.5333 us；原空间重新验收最大违反量约 1.03e-12。MCNF 是连续分片和可变步长，TECCL 是固定块逐物理跳存储转发，报告保留时序模型差异；未对启动费重新优化 MCNF，未做硬件实测或 ReduceScatter。

## 复现与验证

需要 conda math、TECCL 安装依赖、Gurobi 许可证、psutil、matplotlib、numpy，以及 manifest 指定 revision 的 MCNF checkout。将 MCNF 的 `src` 加入 PYTHONPATH，在本 fork 根目录运行：

```bash
conda run --no-capture-output -n math python benchmarks/closd_3plus2plus1/check_allgather.py
conda run --no-capture-output -n math python benchmarks/closd_3plus2plus1/evaluate_physical.py
```

`run_comparison.py` 是新求解入口，`--warmstart` 单独重试冷启动未得到解的 case。runner 拒绝覆盖已有输出目录；需要重跑时应先将已归档的 `chunk_sweep/`、`chunk_sweep_warmstart/` 移到另外位置保留。`evaluate_physical.py` 对保存的五项调度逐边回放、对两个 MCNF z 重新验收，再写报告；`check_allgather.py` 还验证无复制缓存和拒绝不完整调度。物理时间为 `epochs*5/(6c)+0.1*active_epochs` us，AR 按完整 AG 时间二倍估算。合成耗时为原生归一化整数模型的真实运行，不把物理回放当作新求解测速。
