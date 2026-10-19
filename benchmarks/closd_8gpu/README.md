# 八卡 ClosD：WH1234 三小时队列

本轮已全部结束：7 OPTIMAL + 1 TIME_LIMIT（有可行调度）+ 2 TIME_LIMIT（无 incumbent）；四项 MCNF 参照均通过验收。最终四拓扑对比见 [../wh1234_final/comparison.md](../wh1234_final/comparison.md) 和 [HTML](../wh1234_final/comparison.html)。下文保留运行方法及启动时的资源/范围记录。


本轮新增 4+4 和 4+3+1，每来源 1/2/3/5/10 块，共十次 TECCL MILP；另有四项 MCNF AG/AR 参照。使用 WH1234 `math` 环境，Gurobi 12.0.3、四线程、Seed42。每项 `GurobiParams.time_limit=3`（单位小时），实际 Gurobi `TimeLimit=10800` 秒；外层墙钟 11400 秒、RSS 16 GiB，串行执行。两个时间上限分别记录，不再继承旧六卡的 60/150 秒限制。

数据量固定：每卡完整向量/AG 最终输出 250000 bytes，八卡 AG 每来源初始 250000/8 bytes，再分成 c 块，每块 250000/(8c) bytes。每方向物理链路 400 Gbps，每实际活跃 epoch 启动费 0.1 us；物理时间 `5*normalized_finish+0.1*active_epochs` us，完整 AG 时间乘二为 AR 估算。TECCL 的原生 alpha 仍为零传播延迟；启动费在独立回放层添加，与之前结果口径一致。

原始组 [0..3]/[4..7]；坏卡形状删除 GPU7，备用第三 host 的 GPU8 替换，并将活动编号压紧为 0..7。物理组 4+3+1，逻辑组保留 4+4；备用 host 有独立 LRS 和完整上行，带宽不按 host GPU 数降低。原配置、节点映射、逐边/容量等价及源码哈希见 `manifest.json`。拓扑在原生工厂注册为 ClosD44 / ClosD431，原 formulation 和旧六卡结果均保留。

远程目录：`~/mcnf-runs/issue277_teccl_8gpu_3h_20261018/`；tmux 会话：`teccl277_8gpu_3h_20261018`。仅同步本轮所需 teccl 源码、八卡脚本、旧输入模板及固定版本 MCNF src/conf，未同步 .git。远程 `meta/` 保存环境、源码清单、命令和 smoke 失败/重试证据。

在已激活的 conda math 下，将 TECCL 根目录和匹配的 MCNF `src` 加入 PYTHONPATH：

```bash
python benchmarks/closd_8gpu/run.py --selfcheck
python -u benchmarks/closd_8gpu/run.py
python benchmarks/closd_8gpu/report.py
```

正式输出为 `wh1234_3h/`，拒绝覆盖已存在目录。`state.json` 给出当前 case/PID 和两个上限；`results/*effective_params.json` 在 optimize 前写出真实 TimeLimit/Threads/Seed；`summary.json` 逐 case 更新，`results/` 保存原生状态、上下界、模型规模、耗时和独立回放通过的调度，`logs/` 保存完整日志及脱敏 solver.txt。最后自动生成 CSV/Markdown/HTML/图表。单个 case 的 ERROR/TIME_LIMIT/资源失败不会被当作成功，其日志和失败行保留。

MCNF 参照为 HWC / T=12 / main-only，保存 z 并重新载入进行 1e-7 原空间验收；AG 沿用公开 broadcast 支持的 HWC 模式，AR 使用完整 HWC。MCNF 启动费为固定调度事后评估。两个框架的连续分片/固定分块、逻辑路由/物理跳时序差异保留，不称作硬件实测。

归档的控制台日志已移除许可证标识并将远程用户目录写为 `~`；环境中的解释器写为 `$CONDA_PREFIX/bin/python`，未脱敏原件保留在服务器。
