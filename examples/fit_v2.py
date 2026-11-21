"""Synthetic plumbing check, not real-log training or evidence of playing strength."""

import argparse
from collections import Counter
import json
from pathlib import Path

from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json
from nanikiru.observation.dataset import variants
from nanikiru.observation.observer import ObservationConfig
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator
from nanikiru.training.ranking import partition, prepare, run


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    source=args.output/'synthetic.jsonl';counts=Counter()
    targets={'train':6,'validation':2,'test':2}
    with source.open('w',encoding='utf-8') as stream:
        for number in range(1000):
            identity=f'synthetic-ranking-demo-{number}';split=partition(identity,'0')
            if counts[split]>=targets[split]:continue
            game=Simulator(seed=identity)
            request=build_analysis_request(game.record(0),source_version=identity)
            request.position.round_id=identity
            result=analyze(request)
            action=result.recommendations['overall'][0]
            sample=dict(schema_version='discard-sample-v1',source_id=identity,hand_index=0,
                        observer=0,event_count=len(request.history),input=request,
                        labels=dict(action=action,outcome={'synthetic_v1_teacher':True}))
            configs={'complete':ObservationConfig(),
                     'midgame':ObservationConfig(history_start=len(request.history)),
                     'public_missing':ObservationConfig(public_code_rate=1)}
            for row in variants(sample,configs,seed='0'):
                stream.write(debug_json(row,indent=None)+'\n')
            counts[split]+=1
            if dict(counts)==targets:break
    prepare([source],args.output/'cache',seed='0')
    report=run(args.output/'cache',args.output/'fit',rounds=2)
    # Run again against the exact frozen input and compare all outputs.
    repeated=run(args.output/'cache',args.output/'repeat',rounds=2)
    assert report==repeated
    print(json.dumps(dict(synthetic_only=True,sources=dict(counts),repeat_identical=True,
                          weights=report['weights'],
                          test_before=report['baseline']['test'],test_after=report['fitted']['test']),
                     ensure_ascii=False,indent=2))


if __name__=='__main__':main()
