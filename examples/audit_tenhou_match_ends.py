"""Compare only win-round match termination against complete source MJAI games.

This does not re-score hands, validate all actions, or infer missing draw reasons.
"""
import argparse
from hashlib import file_digest
import json
from pathlib import Path

from nanikiru.adapters.mjai import iter_archive
from nanikiru.rules.match import match_status


def audit(source):
    report=dict(scope='win_round_match_end_only',games=0,win_rounds=0,draw_rounds_not_checked=0,
                mismatches=0,examples=[])
    for name,raw in iter_archive(source):
        rounds=[]
        for line in raw.splitlines():
            if not line.strip():continue
            event=json.loads(line);kind=event['type']
            if kind=='start_kyoku':
                start=event;scores=event['scores'][:];winners=[]
            elif kind=='reach_accepted':scores[event['actor']]-=1000
            elif kind in ('hora','ryukyoku'):
                scores=[v+d for v,d in zip(scores,event['deltas'])]
                if kind=='hora':winners.append(event['actor'])
            elif kind=='end_kyoku':rounds.append((start,scores,winners))
        for index,(start,scores,winners) in enumerate(rounds):
            if not winners:
                report['draw_rounds_not_checked']+=1;continue
            report['win_rounds']+=1
            actual=index==len(rounds)-1
            prediction=match_status(scores,dealer=start['oya'],wind={'E':'east','S':'south','W':'west','N':'north'}[start['bakaze']],
                                    number=start['kyoku'],repeat=start['oya'] in winners,kind='win')
            if prediction['ended']!=actual:
                report['mismatches']+=1
                if len(report['examples'])<20:
                    report['examples'].append(dict(member=name,round=index,scores=scores,winners=winners,
                                                   context={k:start[k] for k in ('bakaze','kyoku','oya')},
                                                   actual_end=actual,prediction=prediction))
        report['games']+=1
    with Path(source).open('rb') as stream:report['archive_sha256']=file_digest(stream,'sha256').hexdigest()
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    report=audit(args.source)
    with args.output.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
