"""Eight active GPUs on 4+4 or failure-replacement 4+3+1 physical hosts."""

import math
from teccl.topologies.topology import Topology


class ClosD8(Topology):
    def __init__(self, params):
        layouts = {'ClosD44': ((0, 1, 2, 3), (4, 5, 6, 7)),
                   'ClosD431': ((0, 1, 2, 3), (4, 5, 6), (7,))}
        if params.name not in layouts:
            raise ValueError('expected ClosD44 or ClosD431')
        self.groups = layouts[params.name]
        if params.chassis != len(self.groups):
            raise ValueError('chassis must match the physical host count')
        if not math.isfinite(params.chunk_size) or params.chunk_size <= 0:
            raise ValueError('chunk_size must be finite and positive')
        if len(params.alpha) != 2 or any(not math.isfinite(a) or a < 0 for a in params.alpha):
            raise ValueError('alpha must contain two finite nonnegative delays')
        super().__init__(params)
        self.node_per_chassis = 4

    def construct_topology(self, params):
        high = 8 + len(self.groups)
        self.capacity = [[0.] * (high+1) for _ in range(high+1)]
        self.alpha = [[-1.] * (high+1) for _ in range(high+1)]
        links = [(g, 8+group) for group, gpus in enumerate(self.groups) for g in gpus]
        links += [(8+group, high) for group in range(len(self.groups))]
        for a, b in links:
            for i, j in ((a, b), (b, a)):
                self.capacity[i][j] = 1 / params.chunk_size
                self.alpha[i][j] = params.alpha[int(a >= 8)]

    def set_switch_indicies(self):
        self.switch_indices = list(range(8, len(self.capacity)))
