"""Build the final four-layout report from preserved native solver outcomes."""

from collections import Counter
import csv
from datetime import datetime
from html import escape
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT=Path(__file__).resolve().parent
REPO=ROOT.parents[1]
SHAPES=('33','321','44','431')
LABELS={'33':'3+3','321':'3+2+1','44':'4+4','431':'4+3+1'}
COUNTS=(1,2,3,5,10)


def table(head,rows,html=False):
    if html:
        return '<div class="scroll"><table><thead><tr>'+''.join('<th>'+escape(v)+'</th>' for v in head)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in rows)+'</tbody></table></div>'
    return '| '+' | '.join(head)+' |\n|'+'|'.join(['---']*len(head))+'|\n'+'\n'.join('| '+' | '.join(row)+' |' for row in rows)


def main():
    rows=[];states={};sources=[]
    for n,folder in [(6,'closd_6gpu_3h'),(8,'closd_8gpu')]:
        out=REPO/'benchmarks'/folder/'wh1234_3h'
        state=json.loads((out/'state.json').read_text());assert state['status']=='COMPLETED'
        states[str(n)]=state
        rows+=json.loads((out/'summary.json').read_text())['rows']
        sources += [out/'state.json',out/'summary.json',REPO/'benchmarks'/folder/'manifest.json']
    tc=[r for r in rows if r.get('engine')=='TECCL']
    refs={(r['shape'],r['collective']):r for r in rows if r.get('engine')=='MCNF'}
    assert len(tc)==20 and len(refs)==8
    assert Counter(r['status'] for r in tc)=={'OPTIMAL':16,'TIME_LIMIT':4}
    assert sum(bool(r.get('validated')) for r in tc)==18
    assert len([r for r in rows if r.get('status')=='SKIPPED_SCOPE'])==2
    lookup={(r['shape'],r['chunks']):r for r in tc}
    assert set(lookup)=={(s,c) for s in SHAPES for c in COUNTS}
    validation=json.loads((ROOT/'validation.json').read_text())
    assert validation['validated_schedules']==18 and validation['MCNF_validated']==8
    sources += [ROOT/'validation.json',Path(__file__)]
    headers=['每来源块数']+[LABELS[s] for s in SHAPES]
    communication=[];timing=[]
    for c in COUNTS:
        values=[str(c)];seconds=[str(c)]
        for shape in SHAPES:
            r=lookup[(shape,c)]
            values.append(f"{r['allreduce_estimate_us']:.4f}"+(' *' if r['status']=='TIME_LIMIT' else '') if r.get('validated') else '3h 未得可行解')
            seconds.append(f"{r.get('synthesis_sec',r['process_wall_sec']):.3f}"+(' *' if r['status']=='TIME_LIMIT' else ''))
        communication.append(values);timing.append(seconds)
    note='20 项 TECCL 全部结束：16 OPTIMAL，2 TIME_LIMIT 且有经回放验收的可行调度，2 TIME_LIMIT 且无 incumbent；不是不可行证明。六卡额外的 15/20 块仅留取消记录，未进入 MILP。'
    contract='固定每卡完整向量 250000 bytes；N 卡 AllGather 每来源初始 250000/N bytes，再分 c 块，每块 250000/(Nc) bytes。每方向链路 400 Gbps（50 GB/s），每实际活跃物理 epoch 启动费 0.1 us。表中 AR 为完整 AG 时间二倍估算（us），没有运行 ReduceScatter。'
    ends={n:datetime.fromtimestamp(v['ended_unix'],ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S') for n,v in states.items()}
    completion=f"六卡队列结束：{ends['6']}；八卡队列结束：{ends['8']}（Asia/Shanghai）。"
    status_note='* 表示三小时超时的可行上界，未证明最优。3+3/c10 的原生 MIP gap=6.64444445e-8（约 0.00000664%），仍保留 TIME_LIMIT；4+3+1/c5 原生 MIP gap=22.2222%。它们分别完成 52/40 个 epoch。两个八卡 c10 的 SolCount=0，通信时间保持缺失。原生 MIP gap 是带 horizon 偏移的目标 gap，不是通信时间相对误差。'
    best=[]
    for shape in SHAPES:
        r=min((r for r in tc if r['shape']==shape and r.get('validated')),key=lambda r:r['allreduce_estimate_us'])
        ours=refs[(shape,'allreduce')]
        best.append([LABELS[shape],str(r['chunks']),f"{r['allreduce_estimate_us']:.4f}",f"{ours['physical_time_us']:.4f}",f"{100*(1-ours['physical_time_us']/r['allreduce_estimate_us']):.2f}%",f"{ours['synthesis_sec']:.3f}"])
    best_head=['拓扑','TECCL 最好已验证块数','TECCL AR估算(us)','MCNF 直接AR(us)','MCNF 时间减少','MCNF 合成(s)']
    limits='MCNF 为 HWC / T=12 / main-only 的固定调度物理评估，启动费未重新优化；AG 使用普通 broadcast 支持的 HWC 模式（去掉 ahc_2level），直接 AR 使用完整 HWC。TECCL 是等块逐物理跳 epoch，MCNF 是连续分片、可变步长与逻辑 GPU 路由，不能将模型差异称为硬件加速比。两队列在 WH1234 并行运行，每队列内部串行，Gurobi 12.0.3、Threads=4、Seed42、TimeLimit=10800 s、进程保护11400 s/RSS16GiB。成功行合成耗时含建模/求解/导出，排除独立回放；无解行保留进程中止墙钟，纯优化耗时另存 CSV/JSON。'
    evidence='18 个可行 TECCL 调度均再次独立回放需求、链路容量、GPU 因果、交换机无复制及活跃步，两个超时无解行不造数；八个 MCNF z 已在本机重新载入并通过 1e-7 原空间验收。旧六卡 60 秒和 warm-start 证据保留。'
    data={'status':'COMPLETED','completed_MILP':20,'status_counts':{'OPTIMAL':16,'TIME_LIMIT_feasible':2,'TIME_LIMIT_no_incumbent':2},
          'completed_at':ends,'rows':rows,'queue_states':states,'contract':contract,'limitations':limits,
          'source_sha256':{str(p.relative_to(REPO)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}}
    (ROOT/'comparison.json').write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    keys=['case','shape','chunks','status','validated','solution_count','completion_epochs','active_epochs','allgather_us','allreduce_estimate_us','optimizer_sec','synthesis_sec','process_wall_sec','native_mip_gap','objective_bound','peak_rss_mib']
    with (ROOT/'comparison.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore',lineterminator='\n')
        writer.writeheader();writer.writerows(tc)
    md='# WH1234：TECCL 四种 ClosD 拓扑三小时最终结果\n\n'+note+'\n\n'+completion+'\n\n'+contract+'\n\n'+table(headers,communication)+'\n\n'+status_note+'\n\n## 合成/中止耗时（秒）\n\n'+table(headers,timing)+'\n\n## 同机 MCNF 参照\n\n'+table(best_head,best)+'\n\n'+limits+'\n\n'+evidence+'\n'
    (ROOT/'comparison.md').write_text(md)
    fig,axes=plt.subplots(1,2,figsize=(12,4.7),layout='constrained')
    for shape,color in zip(SHAPES,['#277da8','#43aa8b','#f3722c','#9b5de5']):
        selected=sorted([r for r in tc if r['shape']==shape],key=lambda r:r['chunks'])
        valid=[r for r in selected if r.get('validated')]
        axes[0].plot([r['chunks'] for r in valid],[r['allreduce_estimate_us'] for r in valid],'-',color=color,label=LABELS[shape])
        for r in valid:axes[0].plot(r['chunks'],r['allreduce_estimate_us'],'o',color=color,markerfacecolor='white' if r['status']=='TIME_LIMIT' else color)
        axes[1].plot([r['chunks'] for r in selected],[r.get('synthesis_sec',r['process_wall_sec']) for r in selected],'o-',color=color,label=LABELS[shape])
    axes[0].axhline(refs[('33','allreduce')]['physical_time_us'],color='#333',ls='--',label='MCNF 6 GPU / T=12')
    axes[0].axhline(refs[('44','allreduce')]['physical_time_us'],color='#999',ls=':',label='MCNF 8 GPU / T=12')
    axes[0].plot([],[],'o',color='#555',markerfacecolor='white',label='TIME_LIMIT incumbent')
    axes[0].set(xlabel='Chunks per source',ylabel='AR estimate = 2 x AG (us)',ylim=(0,21.5))
    axes[1].set(xlabel='Chunks per source',ylabel='Synthesis / stopped wall time (s)',yscale='log')
    for ax in axes:ax.set_xticks(COUNTS);ax.grid(alpha=.2);ax.legend(fontsize=8)
    fig.savefig(ROOT/'comparison.png',dpi=170);plt.close(fig)
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>WH1234 TECCL 最终结果</title><style>body{max-width:1320px;margin:32px auto;padding:0 24px;font:16px/1.7 system-ui;color:#183a38;background:#fafaf6}table{border-collapse:collapse;width:100%;background:white;font-size:14px}th,td{padding:10px;border-bottom:1px solid #d8e2dc;text-align:right;white-space:nowrap}th{background:#e7efea}td:first-child,th:first-child{text-align:left}.scroll{overflow:auto}img{width:100%}.note{background:#edf3ed;padding:16px}</style>'
    page+='<h1>WH1234 · TECCL 三小时最终结果</h1><p class="note">'+note+'</p><p>'+completion+'</p><p>'+contract+'</p><img src="comparison.png" alt="通信时间和求解耗时，空心点表示超时可行上界">'+table(headers,communication,True)+'<p>'+status_note+'</p><h2>合成/中止耗时（秒）</h2>'+table(headers,timing,True)+'<h2>MCNF T=12 主调度参照</h2>'+table(best_head,best,True)+'<p>'+limits+'</p><p>'+evidence+'</p><p><a href="comparison.md">Markdown</a> · <a href="comparison.csv">CSV</a> · <a href="comparison.json">JSON</a> · <a href="validation.json">验收记录</a></p></html>'
    (ROOT/'comparison.html').write_text(page)
    print(note);print(table(headers,communication));print(table(best_head,best))


if __name__=='__main__':main()
