# TECCL / MCNF ClosD baselines

[WH1234 三小时最终结果](wh1234_final/comparison.md)：四拓扑各 1/2/3/5/10 块，共20项；16最优、2超时可行上界、2三小时无 incumbent。旧 Mac 六卡结果保留作历史对照。

- [3+3 正常拓扑](closd_3plus3/README.md)：每来源 1/2/3/5/10/15/20 块。
- [3+2+1 坏卡替换拓扑](closd_3plus2plus1/README.md)：每来源 1/2/3/5/10 块。

统一物理报告口径：每卡完整向量 0.25 MB（250000 bytes），AllGather 每来源初始为完整向量的 1/6，每条有向链路 400 Gbps（50 GB/s），每实际活跃物理 epoch 0.1 us；完整 AllGather 时间乘二作为 AllReduce 估算。固定数据总量，仅改变分块数量。原始归一化输入、调度、优化状态、求解日志、MCNF z、回放、物理报告和失败诊断均保留。

MCNF 核心未在此复制或修改。MCNF 来源 revision、配置、节点映射和 SHA256 由各目录 manifest 记录；其公开 API 用于对照求解和原空间验收。报告是模型调度评估，未做硬件实测。
