"""Read-only MJAI response feasibility audit, not a training sample exporter."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import time

from nanikiru.adapters.mjai import iter_archive, _MjaiHand
from nanikiru.contracts import AnalysisRequest
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.rules.visible_actions import additional_actions

MELDS={'chi','pon','open_kan'}
RAW_MELDS={'chi','pon','daiminkan'}


def classify_window(seat, kinds, following, source):
    """Conservative partial label: never infer an actual private ron/pass."""
    if not kinds:return 'no_confirmed_meld_opportunity'
    if following['type']=='tsumo' and following['actor']==(source+1)%4:
        return 'inferred_no_meld'
    if following['type'] in RAW_MELDS:
        return 'observed_meld' if following['actor']==seat else 'priority_censored'
    return 'terminal_or_unresolved'


def inspect_game(raw):
    rows=[json.loads(line) for line in raw.splitlines() if line.strip()]
    discards=[i for i,e in enumerate(rows) if e['type']=='dahai']
    selected={discards[i*(len(discards)-1)//min(11,len(discards)-1)] for i in range(min(12,len(discards)))} if len(discards)>1 else set(discards)
    counts=Counter();examples={};hand=None;hand_index=0
    identity=sha256(raw).hexdigest()
    for i,event in enumerate(rows):
        kind=event['type']
        if kind=='start_kyoku':
            hand=_MjaiHand(event,tenhou_hanchan(),identity,hand_index);hand_index+=1
        elif kind=='end_kyoku':
            hand.finish();hand=None
        elif kind not in ('start_game','end_game'):
            hand.event(event)
        if i not in selected:continue
        following=next(e for e in rows[i+1:] if e['type'] not in ('reach_accepted','dora'))
        counts['discard_windows']+=1
        for seat in range(4):
            if seat==event['actor']:continue
            request=AnalysisRequest(state_version='coverage-audit',position=hand.snapshot(seat),tasks=('recommend',),
                history=tuple(hand.records[seat].events),coverage={'from_start':True,'gaps':[]})
            candidates=additional_actions(request)
            kinds={c.action.kind for c in candidates if c.action.kind in MELDS and c.legality=='allowed'}
            category=classify_window(seat,kinds,following,event['actor'])
            counts[category]+=1
            for action in kinds:counts[f'{category}:{action}']+=1
            if any(c.action.kind=='ron' and c.legality=='unknown' for c in candidates):counts['ron_permission_unknown']+=1
            if category=='observed_meld':
                expected={'daiminkan':'open_kan'}.get(following['type'],following['type'])
                if expected not in kinds:raise ValueError('Observed meld absent from confirmed candidate kinds')
            if kinds and category not in examples:
                examples[category]=dict(source_id=identity,line=i+1,observer=seat,
                    candidate_kinds=sorted(kinds),next_event_type=following['type'],
                    label_scope='meld_only_not_full_pass_or_ron')
    return dict(counts),examples


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('chunks',type=Path);p.add_argument('output',type=Path)
    args=p.parse_args()
    if args.output.exists():p.error('Output exists')
    paths=sorted(args.chunks.glob('*.tar'))
    if not paths:p.error('No chunks')
    started=time.monotonic();events=Counter();counts=Counter();failures=[];examples={};sources=[]
    games=0;deep=0
    for path in paths:
        for n,(name,raw) in enumerate(iter_archive(path)):
            games+=1
            for line in raw.splitlines():
                if line.strip():events[json.loads(line)['type']]+=1
            if n!=0:continue
            try:
                result,found=inspect_game(raw);counts.update(result);deep+=1
                for key,value in found.items():examples.setdefault(key,value)
                sources.append(dict(member=name,sha256=sha256(raw).hexdigest()))
            except Exception as error:failures.append(dict(member=name,error=f'{type(error).__name__}: {error}'))
        report=dict(status='running',scope='raw_event_census_and_bounded_meld_opportunity_feasibility_not_exported_training',
            games=games,deep_games=deep,selection='first_game_per_sorted_chunk_12_even_discard_windows_per_game',
            events=dict(events),counts=dict(counts),examples=examples,sources=sources,failures=failures,
            elapsed_seconds=round(time.monotonic()-started,2))
        args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(games=games,deep_games=deep,failures=len(failures))),flush=True)
    report['status']='failed' if failures else 'complete'
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    if failures:raise SystemExit(1)


if __name__=='__main__':main()
