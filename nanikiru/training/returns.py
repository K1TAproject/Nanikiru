"""A-rule full-match return labels and visible value samples; never trains."""

import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import re

from ..adapters.mjai import iter_archive, parse_mjai
from ..analysis.output import debug_json
from ..analysis.rank_features import FEATURE_VERSION, FEATURE_NAMES, extract_features
from ..models import UNKNOWN
from ..observation import observe_request
from ..processing.analysis_input import build_analysis_request
from ..processing.records import replay
from ..rules.config import TENHOU_HANCHAN, tenhou_hanchan
from ..rules.match import match_status
from ..rules.scoring import waits
from ..analysis.efficiency import hand_codes, meld_codes
from .ranking import code_version, digest

SCHEMA='match-return-samples-1'


def _end_status(hand):
    initial=hand.records[0].initial_snapshot
    settlements=hand.outcome['settlements']
    end=replay(hand.records[0])
    scores=[next(p.score for p in end.players if p.seat==s) for s in range(4)]
    if any(p is UNKNOWN for p in scores) or end.riichi_sticks is UNKNOWN:
        raise ValueError('Unknown terminal scores/deposits')
    if settlements[-1]['kind']=='win':
        repeat=any(s['winner']==initial.dealer_seat for s in settlements)
        kind='win'
    else:
        reason=settlements[-1].get('reason',UNKNOWN)
        # A nonzero exhaustive payment establishes a normal/nagashi draw even
        # when the source omitted its reason. Zero-delta unspecified draws are
        # ambiguous (abortive vs exhaustive); do not invent a terminal label.
        if reason in ('exhaustive','fanpai') or (reason is UNKNOWN and end.remaining_draws==0 and
                                               any(settlements[-1]['score_delta'])):
            dealer=replay(hand.records[initial.dealer_seat])
            repeat=bool(waits(hand_codes(dealer),meld_codes(dealer),formal=True))
            kind='exhaustive'
        else:
            repeat=True;kind='abortive'
    status=match_status(scores,dealer=initial.dealer_seat,wind=initial.round_wind,
                        number=initial.hand_number,repeat=repeat,kind=kind)
    ambiguous=settlements[-1]['kind']!='win' and settlements[-1].get('reason',UNKNOWN) is UNKNOWN and kind=='abortive'
    return end,scores,status,repeat,kind,ambiguous


def final_labels(hands):
    """Validate complete opening/end; return seat-indexed labels, not inputs.

Source payments are authoritative, never re-scored. Missing draw reasons
that cannot establish match termination are rejected rather than guessed.
"""
    if not hands:raise ValueError('No hands')
    opening=hands[0].records[0].initial_snapshot
    if (opening.rules!=tenhou_hanchan() or opening.round_wind!='east' or opening.hand_number!=1
            or opening.honba!=0 or opening.riichi_sticks!=0 or any(p.score!=25000 for p in opening.players)):
        raise ValueError('Requires complete standard A-rule opening')
    for n,hand in enumerate(hands):
        if hand.source_id!=hands[0].source_id or hand.hand_index!=n:
            raise ValueError('Mixed source or missing hand sequence')
        p=hand.records[0].initial_snapshot
        if p.rules!=tenhou_hanchan() or (p.dealer_seat-p.hand_number+1)%4!=opening.dealer_seat:
            raise ValueError('Rules or initial seat order changed')
        end,scores,status,repeat,kind,ambiguous=_end_status(hand)
        if status['ended']!=(n==len(hands)-1):
            raise ValueError('Source end does not establish A-rule match termination (possibly missing draw reason)')
        if n+1<len(hands):
            following=hands[n+1].records[0].initial_snapshot
            contexts=[]
            for repeats in ((True,False) if ambiguous else (repeat,)):
                number=p.hand_number if repeats else p.hand_number%4+1
                winds=('east','south','west','north')
                wind=p.round_wind if repeats or p.hand_number!=4 else winds[(winds.index(p.round_wind)+1)%4]
                contexts.append((p.dealer_seat if repeats else (p.dealer_seat+1)%4,wind,number,
                                 p.honba+1 if repeats or kind!='win' else 0))
            if (following.dealer_seat,following.round_wind,following.hand_number,following.honba) not in contexts:
                raise ValueError('Missing or inconsistent round progression')
    if sum(scores)+1000*end.riichi_sticks!=100000:raise ValueError('Final points/deposits not conserved')
    ranking=status['ranking']
    deposit=end.riichi_sticks*1000
    scores[ranking[0]]+=deposit
    labels=[]
    for seat in range(4):
        rank=ranking.index(seat)+1
        points=(scores[seat]-30000)/1000+(20,10,-10,-20)[rank-1]+(20 if rank==1 else 0)
        labels.append(dict(final_rank=rank,rank_value=-rank,final_points=scores[seat],result_points=points))
    return dict(source_id=hands[0].source_id,rules_profile=TENHOU_HANCHAN,
                ranking=ranking,final_deposits=deposit,end_reason=status['reason'],players=labels)


def state_indices(hands):
    """Publicly determined sample sites, independent of outcome/selected action.

Own draw/call decisions, all three observers' discard/kan response windows,
and non-final round boundaries. No fake pass or action labels are assigned.
"""
    sites=[]
    for h,hand in enumerate(hands):
        for seat,record in enumerate(hand.records):
            sites.append((h,seat,0,'round_start'))
            for cursor,e in enumerate(record.events,1):
                kind=e.kind.split('_',1)[1]
                if kind in ('draw','chi','pon') and e.actor==seat:
                    sites.append((h,seat,cursor,'own_decision'))
                elif kind in ('discard','kan_declared') and e.actor!=seat:
                    sites.append((h,seat,cursor,'response_observation'))
            if h<len(hands)-1:sites.append((h,seat,len(record.events),'round_end'))
    return sites


def value_samples(hands, *, split, max_samples=64):
    if split not in ('train','validation','test'):raise ValueError('Invalid source split')
    if type(max_samples) is not int or max_samples<1:raise ValueError('Positive sample cap required')
    labels=final_labels(hands);identity=labels['source_id']
    sites=state_indices(hands)
    selected=sorted(sites,key=lambda s:sha256(f'{identity}:{s}'.encode()).digest())[:max_samples]
    selected.sort()
    for h,seat,cursor,kind in selected:
        req=build_analysis_request(hands[h].records[seat],source_version='value-prefix',through=cursor,
                                   method='shared-rank-linear-1',objective='match_rank',tasks=('value',))
        # Exclude source outcome payloads/deltas and origin IDs from input.
        req=observe_request(req,seed=0)
        req.position.round_id='value-observation'
        features=extract_features(req)
        yield dict(schema_version=SCHEMA,source_id=identity,split=split,hand_index=h,observer=seat,
                   event_count=cursor,sample_kind=kind,sample_weight=1/len(selected),input=req,
                   features=features,labels=labels['players'][seat])


def export_returns(source,destination,*,plan,limit=None,max_samples=64,progress=None):
    """Export only; existing experiment's source split is mandatory and preserved."""
    plan=Path(plan);configuration=json.loads(plan.read_text(encoding='utf-8'))
    splits={}
    for row in configuration['sources']:
        if row['source_id'] in splits:raise ValueError('Duplicate source in split plan')
        if row['split'] not in ('train','validation','test'):raise ValueError('Invalid split plan')
        splits[row['source_id']]=row['split']
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    manifest=dict(schema_version=SCHEMA,status='incomplete',rules_profile=TENHOU_HANCHAN,
                  feature_version=FEATURE_VERSION,feature_names=FEATURE_NAMES,code_sha256=code_version(),
                  plan_sha256=sha256(plan.read_bytes()).hexdigest(),source=str(Path(source).resolve()),
                  max_samples_per_game=max_samples,selection='source_site_sha256',games=0,samples=0,
                  training_performed=False,members=[],counts={},duplicates=0)
    seen=set();counts=Counter()
    try:
        with (destination/'samples.jsonl').open('w',encoding='utf-8') as stream, (destination/'labels.jsonl').open('w',encoding='utf-8') as returns:
            for name,raw in iter_archive(source,limit=limit):
                if not re.fullmatch(r'(202[3-9]|20[3-9]\d)\d{6}gm-00a9-0000-[0-9a-f]{8}\.mjai\.json(?:\.zst)?',Path(name).name):
                    raise ValueError('Expected verified 2023+ ranked 00a9 source member')
                identity=sha256(raw).hexdigest()
                if identity in seen:manifest['duplicates']+=1;continue
                if identity not in splits:raise ValueError('Source missing from fixed split plan')
                hands=parse_mjai(raw,rules=tenhou_hanchan())
                labels=final_labels(hands)
                # Finish this game before writing any of its samples.
                samples=list(value_samples(hands,split=splits[identity],max_samples=max_samples))
                returns.write(debug_json(dict(labels,split=splits[identity]),indent=None)+'\n')
                for sample in samples:
                    stream.write(debug_json(sample,indent=None)+'\n')
                    counts[f"{sample['split']}:{sample['sample_kind']}"]+=1
                seen.add(identity);manifest['games']+=1;manifest['samples']+=len(samples)
                manifest['members'].append(dict(member=name,source_id=identity,split=splits[identity],samples=len(samples)))
                if progress:progress(dict(games=manifest['games'],samples=manifest['samples']))
        if not seen:raise ValueError('No games')
        manifest['status']='complete'
        manifest['files']={p.name:digest(p) for p in
                           (destination/'samples.jsonl',destination/'labels.jsonl')}
    except Exception as error:
        manifest['status']='failed';manifest['error']=f'{type(error).__name__}: {error}'
        raise
    finally:
        manifest['counts']=dict(counts)
        (destination/'manifest.json').write_text(debug_json(manifest),encoding='utf-8')
    return manifest


def read_samples(folder, *, split):
    """Validated streaming reader for the future fitter; never fits or updates."""
    if split not in ('train','validation','test'):raise ValueError('Invalid split')
    folder=Path(folder);manifest=json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if (manifest['status']!='complete' or manifest['schema_version']!=SCHEMA
            or manifest['feature_version']!=FEATURE_VERSION or manifest['feature_names']!=list(FEATURE_NAMES)
            or manifest['rules_profile']!=TENHOU_HANCHAN or manifest['code_sha256']!=code_version()):
        raise ValueError('Incomplete or stale value dataset; rebuild rather than bypass checks')
    for name in ('samples.jsonl','labels.jsonl'):
        if digest(folder/name)!=manifest['files'][name]:raise ValueError('Dataset checksum mismatch')
    membership={m['source_id']:m['split'] for m in manifest['members']}
    with (folder/'samples.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line)
            if row['schema_version']!=SCHEMA or membership.get(row['source_id'])!=row['split']:
                raise ValueError('Sample source/split mismatch')
            if row['split']==split:yield row


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('destination',type=Path)
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--limit',type=int)
    p.add_argument('--samples-per-game',type=int,default=64)
    args=p.parse_args()
    result=export_returns(args.source,args.destination,plan=args.plan,limit=args.limit,max_samples=args.samples_per_game,
                          progress=lambda d:print(json.dumps(d),flush=True))
    print(json.dumps({k:result[k] for k in ('status','games','samples','training_performed')}))


if __name__=='__main__':main()
