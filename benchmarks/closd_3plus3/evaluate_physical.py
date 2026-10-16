"""Replay the measured chunk sweep under the repository's physical report contract."""

import csv
import hashlib
from html import escape
import json
import math
from pathlib import Path
import time

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

from check_allgather import SEND, check_flow_cache, replay
from mcnf_tree.artifacts import load_artifact_result

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
OUT = ROOT / 'physical_comparison'
COUNTS = (1, 2, 3, 5, 10, 15, 20)
VECTOR_BYTES = 250000
BANDWIDTH_BYTES_SEC = 50_000_000_000
STARTUP_US = 0.1
VECTOR_TIME_US = VECTOR_BYTES / BANDWIDTH_BYTES_SEC * 1e6


def read(path):
    return json.loads(path.read_text())


def physical_time(transport_us, active_steps):
    assert math.isfinite(transport_us) and transport_us >= 0
    assert isinstance(active_steps, int) and active_steps >= 0
    return transport_us + STARTUP_US * active_steps


def main():
    started = time.perf_counter()
    check_flow_cache()
    assert VECTOR_TIME_US == 5.0 and math.isclose(physical_time(5., 3), 5.3)
    OUT.mkdir(exist_ok=True)
    cold = read(ROOT / 'chunk_sweep/summary.json')['rows']
    warm = read(ROOT / 'chunk_sweep_warmstart/summary.json')['rows']
    cold = {row['chunks']: row for row in cold if row['engine'] == 'TECCL'}
    warm = {row['chunks']: row for row in warm}
    assert set(cold) == set(COUNTS) and set(warm) == {10, 15, 20}
    rows, source_paths = [], [ROOT / 'manifest.json', Path(__file__), ROOT / 'check_allgather.py',
                              ROOT / 'run_comparison.py']
    for c in COUNTS:
        original = cold[c]
        selected = original if original['validated'] else warm[c]
        group = 'chunk_sweep' if original['validated'] else 'chunk_sweep_warmstart'
        config_path = ROOT / group / 'inputs' / f'c{c}.json'
        schedule_path = ROOT / group / 'results' / f'c{c}.schedule.json'
        metrics_path = ROOT / group / 'results' / f'c{c}.metrics.json'
        config, schedule = read(config_path), read(schedule_path)
        checked = replay(schedule, config)
        assert checked == selected['validation'] and selected['validated']
        assert math.isclose(c * config['TopologyParams']['chunk_size'], 1 / 6)
        source_paths += [config_path, schedule_path, metrics_path,
                         ROOT / 'chunk_sweep/results' / f'c{c}.metrics.json']
        active_epochs = set()
        for line in schedule['7-Flows']:
            match = SEND.fullmatch(line)
            epoch = int(match[5])
            hops = 1 + (len(match[6].split('->')) if match[6] else 0)
            active_epochs.update(range(epoch, epoch + hops))
        epochs = schedule['3-Epochs_Required']
        assert active_epochs == set(range(epochs)), 'must count actual active epochs'
        chunk_bytes = VECTOR_BYTES / (6 * c)
        epoch_transfer_us = chunk_bytes / BANDWIDTH_BYTES_SEC * 1e6
        assert math.isclose(6 * c * chunk_bytes, VECTOR_BYTES)
        assert math.isclose((BANDWIDTH_BYTES_SEC / chunk_bytes) * epoch_transfer_us / 1e6, 1.)
        transport_us = epochs * epoch_transfer_us
        assert math.isclose(transport_us, VECTOR_TIME_US * selected['allgather_time'])
        ag = physical_time(transport_us, len(active_epochs))
        rows.append({'chunks_per_source': c, 'total_distinct_chunks': 6*c, 'chunk_bytes': chunk_bytes,
                     'source_initial_bytes': c * chunk_bytes, 'output_bytes_per_gpu': VECTOR_BYTES,
                     'physical_link_Gbps': 400, 'epoch_transport_us': epoch_transfer_us,
                     'completion_epochs': epochs, 'active_epochs': len(active_epochs),
                     'transport_us': transport_us, 'startup_us': STARTUP_US * len(active_epochs),
                     'allgather_us': ag, 'allreduce_estimate_us': 2*ag,
                     'selected_status': selected['status'], 'selected_origin': group,
                     'cold_status': original['status'], 'cold_solution_count': original['solution_count'],
                     'cold_synthesis_or_stopped_wall_sec': original.get('synthesis_wall_sec', original['worker_wall_sec']),
                     'cold_optimizer_sec': original['optimizer_runtime_sec'],
                     'warm_synthesis_wall_sec': None if group == 'chunk_sweep' else selected['synthesis_wall_sec'],
                     'warm_seed_build_sec': selected.get('warmstart_build_sec'),
                     'warm_optimizer_sec': None if group == 'chunk_sweep' else selected['optimizer_runtime_sec'],
                     'selected_native_mip_gap': selected['mip_gap'], 'num_variables': selected['num_variables'],
                     'peak_rss_mib': selected['peak_rss_mib'], 'validated': True,
                     'validation': checked, 'schedule': str(schedule_path.relative_to(REPO))})
    refs = []
    for kind in ('allgather', 'allreduce'):
        metrics_path = ROOT / 'chunk_sweep/results' / f'mcnf_{kind}.metrics.json'
        previous = read(metrics_path)
        source_paths.append(metrics_path)
        artifact = REPO / previous['artifact_dir']
        _, problem, result, model = load_artifact_result(artifact)
        model.validate_result(result, tol=1e-7)
        assert math.isclose(sum(task.data_volume for task in problem.tasks), 1.)
        assert math.isclose(result.objective, previous['modeled_time'])
        active = int(np.count_nonzero(result.A > 0))
        assert active == previous['active_steps']
        source_paths += [artifact / 'run_manifest.json', artifact / 'solution.npz']
        refs.append({'collective': kind, 'normalized_volume': 1., 'time_steps': result.time_steps,
                     'active_steps': active, 'transport_us': VECTOR_TIME_US * float(result.A.sum()),
                     'startup_us': STARTUP_US * active,
                     'physical_time_us': physical_time(VECTOR_TIME_US * float(result.A.sum()), active),
                     'synthesis_wall_sec': previous['synthesis_wall_sec'],
                     'optimizer_sec': previous['solver_stats']['solve_time'],
                     'status': result.status, 'validated': True,
                     'constraint_violations': model.constraint_violations(result),
                     'artifact': str(artifact.relative_to(REPO))})
    baseline = refs[1]['physical_time_us']
    for row in rows:
        row['mcnf_allreduce_less_time_percent'] = 100 * (1-baseline/row['allreduce_estimate_us'])
    data = {'contract': {'vector_bytes_per_gpu': VECTOR_BYTES, 'link_Gbps': 400, 'startup_us_per_active_step': STARTUP_US,
                         'full_vector_transport_us': VECTOR_TIME_US, 'allgather_initial_bytes_per_gpu': VECTOR_BYTES/6,
                         'allgather_chunk_bytes': '250000/(6*c)', 'allreduce': '2 * complete AllGather time',
                         'time_formula_us': 'epochs * 5/(6*c) + 0.1 * active_epochs',
                         'method': 'physical replay of saved schedules; no new MILP solve',
                         'synthesis_timing': 'measured earlier on normalized but combinatorially identical models; one run per case',
                         'mcnf_reference': 'T=12 main-only fixed schedule evaluation; no startup reoptimization'},
            'teccl': rows, 'mcnf': refs, 'validation_wall_sec': time.perf_counter()-started,
            'source': read(ROOT/'manifest.json'),
            'source_sha256': {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest()
                              for path in sorted(set(source_paths))}}
    (OUT/'comparison.json').write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
    with (OUT/'comparison.csv').open('w', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=[key for key in rows[0] if key != 'validation'], lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: value for key, value in row.items() if key != 'validation'} for row in rows)
    header = ['每来源块数', '块大小(bytes)', '活跃步', 'AG(μs)', 'AR=2AG(μs)', '冷启动合成/中止(s)', '重试合成(s)', '调度状态']
    table_rows = [[str(r['chunks_per_source']), f"{r['chunk_bytes']:.2f}", str(r['active_epochs']),
                   f"{r['allgather_us']:.4f}", f"{r['allreduce_estimate_us']:.4f}",
                   f"{r['cold_synthesis_or_stopped_wall_sec']:.3f}",
                   '—' if r['warm_synthesis_wall_sec'] is None else f"{r['warm_synthesis_wall_sec']:.3f}",
                   'OPTIMAL' if r['selected_status']=='OPTIMAL' else 'TIME_LIMIT / 可行上界'] for r in rows]
    note = ('同一份每卡完整向量 0.25 MB，六卡 AllGather 每来源初始 0.25/6 MB，再拆为 c 块；'
            '每条有向边 400 Gbps=50 GB/s，每实际活跃步加 0.1 μs。AllReduce 按完整 AllGather 的二倍估算。')
    caveat = ('1/2/3/5 块的原生模型已证明最优；10/15/20 块冷启动各优化 60 秒没有 incumbent，'
              '用 5 块调度拼接的初始解另优化 60 秒，均未改进且未证明最优。表中重试不包含种子构造约 0.09/0.19/0.34 秒，'
              '也不包含先求得 5 块调度的成本。合成耗时含建模、求解和成功时的导出，排除导入和独立回放；中止行保留实际耗时。'
              '这些耗时来自先前归一化模型的真实运行，本次为物理回放，没有新增求解耗时。')
    model_note = ('TECCL 的 alpha 是按 epoch 取整的链路传播延迟，与每活跃步启动费不同；本次不把 0.1 μs 塞入该字段。'
                  '逐步重新标定时间而不改变路由和并发：每步传输 5/(6c) μs，再加 0.1 μs；所有保存调度均无空 epoch。'
                  '对固定 c，这只是每 epoch 乘同一个正数，最少完成 epoch 的目标不变。'
                  'MCNF 是连续分片、可变步长、逻辑 GPU 路由；TECCL 在物理链路逐跳存储转发。'
                  '这里只比较各自模型的固定调度评估，未针对启动费重新优化 MCNF，也没有硬件实测。')
    md_table = '| '+' | '.join(header)+' |\n|'+ '|'.join(['---']*len(header))+'|\n'
    md_table += '\n'.join('| '+' | '.join(row)+' |' for row in table_rows)
    ref_table = '| MCNF T=12 主求解 | 活跃步 | 带宽项(μs) | 启动项(μs) | 总时间(μs) | 合成(s) |\n|---|---:|---:|---:|---:|---:|\n'
    ref_rows = []
    for r in refs:
        values = [r['collective'], str(r['active_steps']), f"{r['transport_us']:.4f}", f"{r['startup_us']:.4f}",
                  f"{r['physical_time_us']:.4f}", f"{r['synthesis_wall_sec']:.3f}"]
        ref_rows.append(values)
        ref_table += '| '+' | '.join(values)+' |\n'
    conclusion = ('本组已验证调度中，2 块的 TECCL 端到端估算最短：AG=6.2 μs，AR=12.4 μs。'
                  f"MCNF T=12 主调度 AG={refs[0]['physical_time_us']:.4f} μs、AR={baseline:.4f} μs；"
                  f"后者比 12.4 μs 少 {100*(1-baseline/12.4):.2f}%。不能据此宣称对所有块数或真实硬件全局最优。")
    (OUT/'comparison.md').write_text('# TECCL / MCNF：3+3 ClosD 物理口径对比\n\n'+note+'\n\n'+conclusion+'\n\n'+md_table+'\n\n'+caveat+'\n\n'+ref_table+'\n'+model_note+'\n\n完整输入、调度、日志和回放脚本保存在本 fork 的上级目录。来源及文件 SHA256 见 comparison.json。\n')
    xs = [r['chunks_per_source'] for r in rows]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.6), layout='constrained')
    ax = axes[0]
    for key, label, color in [('allgather_us','TECCL AG','#277da8'), ('allreduce_estimate_us','TECCL 2 x AG','#f3722c')]:
        ax.plot(xs[:4], [r[key] for r in rows[:4]], 'o-', color=color, label=label+' (optimal)')
        ax.plot(xs[3:], [r[key] for r in rows[3:]], '--', color=color, alpha=.65)
        ax.plot(xs[4:], [r[key] for r in rows[4:]], 'o', markerfacecolor='white', color=color, label=label+' (incumbent)')
    ax.axhline(refs[0]['physical_time_us'], color='#43aa8b', label='MCNF AG / T=12')
    ax.axhline(baseline, color='#9b5de5', label='MCNF AR / T=12')
    ax.set(xlabel='Chunks per source', ylabel='Modeled end-to-end time (us)', xticks=xs, ylim=(0, 34))
    ax.legend(fontsize=8)
    ax = axes[1]
    ax.plot(xs, [r['cold_synthesis_or_stopped_wall_sec'] for r in rows], 'o-', label='Cold synthesis / stopped')
    ax.plot(xs[4:], [r['warm_synthesis_wall_sec'] for r in rows[4:]], 'o--', label='Seeded retry (separate)')
    ax.axhline(refs[0]['synthesis_wall_sec'], color='#43aa8b', label='MCNF AG synthesis')
    ax.axhline(refs[1]['synthesis_wall_sec'], color='#9b5de5', label='MCNF AR synthesis')
    ax.set(xlabel='Chunks per source', ylabel='Measured synthesis wall time (s)', xticks=xs, yscale='log')
    ax.legend(fontsize=8)
    for ax in axes:
        ax.grid(alpha=.2)
    fig.savefig(OUT/'comparison.png', dpi=170)
    plt.close(fig)
    def table(head, values):
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+escape(v)+'</th>' for v in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in values)+'</tbody></table></div>'
    page = '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TECCL 3+3 ClosD 分块对比</title><style>body{max-width:1260px;margin:32px auto;padding:0 24px;font:16px/1.7 system-ui;color:#183a38;background:#fafaf6}h1{font-size:28px}table{border-collapse:collapse;font-size:14px;width:100%;background:white}th,td{padding:10px;border-bottom:1px solid #d8e2dc;text-align:right;white-space:nowrap}th{background:#e7efea}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}img{width:100%;height:auto}.note{padding:16px;background:#edf3ed}a{color:#19766d}</style>'
    page += '<h1>TECCL / MCNF · 3+3 ClosD</h1><p>'+note+'</p><p class="note">'+conclusion+'</p><img src="comparison.png" alt="通信时间与实测算法合成耗时"><h2>TECCL 分块结果</h2>'+table(header, table_rows)+'<p>'+caveat+'</p><h2>MCNF 固定调度参照</h2>'+table(['Collective','活跃步','带宽项(μs)','启动项(μs)','总时间(μs)','合成(s)'],ref_rows)+'<p>'+model_note+'</p><p>Apple M4 / 24 GiB / conda math / Gurobi 13.0.0 / 四线程 / Seed 42。每项一次运行，时延与算法计算耗时分开报告。所有可行 TECCL 调度均通过容量、因果、无复制、需求完整性回放；MCNF 保存的 z 重新载入并通过 1e-7 原空间验收。</p><p><a href="comparison.csv">CSV</a> · <a href="comparison.json">完整结果与来源</a> · <a href="comparison.md">Markdown</a></p></html>'
    (OUT/'comparison.html').write_text(page)
    print(conclusion)
    print(md_table)
    print(ref_table)
    print('PASS: seven schedules replayed; constant data volume and epoch capacity checked; two MCNF z artifacts revalidated')


if __name__ == '__main__':
    main()
