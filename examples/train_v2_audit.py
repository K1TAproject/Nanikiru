"""Rebuild features from verified exports, preserving source splits; no promotion."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from hashlib import sha256
import json
from pathlib import Path

from examples.train_v2_round1 import merge_caches, write_status
from nanikiru.analysis.output import debug_json
from nanikiru.rules.config import TENHOU_HANCHAN
from nanikiru.training.ranking import code_version, digest, partition, prepare, run, TUNABLE


def build(args):
    source,destination=args
    def progress(value):
        print(json.dumps(dict(chunk=destination.name,**value)),flush=True)
    return prepare([source],destination,rules_profile=TENHOU_HANCHAN,progress=progress)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,default=Path('data/external-replays/v2-round1-A-retry-01'))
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--samples-per-game',type=int,default=1)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    if args.samples_per_game<1 or args.workers<1:parser.error('Counts must be positive')
    root=args.output;root.mkdir(parents=True,exist_ok=False)
    try:
        write_status(root,stage='selecting',completed_chunks=0,total_chunks=args.workers)
        old=json.loads((args.source/'experiment.json').read_text(encoding='utf-8'))
        expected={r['source_id']:r['split'] for r in old['sources']}
        selected={};inputs=[]
        for path in sorted(args.source.glob('import-*/discard-samples.jsonl')):
            checksum=digest(path);inputs.append(dict(path=str(path.resolve()),sha256=checksum))
            with path.open(encoding='utf-8') as stream:
                for line in stream:
                    row=json.loads(line);identity=row['source_id']
                    if identity not in expected:raise ValueError('Unexpected source')
                    if partition(identity,'0')!=expected[identity]:raise ValueError('Split changed')
                    key=sha256(json.dumps([identity,row['hand_index'],row['observer'],row['event_count']],separators=(',',':')).encode()).hexdigest()
                    bucket=selected.setdefault(identity,[]);bucket.append((key,line))
                    bucket.sort(key=lambda item:item[0]);del bucket[args.samples_per_game:]
            if digest(path)!=checksum:raise ValueError('Export changed during selection')
        if set(selected)!=set(expected):raise ValueError('Missing source exports')
        paths=[root/f'samples-{i:03d}.jsonl' for i in range(args.workers)]
        for i,path in enumerate(paths):
            with path.open('x',encoding='utf-8') as stream:
                for identity in sorted(selected)[i::args.workers]:
                    for _,line in selected[identity]:stream.write(line if line.endswith('\n') else line+'\n')
        plan=dict(code_sha256=code_version(),formula_scope='discard_riichi_initialization_not_rank_optimization',
                  selection='lowest_sha256_of_source_hand_observer_event_no_label_selection',
                  split_seed='0',sources=expected,inputs=inputs,samples_per_game=args.samples_per_game,
                  rows=sum(map(len,selected.values())),rounds=2,tune=list(TUNABLE),
                  primary_evaluation='mean_final_rank',secondary_evaluation='settlement_points_only_after_rank_equivalence',
                  test_status='previous_round_holdout_reused_diagnostic_not_fresh_final_test')
        (root/'experiment.json').write_text(debug_json(plan),encoding='utf-8')
        caches=[root/f'cache-{i:03d}' for i in range(len(paths))]
        write_status(root,stage='preparing',completed_chunks=0,total_chunks=len(paths))
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            jobs=[pool.submit(build,pair) for pair in zip(paths,caches)]
            for n,future in enumerate(as_completed(jobs),1):
                future.result();write_status(root,stage='preparing',completed_chunks=n,total_chunks=len(jobs))
        merge_caches(caches,root/'features',expected)
        if code_version()!=plan['code_sha256']:raise ValueError('Code changed during run')
        def progress(value):
            with (root/'fit-progress.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(debug_json(value,indent=None)+'\n')
            write_status(root,**value)
        write_status(root,stage='coverage_audit')
        report=run(root/'features',root/'fit',rounds=2,tune=TUNABLE,progress=progress)
        write_status(root,stage='complete',weights=report['weights'],tune=report['tune'],
                     frozen_no_signal=report['frozen_no_signal'],not_strength_evidence=True)
    except Exception as error:
        write_status(root,stage='failed',error=f'{type(error).__name__}: {error}')
        raise


if __name__=='__main__':main()
