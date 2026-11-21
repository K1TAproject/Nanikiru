"""Check selected local MJAI archives without training or changing source data."""

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import json
from pathlib import Path
import time

from nanikiru.adapters.mjai import iter_archive, parse_mjai
from nanikiru.analysis.output import debug_json
from nanikiru.processing.records import replay
from nanikiru.rules.config import tenhou_hanchan


def audit_archive(path):
    counts=Counter();events=Counter();failures=[]
    for name,raw in iter_archive(path):
        counts['games']+=1
        try:
            hands=parse_mjai(raw,rules=tenhou_hanchan())
            for line in raw.splitlines():
                if line.strip():events[json.loads(line)['type']]+=1
            for hand in hands:
                for record in hand.records:replay(record).validate()
                counts['hands']+=1
                counts['decisions']+=len(hand.decisions)
                # Exercise prefix extraction and serialization at both ends of each hand.
                selected=hand.decisions[:1]+hand.decisions[-1:] if len(hand.decisions)>1 else hand.decisions
                for sample in replace(hand,decisions=selected).samples():
                    json.loads(debug_json(sample,indent=None))
                    counts['sample_checks']+=1
            counts['passed_games']+=1
        except Exception as error:
            failures.append(dict(member=name,error=f'{type(error).__name__}: {error}'))
    return dict(archive=str(path),counts=dict(counts),events=dict(events),failures=failures)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chunks',type=Path)
    parser.add_argument('report',type=Path)
    parser.add_argument('--workers',type=int,default=4)
    args=parser.parse_args()
    paths=sorted(args.chunks.glob('*.tar'))
    if not paths:parser.error('No tar archives found')
    if args.report.exists():parser.error('Report already exists; choose a new filename')
    args.report.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter();events=Counter();results=[];started=time.monotonic()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for future in as_completed([pool.submit(audit_archive,p) for p in paths]):
            result=future.result();results.append(result)
            counts.update(result['counts']);events.update(result['events'])
            report=dict(status='complete' if len(results)==len(paths) else 'running',
                        completed_archives=len(results),total_archives=len(paths),
                        elapsed_seconds=round(time.monotonic()-started,1),counts=dict(counts),
                        events=dict(events),failures=[f for r in results for f in r['failures']],
                        archives=sorted(results,key=lambda r:r['archive']))
            args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k not in ('archives','events','failures')},ensure_ascii=False),flush=True)
    if report['failures']:raise SystemExit(1)


if __name__=='__main__':main()
