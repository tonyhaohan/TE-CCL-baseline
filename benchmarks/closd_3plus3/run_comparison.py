"""Issue277: bounded, isolated TECCL chunk sweep and MCNF public-API comparison."""

import argparse
import gzip
import json
import math
import os
from pathlib import Path
import resource
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = ROOT / 'chunk_sweep'
COUNTS = (1, 2, 3, 5, 10, 15, 20)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def config_for(chunks):
    config = json.loads((ROOT / 'inputs/allgather_c2.json').read_text())
    config['TopologyParams']['chunk_size'] = 1 / (6 * chunks)
    config['InstanceParams'].update(num_chunks=chunks, num_epochs=6 * chunks + 6,
                                   schedule_output_file=str((OUT / 'results' / f'c{chunks}.schedule.json').relative_to(REPO)))
    config['GurobiParams'].update(time_limit=60 / 3600, log_file=f'_sweep_c{chunks}')
    assert math.isclose(chunks * config['TopologyParams']['chunk_size'], 1 / 6)
    return config


def warmstart_for(chunks, config):
    from check_allgather import SEND, replay
    assert chunks in (10, 15, 20)
    base = json.loads((ROOT / 'chunk_sweep/results/c5.schedule.json').read_text())
    block_epochs = base['3-Epochs_Required']
    duration = config['TopologyParams']['chunk_size']
    lines, events, arrivals = [], set(), {(s, c, s): 0 for s in range(6) for c in range(chunks)}
    for block in range(chunks // 5):
        for line in base['7-Flows']:
            match = SEND.fullmatch(line)
            c, source, i, j, epoch = map(int, match.groups()[:5])
            c += 5 * block
            epoch += block_epochs * block
            path = [i] + [int(v) for v in match[6].split('->')] + [j]
            lines.append(f'Chunk {c} from {source} traveled over {i}->{j} in epoch {epoch} via switches {match[6]}')
            for offset, (a, b) in enumerate(zip(path, path[1:])):
                events.add((source, a, b, c, epoch + offset))
            arrivals.setdefault((source, c, j), epoch + len(path) - 1)
    horizon = block_epochs * (chunks // 5)
    demand_met = {f'GPU {d}': {f'GPU {s}': {f'Chunk {c}': arrivals[(s, c, d)] * duration
                  for c in range(chunks)} for s in range(6) if s != d} for d in range(6)}
    schedule = {'1-Epoch_Duration': duration, '3-Epochs_Required': horizon,
                '4-Collective_Finish_Time': horizon * duration, '6-Demand_Met': demand_met, '7-Flows': lines}
    validation = replay(schedule, config)
    save(OUT / 'results' / f'c{chunks}.seed.schedule.json', schedule)
    path = OUT / 'inputs' / f'c{chunks}.mst.gz'
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt') as stream:
        for source, a, b, c, epoch in sorted(events):
            stream.write(f'flow_{source}_{a}_{b}_{c}_{epoch} 1\n')
        for source in range(9):
            for d in range(9):
                for c in range(chunks):
                    arrival = arrivals.get((source, c, d), float('inf'))
                    for epoch in range(config['InstanceParams']['num_epochs']):
                        stream.write(f'buffer_{source}_{d}_{c}_{epoch} {int(arrival <= epoch)}\n')
                        if source < 6 and d < 6 and source != d:
                            stream.write(f'total_demand_{source}_{d}_{c}_{epoch} {int(arrival <= epoch + 1)}\n')
    return str(path.relative_to(REPO)), validation


def teccl_case(chunks):
    import gurobipy as gp
    from teccl.input_data import (UserInputParams, TopologyParams, GurobiParams, InstanceParams,
                                  Collective, EpochType, ObjectiveType, SolutionMethod)
    from teccl.scheduler import TECCLSolver
    from check_allgather import replay, check_flow_cache

    check_flow_cache()
    config = config_for(chunks)
    seed_started = time.perf_counter()
    seed_validation = None
    if OUT.name == 'chunk_sweep_warmstart':
        config['InstanceParams']['warmstart'], seed_validation = warmstart_for(chunks, config)
        config['GurobiParams']['log_file'] += '_warmstart'
    seed_wall = time.perf_counter() - seed_started
    save(OUT / 'inputs' / f'c{chunks}.json', config)
    instance = dict(config['InstanceParams'])
    for key, enum in [('collective', Collective), ('epoch_type', EpochType),
                      ('objective_type', ObjectiveType), ('solution_method', SolutionMethod)]:
        instance[key] = enum(instance[key])
    user = UserInputParams()
    user.topology = TopologyParams(**config['TopologyParams'])
    user.gurobi = GurobiParams(**config['GurobiParams'])
    user.instance = InstanceParams(**instance)
    gp.setParam('Threads', 4)
    gp.setParam('Seed', 42)
    Path('Logs').mkdir(exist_ok=True)
    started = time.perf_counter()
    solver = TECCLSolver(user).solver
    encode_start = time.perf_counter()
    status = solver.encode_problem()
    encode_wall = time.perf_counter() - encode_start
    model = solver.model
    row = {'engine': 'TECCL', 'chunks': chunks, 'status_code': status,
           'status': {2: 'OPTIMAL', 9: 'TIME_LIMIT', 3: 'INFEASIBLE'}.get(status, str(status)),
           'horizon_epochs': solver.num_epochs, 'epoch_duration': solver.epoch_duration,
           'num_variables': model.NumVars, 'linear_constraints': model.NumConstrs,
           'general_constraints': model.NumGenConstrs, 'nonzeros': model.NumNZs,
           'threads': model.Params.Threads, 'seed': model.Params.Seed,
           'optimizer_runtime_sec': model.Runtime, 'encode_call_wall_sec': encode_wall,
           'build_and_setup_estimate_sec': max(0., encode_wall - model.Runtime),
           'solution_count': model.SolCount, 'objective_bound': model.ObjBound,
           'validated': False}
    if model.SolCount:
        row.update(native_objective=model.ObjVal, mip_gap=model.MIPGap,
                   max_model_violation=max(model.ConstrVio, model.BoundVio, model.IntVio))
        export_start = time.perf_counter()
        primitive, demand_epochs, _, _ = solver.get_flows_buffer_demand()
        save(OUT / 'results' / f'c{chunks}.primitive.json', {'flows': primitive})
        _, schedule = solver.get_schedule()
        save(OUT / 'results' / f'c{chunks}.schedule.json', schedule)
        row['export_wall_sec'] = time.perf_counter() - export_start
        validation_start = time.perf_counter()
        row['validation'] = replay(schedule, config)
        row['validation_wall_sec'] = time.perf_counter() - validation_start
        row['validated'] = row['max_model_violation'] <= 1e-6
        row['allgather_time'] = schedule['4-Collective_Finish_Time']
        row['allreduce_estimate'] = 2 * row['allgather_time']
        row['epochs_required'] = schedule['3-Epochs_Required']
        row['epoch_lower_bound'] = max(0, math.ceil(solver.num_epochs + 1 + model.ObjBound / 100 - 1e-6))
        row['synthesis_wall_sec'] = encode_wall + (encode_start - started) + row['export_wall_sec']
    row['warmstart_build_sec'] = seed_wall if seed_validation else None
    row['warmstart_seed_validation'] = seed_validation
    row['worker_wall_sec'] = time.perf_counter() - started
    row['peak_rss_mib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20
    return row


def mcnf_case(kind):
    from mcnf_tree.config import run_experiment_dict
    from mcnf_tree.artifacts import load_artifact_result
    from mcnf_tree.collectives import normalize_comm_patterns

    network = json.loads((ROOT / 'manifest.json').read_text())['mcnf_config']
    if kind == 'allreduce':
        task = {'type': 'allreduce', 'params': {'group_size': 6, 'data_volume': 1 / 6}}
        comm = {'name': 'hwc'}
    else:
        task = {'type': 'mixed', 'mix': [{'type': 'broadcast', 'params': {'root': r, 'data_volume': 1 / 6}} for r in range(6)]}
        # 普通 broadcast 的现有公开契约不接受 ahc_2level；使用其余 HWC 模式。
        comm = {'patterns': [{'name': p.name, 'params': p.params}
                            for p in normalize_comm_patterns({'name': 'hwc'}) if p.name != 'ahc_2level']}
    config = {'network': network, 'task': task, 'comm': comm,
              'modeling': {'family': 'node_arc', 'params': {'time_steps': 12, 'beta': 1.}},
              'solver': {'name': 'gurobi_native', 'solver_args': {'Method': 2, 'Crossover': 0, 'Threads': 4,
                         'TimeLimit': 60, 'FeasibilityTol': 1e-8, 'OptimalityTol': 1e-8, 'BarConvTol': 1e-10}},
              'presolve': {'name': 'none'}, 'squeeze': {'method': 'none'},
              'crossover': {'method': 'none'}, 'pareto': {'method': 'none'},
              'run': {'name': f'issue277_{kind}_t12', 'artifact': True, 'artifact_include_z': True,
                      'artifact_dir': 'benchmarks/closd_3plus3/chunk_sweep/mcnf_artifacts',
                      'artifact_fallback_dir': 'benchmarks/closd_3plus3/chunk_sweep/mcnf_artifacts',
                      'solver_log': True, 'solver_log_dir': 'benchmarks/closd_3plus3/chunk_sweep/logs/mcnf',
                      'solver_log_to_console': False, 'save': False}}
    save(OUT / 'inputs' / f'mcnf_{kind}.json', config)
    started = time.perf_counter()
    payload = run_experiment_dict(config)
    synthesis = time.perf_counter() - started
    save(OUT / 'results' / f'mcnf_{kind}.payload.json', payload)
    _, problem, result, model = load_artifact_result(payload['artifact_dir'])
    model.validate_result(result, tol=1e-7)
    assert math.isclose(sum(t.data_volume for t in problem.tasks), 1.)
    assert tuple(problem.topology.gpu_nodes) == tuple(range(6))
    return {'engine': 'MCNF', 'collective': kind, 'status': result.status, 'modeled_time': result.objective,
            'time_steps': result.time_steps, 'active_steps': int(sum(result.A > 1e-10)),
            'synthesis_wall_sec': synthesis, 'optimizer_solve_time_sec': result.solve_time,
            'timings': payload['timings'], 'solver_stats': result.solver_stats,
            'num_variables': model.c.size, 'linear_constraints': model.eq_matrix.shape[0] + model.ineq_matrix.shape[0],
            'nonzeros': model.eq_matrix.nnz + model.ineq_matrix.nnz, 'validated': True,
            'constraint_violations': model.constraint_violations(result), 'artifact_dir': payload['artifact_dir'],
            'peak_rss_mib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2**20}


def run_queue():
    import psutil
    OUT.mkdir()
    rows = []
    warm = OUT.name == 'chunk_sweep_warmstart'
    counts = (10, 15, 20) if warm else COUNTS
    jobs = [(f'c{c}', ['--chunk', str(c)] + (['--warmstart'] if warm else [])) for c in counts]
    if not warm:
        jobs += [(f'mcnf_{kind}', ['--mcnf', kind]) for kind in ['allgather', 'allreduce']]
    for name, args in jobs:
        log = OUT / 'logs' / f'{name}.console.txt'
        log.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        with log.open('w') as stream:
            child = subprocess.Popen([sys.executable, __file__, *args], cwd=REPO, stdout=stream, stderr=subprocess.STDOUT)
            reason = None
            while child.poll() is None:
                try:
                    if psutil.Process(child.pid).memory_info().rss > 6 * 2**30:
                        reason = 'RSS_LIMIT_6GIB'
                    if time.perf_counter() - started > 150:
                        reason = 'WALL_LIMIT_150S'
                    if reason:
                        child.kill()
                        child.wait()
                        break
                except psutil.NoSuchProcess:
                    pass
                time.sleep(.5)
        path = OUT / 'results' / f'{name}.metrics.json'
        row = json.loads(path.read_text()) if path.exists() else {'case': name, 'status': reason or 'WORKER_FAILED', 'validated': False}
        row['process_wall_sec'] = time.perf_counter() - started
        row['exit_code'] = child.returncode
        rows.append(row)
        save(OUT / 'summary.json', {'rows': rows})
        print(name, row.get('status'), row.get('allgather_time', row.get('modeled_time')), f"wall={row['process_wall_sec']:.2f}s", flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--chunk', type=int, choices=COUNTS)
    parser.add_argument('--mcnf', choices=['allgather', 'allreduce'])
    parser.add_argument('--warmstart', action='store_true')
    args = parser.parse_args()
    if args.warmstart:
        assert args.chunk in (None, 10, 15, 20) and args.mcnf is None
        OUT = ROOT / 'chunk_sweep_warmstart'
    if args.chunk is None and args.mcnf is None:
        run_queue()
    else:
        name = f'c{args.chunk}' if args.chunk else f'mcnf_{args.mcnf}'
        try:
            row = teccl_case(args.chunk) if args.chunk else mcnf_case(args.mcnf)
            save(OUT / 'results' / f'{name}.metrics.json', row)
        except Exception as error:
            save(OUT / 'results' / f'{name}.metrics.json', {'case': name, 'status': 'ERROR', 'validated': False,
                                                        'error': f'{type(error).__name__}: {error}'})
            traceback.print_exc()
            raise
