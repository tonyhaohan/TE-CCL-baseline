"""Independent eight-GPU alpha=0 replay and topology/input self-checks."""

import json
import math
import re
from collections import Counter
from pathlib import Path
from teccl.input_data import TopologyParams
from teccl.topologies.closd8 import ClosD8

SEND = re.compile(r"Chunk (\d+) from (\d+) traveled over (\d+)->(\d+) in epoch (\d+)(?: via switches (.*))?")

def replay(data, config):
    shape = config['TopologyParams']['name']
    assert shape in ('ClosD44', 'ClosD431')
    groups = ((0,1,2,3),(4,5,6,7)) if shape == 'ClosD44' else ((0,1,2,3),(4,5,6),(7,))
    high = 8 + len(groups)
    switches = set(range(8,high+1))
    links = [(g,8+group) for group,gpus in enumerate(groups) for g in gpus] + [(8+group,high) for group in range(len(groups))]
    edges = {(a,b) for i,j in links for a,b in ((i,j),(j,i))}
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
        assert source in range(8) and i in range(8) and j in range(8) and 0 <= c < count
        path = [i] + ([int(v) for v in match[6].split('->')] if match[6] else []) + [j]
        assert all(v in switches for v in path[1:-1])
        for offset, (a, b) in enumerate(zip(path, path[1:])):
            assert (a, b) in edges and 0 <= epoch + offset < horizon
            events[(epoch + offset, source, c, a, b)] += 1
    available = {(source, c, source) for source in range(8) for c in range(count)}
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
            if a < 8:
                assert (source, c, a) in available, 'GPU sent before receipt'
            else:
                switch_out[(source, c, a)] += volume
            if b < 8:
                gpu_arrivals.append((source, c, b))
            else:
                switch_in[(source, c, b)] += volume
        assert all(v <= prior_switch_in[key] for key, v in switch_out.items()), 'switch copied or forwarded early'
        for key in gpu_arrivals:
            available.add(key)
            arrivals.setdefault(key, epoch + 1)
        prior_switch_in = switch_in
    required = {(s, c, d) for s in range(8) for d in range(8) if s != d for c in range(count)}
    assert required <= available, 'missing AllGather demand'
    assert max(arrivals[k] for k in required) == horizon
    assert math.isclose(data['4-Collective_Finish_Time'], horizon * params['chunk_size'])
    for s, c, d in required:
        assert math.isclose(data['6-Demand_Met'][f'GPU {d}'][f'GPU {s}'][f'Chunk {c}'], arrivals[(s, c, d)] * params['chunk_size'])
    active = {event[0] for event in events}
    assert active == set(range(horizon)), 'unexpected idle epoch'
    return {'active_epochs': len(active), 'verified_demands': len(required), 'physical_sends': sum(events.values()), 'epochs': horizon,
            'modeled_finish_time': data['4-Collective_Finish_Time'],
            'output_GB_per_GPU': 8 * count * params['chunk_size'],
            'corrected_algorithm_bandwidth_GBps': 8 * count * params['chunk_size'] / data['4-Collective_Finish_Time']}



def selfcheck():
    for name,hosts,edges in [('ClosD44',2,20),('ClosD431',3,22)]:
        topo=ClosD8(TopologyParams(name=name,chassis=hosts,chunk_size=1.))
        assert sum(v>0 for row in topo.capacity for v in row)==edges
        assert len(topo.capacity)==8+hosts+1
        assert all(v in (0.,1.) for row in topo.capacity for v in row)
    for params in [TopologyParams(name='ClosD44',chassis=3),
                   TopologyParams(name='ClosD44',chassis=2,chunk_size=0.),
                   TopologyParams(name='ClosD431',chassis=3,alpha=(-1.,0.))]:
        try:
            ClosD8(params)
        except ValueError:
            pass
        else:
            raise AssertionError('invalid topology accepted')
    # A complete broadcast through the same switch input twice would violate no-copy.
    config={'TopologyParams':{'name':'ClosD44','alpha':[0.,0.],'chunk_size':.125},
            'InstanceParams':{'num_chunks':1,'switch_copy':False,'switch_to_gpu_link_on':True}}
    broken={'1-Epoch_Duration':.125,'3-Epochs_Required':2,'4-Collective_Finish_Time':.25,
            '7-Flows':['Chunk 0 from 0 traveled over 0->1 in epoch 0 via switches 8',
                       'Chunk 0 from 0 traveled over 0->2 in epoch 0 via switches 8']}
    try:
        replay(broken,config)
    except AssertionError:
        pass
    else:
        raise AssertionError('duplicated physical input accepted')


if __name__=='__main__':
    selfcheck()
    print('PASS: both topologies, invalid-input and corrupt-schedule rejection')
