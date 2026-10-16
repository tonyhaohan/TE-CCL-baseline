"""ClosD with one failed GPU replaced on a third physical host (3+2+1)."""

import math

from teccl.input_data import TopologyParams
from teccl.topologies.topology import Topology


class ClosD321(Topology):
    def __init__(self, topo_input: TopologyParams):
        if topo_input.chassis != 3:
            raise ValueError('ClosD321 requires three physical hosts (3+2+1 GPUs)')
        if not math.isfinite(topo_input.chunk_size) or topo_input.chunk_size <= 0:
            raise ValueError('chunk_size must be finite and positive')
        if len(topo_input.alpha) != 2 or any(not math.isfinite(a) or a < 0 for a in topo_input.alpha):
            raise ValueError('alpha must contain two finite nonnegative delays')
        super().__init__(topo_input)
        self.node_per_chassis = 3

    def construct_topology(self, topo_input: TopologyParams):
        self.capacity = [[0.0] * 10 for _ in range(10)]
        self.alpha = [[-1.0] * 10 for _ in range(10)]
        groups = ((0, 1, 2), (3, 4), (5,))
        links = [(gpu, 6 + group) for group, gpus in enumerate(groups) for gpu in gpus]
        links += [(6, 9), (7, 9), (8, 9)]
        for i, j in links:
            for src, dst in ((i, j), (j, i)):
                self.capacity[src][dst] = 1.0 / self.chunk_size
                self.alpha[src][dst] = topo_input.alpha[int(i >= 6)]

    def set_switch_indicies(self):
        self.switch_indices = [6, 7, 8, 9]
