"""Reproducible simulator-source observation comparison, no training claims."""

import argparse
import json
from pathlib import Path

from nanikiru.analysis.output import debug_json
from nanikiru.observation.dataset import export_dataset
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator


def source_samples(rounds=2, decisions=8):
    for seed in range(rounds):
        game=Simulator(seed=f'observer-demo-{seed}')
        emitted=0
        while game.phase!='ended' and emitted<decisions:
            actions=[a for seat in range(4) for a in game.legal_actions(seat)]
            passed=next((a for a in actions if a.kind=='pass'),None)
            if passed:
                game.submit(passed)
                continue
            action=next(a for a in actions if a.kind=='discard')
            record=game.record(action.actor)
            request=build_analysis_request(record,source_version=f'demo-{seed}',method='fixed-v2')
            yield dict(schema_version='discard-sample-v1',source_id=f'synthetic-simulator-demo-{seed}',
                       hand_index=0,observer=action.actor,event_count=len(record.events),input=request,
                       labels={'action':action,'outcome':None})
            emitted+=1
            game.submit(action)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True,help='new directory')
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    source=args.output/'source.jsonl'
    with source.open('w',encoding='utf-8') as stream:
        for sample in source_samples():
            stream.write(debug_json(sample,indent=None)+'\n')
    report=export_dataset([source],args.output/'observations',shard_size=25,audit_limit=16,seed='demo-v1')
    lines=['# 观察器对照实验','', '固定模拟种子，2 个来源、16 个弃牌时点，各生成 5 种观察，共 80 条样本。',
           '标签中的弃牌来自测试推进，不是最优策略；本实验检查信息降级，不测策略强度。','',
           '| 观察类型 | 方法 | 可排序 / 样本 | 可执行推荐 | 全候选进张可用 |',
           '| --- | --- | --- | --- | --- |']
    totals={}; agreement={}
    for line in (args.output/'observations'/'audit.jsonl').read_text(encoding='utf-8').splitlines():
        row=json.loads(line)
        methods=row['results']
        pair=agreement.setdefault(row['variant'],[0,0])
        pair[0]+=1
        pair[1]+=bool(set(methods['minimal-shape-v1']['top_codes']) & set(methods['fixed-v2']['top_codes']))
        for method,result in row['results'].items():
            counts=totals.setdefault((row['variant'],method),[0,0,0,0])
            counts[0]+=1;counts[1]+=bool(result['top_tiles']);counts[2]+=result['executable']
            counts[3]+=bool(result['candidate_statuses']) and all(c['ukeire']=='available' for c in result['candidate_statuses'].values())
    for (variant,method),(n,rank,executable,ukeire) in totals.items():
        lines.append(f'| {variant} | {method} | {rank} / {n} | {executable} | {ukeire} |')
    lines+=['','## 两个方法的首选比较','', '| 观察类型 | 首选牌种存在交集 / 样本 |','| --- | --- |']
    for variant,(n,same) in agreement.items():lines.append(f'| {variant} | {same} / {n} |')
    lines+=['','首选不同不代表任一方法更优；只用于观察人工权重是否改变行为。',
            '结果仅对应本次固定样本。随机遮蔽可能没有遮到某一局面的牌；具体掩码配置、种子和逐候选状态见分片及 audit.jsonl。']
    (args.output/'report.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
