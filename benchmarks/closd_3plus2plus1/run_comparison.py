"""Issue277: isolated 3+2+1 failure sweep; reuse the measured TECCL worker."""

import argparse
import gzip
import importlib.util
import json
import math
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

import psutil
from check_allgather import SEND, replay

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
COUNTS = (1, 2, 3, 5, 10)
OUT = ROOT/'chunk_sweep'
spec = importlib.util.spec_from_file_location('balanced_runner', ROOT.parent/'closd_3plus3/run_comparison.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
base.ROOT = ROOT
base.OUT = OUT
original_config_for = base.config_for
save = base.save


def config_for(chunks):
    config = original_config_for(chunks)
    config['TopologyParams'].update(name='ClosD321', chassis=3)
    config['GurobiParams']['log_file'] = f'_failure321_c{chunks}'
    return config


def warmstart_for(chunks, config):
    choices = []
    for c in COUNTS:
        path = ROOT/'chunk_sweep/results'/f'c{c}.metrics.json'
        if c < chunks and chunks % c == 0 and path.exists() and json.loads(path.read_text()).get('validated'):
            choices.append(c)
    if not choices:
        raise ValueError('no validated smaller divisor available for MIP start')
    source_chunks = max(choices)
    seed = json.loads((ROOT/'chunk_sweep/results'/f'c{source_chunks}.schedule.json').read_text())
    block_epochs = seed['3-Epochs_Required']
    horizon = block_epochs * (chunks//source_chunks)
    config['InstanceParams']['num_epochs'] = max(config['InstanceParams']['num_epochs'], horizon+6)
    duration = config['TopologyParams']['chunk_size']
    lines, events = [], set()
    arrivals = {(s,c,s):0 for s in range(6) for c in range(chunks)}
    for block in range(chunks//source_chunks):
        for line in seed['7-Flows']:
            m=SEND.fullmatch(line)
            c,s,i,j,k=map(int,m.groups()[:5])
            c += source_chunks*block
            k += block_epochs*block
            path=[i]+[int(v) for v in m[6].split('->')]+[j]
            lines.append(f'Chunk {c} from {s} traveled over {i}->{j} in epoch {k} via switches {m[6]}')
            events.update((s,a,b,c,k+offset) for offset,(a,b) in enumerate(zip(path,path[1:])))
            key=(s,c,j)
            arrivals[key]=min(arrivals.get(key,float('inf')), k+len(path)-1)
    schedule={'1-Epoch_Duration':duration,'3-Epochs_Required':horizon,
              '4-Collective_Finish_Time':horizon*duration,'7-Flows':lines,
              '6-Demand_Met':{f'GPU {d}':{f'GPU {s}':{f'Chunk {c}':arrivals[(s,c,d)]*duration
                  for c in range(chunks)} for s in range(6) if s!=d} for d in range(6)}}
    validation=replay(schedule,config)
    validation['seed_chunks_per_source']=source_chunks
    save(OUT/'results'/f'c{chunks}.seed.schedule.json',schedule)
    path=OUT/'inputs'/f'c{chunks}.mst.gz'
    path.parent.mkdir(exist_ok=True,parents=True)
    with gzip.open(path,'wt') as stream:
        for s,a,b,c,k in sorted(events):
            stream.write(f'flow_{s}_{a}_{b}_{c}_{k} 1\n')
        for s in range(10):
            for d in range(10):
                for c in range(chunks):
                    arrival=arrivals.get((s,c,d),float('inf'))
                    for k in range(config['InstanceParams']['num_epochs']):
                        stream.write(f'buffer_{s}_{d}_{c}_{k} {int(arrival<=k)}\n')
                        if s<6 and d<6 and s!=d:
                            stream.write(f'total_demand_{s}_{d}_{c}_{k} {int(arrival<=k+1)}\n')
    return str(path.relative_to(REPO)),validation


def mcnf_case(kind):
    from mcnf_tree.config import run_experiment_dict
    from mcnf_tree.artifacts import load_artifact_result
    old=ROOT.parent/'closd_3plus3/chunk_sweep/inputs'/f'mcnf_{kind}.json'
    config=json.loads(old.read_text())
    config['network']=json.loads((ROOT/'manifest.json').read_text())['mcnf_config']
    config['run'].update(name=f'issue277_failure321_{kind}_t12',
        artifact_dir=str((OUT/'mcnf_artifacts').relative_to(REPO)),
        artifact_fallback_dir=str((OUT/'mcnf_artifacts').relative_to(REPO)),
        solver_log_dir=str((OUT/'logs/mcnf').relative_to(REPO)))
    save(OUT/'inputs'/f'mcnf_{kind}.json',config)
    started=time.perf_counter()
    payload=run_experiment_dict(config)
    wall=time.perf_counter()-started
    save(OUT/'results'/f'mcnf_{kind}.payload.json',payload)
    _,problem,result,model=load_artifact_result(payload['artifact_dir'])
    model.validate_result(result,tol=1e-7)
    assert math.isclose(sum(t.data_volume for t in problem.tasks),1.)
    assert tuple(problem.topology.gpu_nodes)==tuple(range(6))
    return {'engine':'MCNF','collective':kind,'status':result.status,'modeled_time':result.objective,
        'time_steps':result.time_steps,'active_steps':int(sum(result.A>0)),
        'synthesis_wall_sec':wall,'optimizer_solve_time_sec':result.solve_time,
        'timings':payload['timings'],'solver_stats':result.solver_stats,
        'num_variables':model.c.size,'linear_constraints':model.eq_matrix.shape[0]+model.ineq_matrix.shape[0],
        'nonzeros':model.eq_matrix.nnz+model.ineq_matrix.nnz,'validated':True,
        'constraint_violations':model.constraint_violations(result),'artifact_dir':payload['artifact_dir'],
        'peak_rss_mib':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/2**20}


def queue(warm):
    OUT.mkdir()
    if warm:
        cold=json.loads((ROOT/'chunk_sweep/summary.json').read_text())['rows']
        pending=[r['chunks'] for r in cold if r.get('engine')=='TECCL' and not r.get('validated')]
        jobs=[(f'c{c}',['--chunk',str(c),'--warmstart']) for c in pending]
    else:
        jobs=[(f'c{c}',['--chunk',str(c)]) for c in COUNTS]
        jobs += [(f'mcnf_{k}',['--mcnf',k]) for k in ['allgather','allreduce']]
    rows=[]
    for name,args in jobs:
        log=OUT/'logs'/f'{name}.console.txt'
        log.parent.mkdir(exist_ok=True,parents=True)
        started=time.perf_counter()
        reason=None
        with log.open('w') as stream:
            child=subprocess.Popen([sys.executable,__file__,*args],cwd=REPO,stdout=stream,stderr=subprocess.STDOUT)
            while child.poll() is None:
                try:
                    if psutil.Process(child.pid).memory_info().rss>6*2**30:
                        reason='RSS_LIMIT_6GIB'
                    if time.perf_counter()-started>150:
                        reason='WALL_LIMIT_150S'
                    if reason:
                        child.kill(); child.wait(); break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(.5)
        path=OUT/'results'/f'{name}.metrics.json'
        row=json.loads(path.read_text()) if path.exists() else {'case':name,'status':reason or 'WORKER_FAILED','validated':False}
        row.update(process_wall_sec=time.perf_counter()-started,exit_code=child.returncode)
        rows.append(row)
        save(OUT/'summary.json',{'rows':rows})
        print(name,row.get('status'),row.get('allgather_time',row.get('modeled_time')),f"wall={row['process_wall_sec']:.2f}s",flush=True)
    save(OUT/'summary.json',{'rows':rows})


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--chunk',type=int,choices=COUNTS)
    parser.add_argument('--mcnf',choices=['allgather','allreduce'])
    parser.add_argument('--warmstart',action='store_true')
    args=parser.parse_args()
    assert not(args.chunk and args.mcnf)
    assert not(args.warmstart and args.mcnf)
    if args.warmstart:
        OUT=ROOT/'chunk_sweep_warmstart'
    base.OUT=OUT
    base.config_for=config_for
    base.warmstart_for=warmstart_for
    if args.chunk is None and args.mcnf is None:
        queue(args.warmstart)
    else:
        name=f'c{args.chunk}' if args.chunk else f'mcnf_{args.mcnf}'
        try:
            row=base.teccl_case(args.chunk) if args.chunk else mcnf_case(args.mcnf)
            save(OUT/'results'/f'{name}.metrics.json',row)
        except Exception as error:
            save(OUT/'results'/f'{name}.metrics.json',{'case':name,'status':'ERROR','validated':False,'error':f'{type(error).__name__}: {error}'})
            traceback.print_exc()
            raise
