"""WH1234 issue277: bounded 3-hour native TECCL solves on two eight-GPU ClosD layouts."""

import argparse
import csv
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

from check import replay, selfcheck

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = ROOT/'wh1234_3h'
COUNTS = (1,2,3,5,10)
SHAPES = ('44','431')
LIMIT_SEC = 10800
WALL_SEC = 11400
RSS_BYTES = 16*2**30


def save(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
    temp.replace(path)


def config_for(shape,chunks):
    assert shape in SHAPES and chunks in COUNTS
    c=json.loads((REPO/'benchmarks/closd_3plus3/inputs/allgather_c2.json').read_text())
    c['TopologyParams'].update(name=f'ClosD{shape}',chassis=2 if shape=='44' else 3,chunk_size=1/(8*chunks))
    c['GurobiParams'].update(time_limit=3.,log_file=f'_wh1234_3h_{shape}_c{chunks}_{OUT.name}')
    c['InstanceParams'].update(num_chunks=chunks,num_epochs=8*chunks+8,
        schedule_output_file=str((OUT/'results'/f'{shape}_c{chunks}.schedule.json').relative_to(REPO)))
    assert c['GurobiParams']['time_limit']*3600==LIMIT_SEC
    assert math.isclose(chunks*c['TopologyParams']['chunk_size'],1/8)
    return c


def solve_teccl(shape,chunks):
    import gurobipy as gp
    from teccl.input_data import (UserInputParams,TopologyParams,GurobiParams,InstanceParams,
                                 Collective,EpochType,ObjectiveType,SolutionMethod)
    from teccl.scheduler import TECCLSolver
    config=config_for(shape,chunks)
    case=f'{shape}_c{chunks}'
    save(OUT/'inputs'/f'{case}.json',config)
    instance=dict(config['InstanceParams'])
    for key,enum in [('collective',Collective),('epoch_type',EpochType),('objective_type',ObjectiveType),('solution_method',SolutionMethod)]:
        instance[key]=enum(instance[key])
    user=UserInputParams()
    user.topology=TopologyParams(**config['TopologyParams'])
    user.gurobi=GurobiParams(**config['GurobiParams'])
    user.instance=InstanceParams(**instance)
    gp.setParam('Threads',4);gp.setParam('Seed',42)
    Path('Logs').mkdir(exist_ok=True)
    started=time.perf_counter()
    solver=TECCLSolver(user).solver
    set_params=solver.set_gurobi_params
    def record_params():
        set_params()
        solver.model.update()
        assert solver.model.Params.TimeLimit==LIMIT_SEC
        assert solver.model.Params.Threads==4 and solver.model.Params.Seed==42
        save(OUT/'results'/f'{case}.effective_params.json',
             {'TimeLimit':solver.model.Params.TimeLimit,'Threads':solver.model.Params.Threads,
              'Seed':solver.model.Params.Seed,'num_variables':solver.model.NumVars,
              'horizon_epochs':solver.num_epochs,'epoch_duration':solver.epoch_duration})
    solver.set_gurobi_params=record_params
    status=solver.encode_problem()
    encoded=time.perf_counter()
    model=solver.model
    row={'engine':'TECCL','case':case,'shape':shape,'chunks':chunks,
         'status':{2:'OPTIMAL',9:'TIME_LIMIT',3:'INFEASIBLE'}.get(status,str(status)),
         'native_status_code':status,'solution_count':model.SolCount,
         'time_limit_sec':model.Params.TimeLimit,'optimizer_sec':model.Runtime,
         'encode_wall_sec':encoded-started,'num_variables':model.NumVars,
         'linear_constraints':model.NumConstrs,'general_constraints':model.NumGenConstrs,
         'nonzeros':model.NumNZs,'horizon_epochs':solver.num_epochs,
         'objective_bound':model.ObjBound,'validated':False}
    if model.SolCount:
        row.update(native_objective=model.ObjVal,native_mip_gap=model.MIPGap,
                   max_model_violation=max(model.ConstrVio,model.BoundVio,model.IntVio))
        _,schedule=solver.get_schedule()
        save(OUT/'results'/f'{case}.schedule.json',schedule)
        exported=time.perf_counter()
        checked=replay(schedule,config)
        assert row['max_model_violation']<=1e-6
        active=checked['active_epochs']
        epochs=schedule['3-Epochs_Required']
        ag=5*schedule['4-Collective_Finish_Time']+.1*active
        row.update(validated=True,validation=checked,completion_epochs=epochs,active_epochs=active,
                   chunk_bytes=250000/(8*chunks),transport_us=5*schedule['4-Collective_Finish_Time'],
                   startup_us=.1*active,allgather_us=ag,allreduce_estimate_us=2*ag,
                   export_sec=exported-encoded,validation_sec=time.perf_counter()-exported,
                   synthesis_sec=exported-started)
    row['worker_sec']=time.perf_counter()-started
    row['peak_rss_mib']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024 if sys.platform=='linux' else 2**20)
    log=next(Path('Logs').glob(f'*_wh1234_3h_{shape}_c{chunks}_{OUT.name}.log'))
    text=log.read_text()
    import re
    text=re.sub(r'(?m)^.*LicenseID.*\n?','',text)
    (OUT/'logs').mkdir(parents=True,exist_ok=True)
    (OUT/'logs'/f'{case}.solver.txt').write_text('\n'.join(line.rstrip() for line in text.splitlines()).rstrip()+'\n')
    return row


def solve_mcnf(shape,kind):
    from mcnf_tree.config import run_experiment_dict
    from mcnf_tree.artifacts import load_artifact_result
    old=REPO/'benchmarks/closd_3plus3/chunk_sweep/inputs'/f'mcnf_{kind}.json'
    config=json.loads(old.read_text())
    config['network']=json.loads((ROOT/'manifest.json').read_text())['topologies'][shape]['mcnf_config']
    if kind=='allreduce':
        config['task']={'type':'allreduce','params':{'group_size':8,'data_volume':1/8}}
    else:
        config['task']={'type':'mixed','mix':[{'type':'broadcast','params':{'root':r,'data_volume':1/8}} for r in range(8)]}
    case=f'{shape}_mcnf_{kind}'
    config['solver']['solver_args']['TimeLimit']=LIMIT_SEC
    config['run'].update(name=f'issue277_8gpu_{case}_t12',
        artifact_dir=str((OUT/'mcnf_artifacts').relative_to(REPO)),
        artifact_fallback_dir=str((OUT/'mcnf_artifacts').relative_to(REPO)),
        solver_log_dir=str((OUT/'logs/mcnf').relative_to(REPO)))
    save(OUT/'inputs'/f'{case}.json',config)
    started=time.perf_counter();payload=run_experiment_dict(config);elapsed=time.perf_counter()-started
    save(OUT/'results'/f'{case}.payload.json',payload)
    _,problem,result,model=load_artifact_result(payload['artifact_dir'])
    model.validate_result(result,tol=1e-7)
    assert math.isclose(sum(t.data_volume for t in problem.tasks),1.)
    assert tuple(problem.topology.gpu_nodes)==tuple(range(8))
    active=int(sum(result.A>0))
    return {'engine':'MCNF','case':case,'shape':shape,'collective':kind,'status':result.status,
            'time_steps':result.time_steps,'active_steps':active,'normalized_time':result.objective,
            'physical_time_us':5*float(result.A.sum())+.1*active,'synthesis_sec':elapsed,
            'optimizer_sec':result.solver_stats['solve_time'],'validated':True,
            'violations':model.constraint_violations(result),'artifact_dir':payload['artifact_dir'],
            'solver_stats':result.solver_stats}


def rss(pid):
    try:
        return int(Path(f'/proc/{pid}/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
    except FileNotFoundError:
        return 0


def queue():
    OUT.mkdir()
    rows=[]
    jobs=[(f'{shape}_mcnf_{kind}',['--shape',shape,'--mcnf',kind])
          for shape in SHAPES for kind in ('allgather','allreduce')]
    jobs += [(f'{shape}_c{c}',['--shape',shape,'--chunk',str(c)]) for c in COUNTS for shape in SHAPES]
    for case,args in jobs:
        log=OUT/'logs'/f'{case}.console.txt'
        log.parent.mkdir(parents=True,exist_ok=True)
        started=time.time();reason=None
        with log.open('w') as stream:
            command=[sys.executable,'-u',str(Path(__file__).resolve()),*args]
            child=subprocess.Popen(command,cwd=REPO,stdout=stream,stderr=subprocess.STDOUT)
            save(OUT/'state.json',{'status':'RUNNING','case':case,'pid':child.pid,'started_unix':started,
                                 'command':command,'completed':len(rows),'total':len(jobs),
                                 'solver_time_limit_sec':LIMIT_SEC,'process_wall_limit_sec':WALL_SEC})
            while child.poll() is None:
                if rss(child.pid)>RSS_BYTES:reason='RSS_LIMIT_16GIB'
                if time.time()-started>WALL_SEC:reason='WALL_LIMIT_11400S'
                if reason:child.kill();child.wait();break
                time.sleep(2)
        path=OUT/'results'/f'{case}.metrics.json'
        row=json.loads(path.read_text()) if path.exists() else {'case':case,'status':reason or 'WORKER_FAILED','validated':False}
        row.update(process_wall_sec=time.time()-started,exit_code=child.returncode)
        # Keep license identifiers out of pullback console copies.
        import re
        text=re.sub(r'(?m)^.*LicenseID.*\n?','',log.read_text())
        log.write_text(text)
        rows.append(row)
        save(OUT/'summary.json',{'rows':rows})
        print(case,row['status'],row.get('allgather_us',row.get('physical_time_us')),flush=True)
    save(OUT/'state.json',{'status':'COMPLETED','completed':len(rows),'total':len(jobs),
                          'ended_unix':time.time(),'validated':sum(bool(r.get('validated')) for r in rows)})
    keys=['case','engine','shape','chunks','status','validated','allgather_us','allreduce_estimate_us',
          'physical_time_us','optimizer_sec','synthesis_sec','process_wall_sec']
    with (OUT/'summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore',lineterminator='\n')
        writer.writeheader();writer.writerows(rows)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--shape',choices=SHAPES)
    parser.add_argument('--chunk',type=int,choices=COUNTS)
    parser.add_argument('--mcnf',choices=['allgather','allreduce'])
    parser.add_argument('--selfcheck',action='store_true')
    args=parser.parse_args()
    if args.selfcheck:
        selfcheck()
        for shape in SHAPES:
            for c in COUNTS:config_for(shape,c)
        assert LIMIT_SEC==10800 and WALL_SEC>LIMIT_SEC
        print('PASS: ten fixed-volume inputs, 3-hour solver and longer wall guard, topology/replay checks')
    elif args.shape:
        assert bool(args.chunk) != bool(args.mcnf)
        case=f'{args.shape}_c{args.chunk}' if args.chunk else f'{args.shape}_mcnf_{args.mcnf}'
        try:
            row=solve_teccl(args.shape,args.chunk) if args.chunk else solve_mcnf(args.shape,args.mcnf)
            save(OUT/'results'/f'{case}.metrics.json',row)
        except Exception as error:
            save(OUT/'results'/f'{case}.metrics.json',{'case':case,'status':'ERROR','validated':False,
                                                      'error':f'{type(error).__name__}: {error}'})
            traceback.print_exc();raise
    else:
        assert args.chunk is None and args.mcnf is None
        queue()
