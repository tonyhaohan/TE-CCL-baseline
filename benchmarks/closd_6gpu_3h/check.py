"""Reuse independently checked six-GPU replay; count active physical epochs explicitly."""

import importlib.util
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CHECKS={}
for name,folder in [('ClosD','closd_3plus3'),('ClosD321','closd_3plus2plus1')]:
    spec=importlib.util.spec_from_file_location(f'replay_{name}',ROOT.parent/folder/'check_allgather.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    CHECKS[name]=module


def replay(data,config):
    check=CHECKS[config['TopologyParams']['name']]
    validated=check.replay(data,config)
    active=set()
    for line in data['7-Flows']:
        match=check.SEND.fullmatch(line)
        start=int(match[5])
        hops=1+(len(match[6].split('->')) if match[6] else 0)
        active.update(range(start,start+hops))
    assert active and min(active)>=0 and max(active)<data['3-Epochs_Required']
    return {**validated,'active_epochs':len(active)}


def selfcheck():
    for check in CHECKS.values():check.check_flow_cache()
    for name,check in CHECKS.items():
        assert len(check.EDGES)==(16 if name=='ClosD' else 18)
    assert len(CHECKS)==2


if __name__=='__main__':
    selfcheck()
    print('PASS: six-GPU replay owners and no-copy cache regression')
