"""Fixed MCNF ClosD smoke topology: two groups of three GPUs, one plane."""

import math

from teccl.input_data import TopologyParams
from teccl.topologies.topology import Topology


class ClosD(Topology):
    def __init__(self, topo_input: TopologyParams):
        if topo_input.chassis != 2:
            raise ValueError("ClosD smoke topology requires chassis=2 (3+3 GPUs)")
        if not math.isfinite(topo_input.chunk_size) or topo_input.chunk_size <= 0:
            raise ValueError("chunk_size must be finite and positive")
        if len(topo_input.alpha) != 2 or any(not math.isfinite(a) or a < 0 for a in topo_input.alpha):
            raise ValueError("alpha must contain two finite nonnegative delays")
        super().__init__(topo_input)
        self.node_per_chassis = 3

    def construct_topology(self, topo_input: TopologyParams):
        self.capacity = [[0.0] * 9 for _ in range(9)]
        self.alpha = [[-1.0] * 9 for _ in range(9)]
        links = [(gpu, 6 + gpu // 3) for gpu in range(6)] + [(6, 8), (7, 8)]
        for i, j in links:
            for src, dst in ((i, j), (j, i)):
                self.capacity[src][dst] = 1.0 / self.chunk_size
                self.alpha[src][dst] = topo_input.alpha[int(i >= 6)]

    def set_switch_indicies(self):
        self.switch_indices = [6, 7, 8]
