"""Render completed or partial WH1234 rows; missing measurements remain missing."""

import csv
from html import escape
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

OUT=Path(__file__).resolve().parent/'wh1234_3h'


def main():
    data=json.loads((OUT/'summary.json').read_text())
    raw_rows=data['rows']
    rows=[r for r in raw_rows if r.get('status')!='SKIPPED_SCOPE']
    skipped=len(raw_rows)-len(rows)
    state=json.loads((OUT/'state.json').read_text())
    effective_total=14
    header=['Case','状态','已验收','AG(us)','AR=2AG(us)','MCNF(us)','优化(s)','合成/中止(s)']
    def num(value):return '—' if value is None else f'{value:.6g}'
    values=[[r['case'],r['status'],str(r.get('validated',False)),num(r.get('allgather_us')),
             num(r.get('allreduce_estimate_us')),num(r.get('physical_time_us')),num(r.get('optimizer_sec')),
             num(r.get('synthesis_sec',r.get('process_wall_sec')))] for r in rows]
    note=('WH1234，3+3 / 3+2+1：均为 1/2/3/5/10 块。每项 Gurobi TimeLimit=10800 s，四线程、Seed42；'
          '外层墙钟 11400 s，RSS 16 GiB，串行队列。每卡完整向量 250000 bytes，AG 每来源 1/6，'
          '每来源 c 块，每块 250000/(6c) bytes；每有向链路 400 Gbps，每实际活跃 epoch 0.1 us。'
          'AR 按完整 AG 时间二倍估算。本轮重跑不覆盖旧六卡 60 秒实验。')
    limits=('TECCL 以固定块和逐物理跳 epoch 建模，MCNF 使用连续分片、可变步长的逻辑 GPU 路由。'
            'MCNF 为 HWC / T=12 / main-only，AG 沿用公开 broadcast 支持的 HWC 模式（去掉 ahc_2level），'
            '直接 AR 使用完整 HWC；启动费是保存调度的事后评估，未重新优化。'
            'TIME_LIMIT 与无 incumbent 保留为原状态；未验收行没有通信时间。没有硬件实测，'
            'WH1234 Gurobi 12.0.3 的计算耗时不与旧 Mac Gurobi 13.0.0 直接混为同机测速。')
    md='# TECCL 六卡重跑 ClosD / WH1234 三小时实验\n\n'+note+'\n\n'
    md+=f"队列状态：{state['status']}，完成 {len(rows)}/{effective_total}（含四项 MCNF 参照，另有 {skipped} 项范围取消记录，不计入实验）。\n\n"
    md+='| '+' | '.join(header)+' |\n|'+'|'.join(['---']*len(header))+'|\n'
    md+='\n'.join('| '+' | '.join(v)+' |' for v in values)+'\n\n'+limits+'\n'
    (OUT/'comparison.md').write_text(md)
    keys=['case','engine','shape','chunks','status','validated','allgather_us','allreduce_estimate_us',
          'physical_time_us','optimizer_sec','synthesis_sec','process_wall_sec']
    with (OUT/'summary.csv').open('w',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=keys,extrasaction='ignore',lineterminator='\n')
        writer.writeheader();writer.writerows(rows)
    fig,axes=plt.subplots(1,2,figsize=(11,4.5),layout='constrained')
    for shape,color in [('33','#277da8'),('321','#f3722c')]:
        selected=[r for r in rows if r.get('engine')=='TECCL' and r.get('shape')==shape and r.get('validated')]
        selected.sort(key=lambda r:r['chunks'])
        if selected:
            axes[0].plot([r['chunks'] for r in selected],[r['allreduce_estimate_us'] for r in selected],'o-',color=color,label=f'TECCL {shape}: 2 x AG')
        ref=next((r for r in rows if r.get('engine')=='MCNF' and r.get('shape')==shape and r.get('collective')=='allreduce' and r.get('validated')),None)
        if ref:axes[0].axhline(ref['physical_time_us'],color=color,ls='--',label=f'MCNF {shape}: AR / T=12')
        measured=[r for r in rows if r.get('engine')=='TECCL' and r.get('shape')==shape]
        if measured:axes[1].plot([r['chunks'] for r in measured],[r.get('synthesis_sec',r['process_wall_sec']) for r in measured],'o-',color=color,label=f'TECCL {shape}')
    axes[0].set(xlabel='Chunks per source',ylabel='Modeled end-to-end time (us)',ylim=(0,None))
    axes[1].set(xlabel='Chunks per source',ylabel='Synthesis / stopped wall time (s)',yscale='log')
    for ax in axes:
        ax.set_xticks([1,2,3,5,10]);ax.grid(alpha=.2)
        if ax.lines:ax.legend(fontsize=8)
    fig.savefig(OUT/'comparison.png',dpi=160);plt.close(fig)
    table='<table><thead><tr>'+''.join('<th>'+escape(v)+'</th>' for v in header)+'</tr></thead><tbody>'
    table+=''.join('<tr>'+''.join('<td>'+escape(v)+'</td>' for v in row)+'</tr>' for row in values)+'</tbody></table>'
    page='<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TECCL 六卡重跑三小时实验</title><style>body{max-width:1280px;margin:32px auto;padding:0 24px;font:16px/1.7 system-ui;color:#183a38;background:#fafaf6}table{border-collapse:collapse;width:100%;font-size:13px;background:white}th,td{padding:8px;border-bottom:1px solid #ccc;text-align:right;white-space:nowrap}td:first-child{text-align:left}img{width:100%}.scroll{overflow:auto}</style>'
    page+='<h1>TECCL / MCNF · 六卡重跑 ClosD · WH1234</h1><p>'+note+'</p>'
    page+=f"<p>队列 {escape(state['status'])}，已完成 {len(rows)}/{effective_total}。</p>"
    page+='<img src="comparison.png" alt="通信时间与算法合成耗时"><div class="scroll">'+table+'</div><p>'+limits+'</p><p><a href="summary.csv">CSV</a> · <a href="summary.json">原始汇总</a> · <a href="state.json">队列状态</a></p></html>'
    (OUT/'comparison.html').write_text(page)
    print(f"REPORT: {state['status']} {len(rows)}/{effective_total}")


if __name__=='__main__':main()
