"""Independent replay for these alpha=0, one-chunk-per-link-per-epoch cases."""

import copy
import json
import math
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from teccl.solvers.allgather import AllGatherFormulation

from teccl.input_data import TopologyParams
from teccl.topologies.closd321 import ClosD321 as ClosD

ROOT = Path(__file__).resolve().parent
EDGES = {(a, b) for i, j in [(g, 6 + group) for group, gpus in enumerate(((0, 1, 2), (3, 4), (5,))) for g in gpus] + [(6, 9), (7, 9), (8, 9)]
         for a, b in [(i, j), (j, i)]}
SEND = re.compile(r"Chunk (\d+) from (\d+) traveled over (\d+)->(\d+) in epoch (\d+)(?: via switches (.*))?")


def replay(data, config):
    params, instance = config['TopologyParams'], config['InstanceParams']
    assert params['alpha'] == [0.0, 0.0]
    assert not instance['switch_copy'] and instance['switch_to_gpu_link_on']
    assert math.isclose(data['1-Epoch_Duration'], params['chunk_size'])
    count = instance['num_chunks']
    horizon = data['3-Epochs_Required']
    events = Counter()
    for line in data['7-Flows']:
        match = SEND.fullmatch(line)
        assert match, line
        c, source, i, j, epoch = map(int, match.groups()[:5])
        assert source in range(6) and i in range(6) and j in range(6) and 0 <= c < count
        path = [i] + ([int(v) for v in match[6].split('->')] if match[6] else []) + [j]
        assert all(v in (6, 7, 8, 9) for v in path[1:-1])
        for offset, (a, b) in enumerate(zip(path, path[1:])):
            assert (a, b) in EDGES and 0 <= epoch + offset < horizon
            events[(epoch + offset, source, c, a, b)] += 1
    available = {(source, c, source) for source in range(6) for c in range(count)}
    arrivals = {}
    prior_switch_in = Counter()
    physical_load = Counter()
    for epoch in range(horizon):
        switch_out, switch_in = Counter(), Counter()
        gpu_arrivals = []
        for (k, source, c, a, b), volume in events.items():
            if k != epoch:
                continue
            assert volume == 1
            physical_load[(a, b, k)] += volume
            assert physical_load[(a, b, k)] <= 1, 'link capacity exceeded'
            if a < 6:
                assert (source, c, a) in available, 'GPU sent before receipt'
            else:
                switch_out[(source, c, a)] += volume
            if b < 6:
                gpu_arrivals.append((source, c, b))
            else:
                switch_in[(source, c, b)] += volume
        assert all(v <= prior_switch_in[key] for key, v in switch_out.items()), 'switch copied or forwarded early'
        for key in gpu_arrivals:
            available.add(key)
            arrivals.setdefault(key, epoch + 1)
        prior_switch_in = switch_in
    required = {(s, c, d) for s in range(6) for d in range(6) if s != d for c in range(count)}
    assert required <= available, 'missing AllGather demand'
    assert max(arrivals[k] for k in required) == horizon
    assert math.isclose(data['4-Collective_Finish_Time'], horizon * params['chunk_size'])
    for s, c, d in required:
        assert math.isclose(data['6-Demand_Met'][f'GPU {d}'][f'GPU {s}'][f'Chunk {c}'], arrivals[(s, c, d)] * params['chunk_size'])
    return {'verified_demands': len(required), 'physical_sends': sum(events.values()), 'epochs': horizon,
            'modeled_finish_time': data['4-Collective_Finish_Time'],
            'output_GB_per_GPU': 6 * count * params['chunk_size'],
            'corrected_algorithm_bandwidth_GBps': 6 * count * params['chunk_size'] / data['4-Collective_Finish_Time']}


def check_flow_cache():
    solver = AllGatherFormulation.__new__(AllGatherFormulation)
    solver.topology = ClosD(TopologyParams(chassis=3))
    solver.nodes = list(range(10))
    solver.epoch_duration = 1.0
    solver.user_input = SimpleNamespace(instance=SimpleNamespace(switch_copy=False, switch_to_gpu_link_on=True, alpha_threshold=0.1))
    incoming = {(0, 1, 6, 0, 0), (0, 2, 6, 0, 0)}
    flows, memo = incoming.copy(), {}
    first = solver.find_flow(flows, 0, 6, 0, 0, memo, (0, 6, 0, 0, 1))
    assert solver.find_flow(flows, 0, 6, 0, 0, memo, (0, 6, 0, 0, 1)) == first
    assert solver.find_flow(flows, 0, 6, 0, 0, memo, (0, 6, 9, 0, 1)) != first
    assert not flows
    solver.user_input.instance.switch_copy = True
    flows, memo = incoming.copy(), {}
    first = solver.find_flow(flows, 0, 6, 0, 0, memo, (0, 6, 0, 0, 1))
    assert solver.find_flow(flows, 0, 6, 0, 0, memo, (0, 6, 9, 0, 1)) == first
    assert flows == incoming


def main():
    check_flow_cache()
    topology = ClosD(TopologyParams(name='ClosD321', chassis=3, chunk_size=1.0))
    assert {(i, j) for i in range(10) for j in range(10) if topology.capacity[i][j]} == EDGES
    assert len(EDGES) == 18 and topology.switch_indices == [6, 7, 8, 9]
    for group in ['chunk_sweep', 'chunk_sweep_warmstart']:
        for path in sorted((ROOT/group/'results').glob('c*.schedule.json')):
            if '.seed.' in path.name:
                continue
            config = json.loads((ROOT/group/'inputs'/path.name.replace('.schedule.json','.json')).read_text())
            data = json.loads(path.read_text())
            replay(data, config)
            broken = copy.deepcopy(data)
            broken['7-Flows'] = []
            try:
                replay(broken, config)
            except AssertionError:
                pass
            else:
                raise AssertionError('incomplete failure schedule accepted')
    print('PASS: failure topology, no-copy cache regression, saved schedules and incomplete-output rejection')


if __name__ == '__main__':
    main()
