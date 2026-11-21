"""Four-player complete MJAI archives -> visible records, without re-scoring outcomes.

Physical copies are assigned locally because MJAI specifies tile codes, not IDs.
The existing import-state projector handles visibility and confirmed event deltas.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import replace
import argparse
import gzip
from hashlib import sha256
import io
import json
import re
from pathlib import Path
import tarfile
import xml.etree.ElementTree as ET

import zstandard

from .tenhou import _Hand, code
from ..models import UNKNOWN, valid_seat
from ..rules.config import RuleConfig, TENHOU_HANCHAN, tenhou_hanchan
from ..rules.scoring import validate_groups
from ..tiles import validate_code
from ..analysis.output import debug_json
from ..processing.records import save_record

HONORS={'E':'1z','S':'2z','W':'3z','N':'4z','P':'5z','F':'6z','C':'7z'}


def tile_code(value):
    if not isinstance(value,str):raise ValueError('MJAI tile must be a string')
    if value in HONORS:return HONORS[value]
    if value in ('5mr','5pr','5sr'):return '0'+value[1]
    if not isinstance(value,str) or len(value)!=2 or value[0]=='0' or value[1] not in 'mps':
        raise ValueError(f'Unknown/unsupported MJAI tile: {value!r}')
    return validate_code(value)


def four_scores(value):
    if not isinstance(value,list) or len(value)!=4 or any(type(n) is not int or n%100 for n in value):
        raise ValueError('Expected four integer scores in 100-point units')
    return value


class _MjaiHand(_Hand):
    event_prefix='mjai'
    sample_schema='discard-sample-v1'

    def __init__(self,event,rules,source_id,hand_index):
        self.unused=set(range(136));self.win_context=None;self.visible_ids=[{} for _ in range(4)]
        wind=event['bakaze'];number=event['kyoku']
        if not valid_seat(event['oya']) or any(type(event[k]) is not int or event[k]<0 for k in ('honba','kyotaku')):
            raise ValueError('Invalid dealer/honba/kyotaku')
        if wind not in ('E','S','W','N') or type(number) is not int or not 1<=number<=4:
            raise ValueError('Invalid round wind/number')
        hands=event['tehais']
        if not isinstance(hands,list) or len(hands)!=4 or any(not isinstance(h,list) or len(h)!=13 for h in hands):
            raise ValueError('Four complete 13-tile hands required; partial/three-player logs unsupported')
        ids=[[self.allocate(c) for c in h] for h in hands]
        marker=self.allocate(event['dora_marker'])
        scores=four_scores(event['scores'])
        init=dict(seed=','.join(map(str,[('E','S','W','N').index(wind)*4+number-1,
                  event['honba'],event['kyotaku'],0,0,marker])),oya=str(event['oya']),
                  ten=','.join(str(s//100) for s in scores),
                  **{f'hai{s}':','.join(map(str,h)) for s,h in enumerate(ids)})
        super().__init__(init,rules,source_id,hand_index)

    def public_ids(self,value,seat):
        if isinstance(value,str) and re.fullmatch(r't\d+',value):
            mapping=self.visible_ids[seat]
            if value not in mapping:mapping[value]=f'v{len(mapping)}'
            return mapping[value]
        if isinstance(value,dict):return {k:self.public_ids(v,seat) for k,v in value.items()}
        if isinstance(value,list):return [self.public_ids(v,seat) for v in value]
        if isinstance(value,tuple):return tuple(self.public_ids(v,seat) for v in value)
        return value

    def snapshot(self,seat):
        p=super().snapshot(seat)
        p.tiles=tuple(replace(t,id=self.public_ids(t.id,seat)) for t in p.tiles)
        p.groups=self.public_ids(p.groups,seat)
        p.draw_context=self.public_ids(p.draw_context,seat)
        p.pending=self.public_ids(p.pending,seat)
        return p

    def emit(self,kind,actor=None,**fact):
        super().emit(kind,actor,**fact)
        for seat,record in enumerate(self.records):
            record.events[-1].data['fact']=self.public_ids(record.events[-1].data['fact'],seat)

    def finish(self):
        hand=super().finish()
        return replace(hand,decisions=tuple((seat,cursor,replace(action,tile_ids=self.public_ids(action.tile_ids,seat)))
                                           for seat,cursor,action in hand.decisions))

    def allocate(self,pai):
        c=tile_code(pai)
        tile=next((t for t in sorted(self.unused) if code(t)==c),None)
        if tile is None:raise ValueError('MJAI physical tile inventory exceeded')
        self.unused.remove(tile);return tile

    def pick(self,seat,pais,*,exclude=()):
        available=[t for t in self.hands[seat] if t not in exclude];result=[]
        for pai in pais:
            c=tile_code(pai);tile=next((t for t in available if code(t)==c),None)
            if tile is None:raise ValueError('MJAI action tile not in hand')
            available.remove(tile);result.append(tile)
        return result

    def normalized(self,tag,**attrs):
        self.apply(ET.Element(tag,{k:str(v) for k,v in attrs.items()}))

    def event(self,event):
        kind=event['type'];seat=event.get('actor')
        if kind in ('tsumo','dahai','reach','reach_accepted','chi','pon','daiminkan','ankan','kakan','hora') and not valid_seat(seat):
            raise ValueError('Invalid MJAI actor')
        if self.ends and kind!='hora':raise ValueError('Action after terminal event')
        if kind=='tsumo':self.normalized('TUVW'[seat]+str(self.allocate(event['pai'])))
        elif kind=='dahai':
            drawn=self.draw['tile'] if self.draw and self.draw['seat']==seat else None
            if type(event['tsumogiri']) is not bool:raise ValueError('Discard requires explicit tsumogiri boolean')
            if event['tsumogiri']:
                if drawn is None or code(drawn)!=tile_code(event['pai']):raise ValueError('Tsumogiri differs from drawn tile')
                tile=drawn
            else:tile=self.pick(seat,[event['pai']],exclude=(drawn,))[0]
            self.normalized('DEFG'[seat]+str(tile))
        elif kind in ('reach','reach_accepted'):
            self.normalized('REACH',who=seat,step=1 if kind=='reach' else 2)
            if 'scores' in event and four_scores(event['scores'])!=self.scores:raise ValueError('Riichi scores disagree')
        elif kind=='dora':self.normalized('DORA',hai=self.allocate(event['dora_marker']))
        elif kind in ('chi','pon','daiminkan','ankan','kakan'):
            if not isinstance(event['consumed'],list):raise ValueError('Consumed tiles must be a list')
            mapped={'daiminkan':'open_kan','ankan':'closed_kan','kakan':'added_kan'}.get(kind,kind)
            if kind in ('chi','pon','daiminkan'):
                target=event['target']
                if not valid_seat(target) or target==seat or not self.pending:raise ValueError('Invalid call target')
                called=int(self.pending['tile_id'][1:])
                if code(called)!=tile_code(event['pai']):raise ValueError('Call differs from pending tile')
                contributed=self.pick(seat,event['consumed'])
                g=dict(kind=mapped,tiles=[*contributed,called],called=called,source=target)
                if kind=='chi' and target!=(seat-1)%4:raise ValueError('Chi must be from preceding player')
            elif kind=='ankan':
                contributed=self.pick(seat,event['consumed'])
                g=dict(kind=mapped,tiles=contributed,called=None,source=None)
            else:
                consumed=Counter(tile_code(c) for c in event['consumed'])
                pon=next((g for g in self.groups.values() if g['seat']==seat and g['kind']=='pon' and
                          Counter(code(t) for t in g['tiles'])==consumed),None)
                if pon is None:raise ValueError('Added kan without matching pon')
                added=self.pick(seat,[event['pai']])[0]
                g=dict(kind=mapped,tiles=[*pon['tiles'],added],added=added,called=pon['called'],source=pon['source'])
            validate_groups([(mapped,tuple(code(t) for t in g['tiles']))])
            self.apply_call(seat,g)
        elif kind in ('hora','ryukyoku'):self.terminal(event)
        else:raise ValueError(f'Unsupported MJAI event: {kind}')

    def terminal(self,event):
        kind=event['type']
        if self.ends and (kind!='hora' or self.ends[-1]['kind']!='win'):
            raise ValueError('Conflicting terminal events')
        delta=four_scores(event['deltas'])
        normalized_four=False
        if (kind=='ryukyoku' and delta==[0]*4 and self.rules.abortive_draws
                and event.get('reason') in (None,'four_riichi') and len(self.ready)==3
                and len(self.declaring)==1 and self.phase=='await_response' and self.pending
                and self.pending['source_seat'] in self.declaring):
            seat=self.pending['source_seat']
            if self.scores[seat]<1000:raise ValueError('Fourth riichi has insufficient points')
            self.normalized('REACH',who=seat,step=2)
            for record in self.records:
                record.events[-1].data['fact']['normalization']='four_riichi_terminal'
            normalized_four=True
        after=[v+d for v,d in zip(self.scores,delta)]
        if 'scores' in event and four_scores(event['scores'])!=after:raise ValueError('Terminal scores disagree')
        result=dict(kind='win' if kind=='hora' else 'draw',scores_before=self.scores[:],
                    score_delta=delta,scores_after=after)
        if kind=='hora':
            seat,target=event['actor'],event['target']
            if not valid_seat(target):raise ValueError('Invalid win target')
            if not self.ends:
                if seat==target:
                    if self.phase!='await_discard' or not self.draw or self.draw['seat']!=seat:raise ValueError('Tsumo outside own draw')
                    tile=self.draw['tile']
                else:
                    if not self.pending or self.pending['source_seat']!=target:raise ValueError('Ron does not match pending response')
                    tile=int(self.pending['tile_id'][1:])
                self.win_context=(target,tile)
            target_before,tile=self.win_context
            if target!=target_before or any(r.get('winner') in (seat,target) for r in self.ends) or (self.ends and seat==target):
                raise ValueError('Inconsistent multiple ron')
            if 'pai' in event and tile_code(event['pai'])!=code(tile):raise ValueError('Winning tile mismatch')
            ura=event.get('ura_markers',event.get('uradora_markers'))
            result.update(winner=seat,source=target,win_tile=code(tile),
                          ura=UNKNOWN if ura is None else [tile_code(c) for c in ura])
            # Only explicitly supplied end-only fields; never copy arbitrary metadata.
            for key in ('yakus','fu','fan','hora_points'):
                if key in event:result[key]=deepcopy(event[key])
            self.sticks=0
        else:
            result['reason']='four_riichi' if self.rules.abortive_draws and len(self.ready)==4 and delta==[0]*4 else event.get('reason',UNKNOWN)
            if normalized_four:result['normalization']='missing_fourth_reach_accepted'
        self.ends.append(result);self.scores=after
        self.phase,self.actor,self.pending,self.draw,self.kan='ended',None,None,None,None
        self.emit('ended',result=result)


def parse_mjai(data,*,rules=None):
    """Full-information four-player archive only; source scores are not re-scored."""
    if not isinstance(rules,RuleConfig):raise ValueError('Specify RuleConfig: MJAI does not encode full rules')
    raw=data.encode('utf-8') if isinstance(data,str) else data
    source_id=sha256(raw).hexdigest();hands=[];hand=None;started=False;ended=False;last_scores=None;last_sticks=None;line_number=0
    try:
        for line_number,line in enumerate(raw.decode('utf-8-sig').splitlines(),1):
            if not line.strip():continue
            event=json.loads(line)
            if not isinstance(event,dict):raise ValueError('Each MJAI line must be an object')
            kind=event['type']
            if ended:raise ValueError('Event after end_game')
            if kind=='start_game':
                if started:raise ValueError('Duplicate start_game')
                if event.get('aka_flag',True) is not True:raise ValueError('No-red inventory unsupported')
                if 'names' in event and len(event['names'])!=4:raise ValueError('Four-player archive required')
                started=True
            elif not started:raise ValueError('Missing start_game')
            elif kind=='start_kyoku':
                if hand is not None:raise ValueError('Missing end_kyoku')
                if last_scores is not None and event['scores']!=last_scores:raise ValueError('Scores inconsistent between rounds')
                if last_sticks is not None and event['kyotaku']!=last_sticks:raise ValueError('Deposits inconsistent between rounds')
                hand=_MjaiHand(event,rules,source_id,len(hands))
            elif kind=='end_kyoku':
                if hand is None:raise ValueError('Unexpected end_kyoku')
                hands.append(hand.finish());last_scores=hand.scores[:];last_sticks=hand.sticks;hand=None
            elif kind=='end_game':
                if hand is not None or not hands:raise ValueError('Incomplete match')
                ended=True
            elif hand is None:raise ValueError('Event outside round')
            else:hand.event(event)
        if not ended:raise ValueError('Missing end_game')
    except (KeyError,TypeError,IndexError,ValueError) as error:
        raise ValueError(f'MJAI line {line_number}: {error}') from error
    return tuple(hands)


def decode_payload(raw):
    try:
        if raw.startswith(b'\x28\xb5\x2f\xfd'):
            with zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw)) as stream:return stream.read()
        if raw.startswith(b'\x1f\x8b'):return gzip.decompress(raw)
        return raw
    except (OSError,EOFError,zstandard.ZstdError) as error:
        raise ValueError(f'Invalid compressed MJAI payload: {error}') from error


def load_mjai(path,*,rules=None):
    return parse_mjai(decode_payload(Path(path).read_bytes()),rules=rules)


def iter_archive(path,*,limit=None,offset=0):
    """Yield one compressed game at a time, without extracting tar paths to disk."""
    if limit is not None and (type(limit) is not int or limit<1):raise ValueError('limit must be positive')
    if type(offset) is not int or offset<0:raise ValueError('offset must be nonnegative')
    if not tarfile.is_tarfile(path):
        if offset:raise ValueError('offset requires tar archive')
        yield Path(path).name,decode_payload(Path(path).read_bytes());return
    count=0
    with tarfile.open(path,'r:*') as archive:
        for member in archive:
            if not member.isfile() or not member.name.endswith(('.mjai.json.zst','.mjson','.jsonl','.mjai.json','.json.gz',
                                                              '.jsonl.gz','.mjson.gz','.jsonl.zst','.mjson.zst')):continue
            count+=1
            if count<=offset:continue
            try:
                with archive.extractfile(member) as stream:raw=decode_payload(stream.read())
            except ValueError as error:raise ValueError(f'{member.name}: {error}') from error
            yield member.name,raw
            if limit is not None and count-offset>=limit:break


def export_mjai(source,destination,*,rules,limit=None,offset=0,samples_per_game=None,records=False,verified_profile=None,progress=None):
    if verified_profile not in (None,TENHOU_HANCHAN):raise ValueError('Unsupported verified profile')
    if verified_profile and rules!=tenhou_hanchan():raise ValueError('Rule configuration differs from target profile')
    if samples_per_game is not None and (type(samples_per_game) is not int or samples_per_game<1):
        raise ValueError('samples_per_game must be positive')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    report=dict(schema_version='mjai-import-1',status='incomplete',source=str(Path(source).resolve()),
                selection=dict(offset=offset,limit=limit,samples_per_game=samples_per_game),
                rules=rules.__dict__,verified_profile=verified_profile,games=0,hands=0,decisions=0,samples=0,duplicates=0,members=[])
    seen=set();current_member=None
    try:
        with (destination/'discard-samples.jsonl').open('w',encoding='utf-8') as stream:
            for name,raw in iter_archive(source,limit=limit,offset=offset):
                current_member=name
                if verified_profile:
                    match=re.fullmatch(r'(\d{4})\d{6}gm-00a9-0000-[0-9a-f]{8}\.mjai\.json(?:\.zst)?',Path(name).name)
                    if not match or int(match[1])<2023:
                        raise ValueError(f'{name}: target requires 2023+ Tenhou 00a9 ranked source identifier')
                identity=sha256(raw).hexdigest()
                if identity in seen:report['duplicates']+=1;continue
                try:hands=parse_mjai(raw,rules=rules)
                except ValueError as error:raise ValueError(f'{name}: {error}') from error
                seen.add(identity)
                if verified_profile:
                    initial=hands[0].records[0].initial_snapshot
                    if (initial.round_wind!='east' or initial.hand_number!=1 or initial.honba!=0
                            or initial.riichi_sticks!=0 or any(p.score!=25000 for p in initial.players)):
                        raise ValueError(f'{name}: target requires complete standard match opening')
                total=sum(len(h.decisions) for h in hands)
                count=total if samples_per_game is None else min(total,samples_per_game)
                selected={i*(total-1)//max(1,count-1) for i in range(count)}
                cursor=0
                if records:(destination/identity).mkdir()
                for hand in hands:
                    if records:
                        for seat,record in enumerate(hand.records):
                            save_record(record,destination/identity/f'hand-{hand.hand_index:03d}-seat-{seat}.json')
                    decisions=tuple(d for i,d in enumerate(hand.decisions,cursor) if i in selected)
                    for sample in replace(hand,decisions=decisions).samples():
                        if verified_profile:
                            sample['rules_verification']=dict(profile=verified_profile,source_id=identity,
                                source_member=name,evidence='tenhou-ranked-00a9-2023plus-and-standard-opening')
                        stream.write(debug_json(sample,indent=None)+'\n');report['samples']+=1
                    cursor+=len(hand.decisions)
                report['games']+=1;report['hands']+=len(hands);report['decisions']+=total
                report['members'].append(dict(name=name,source_id=identity,hands=len(hands),decisions=total))
                if progress:progress(dict(stage='importing',games=report['games'],samples=report['samples']))
                current_member=None
        if not report['games']:raise ValueError('No MJAI games found')
        report['status']='complete'
    except Exception as error:
        report['error']=dict(member=current_member,message=f'{type(error).__name__}: {error}')
        raise
    finally:(destination/'import-report.json').write_text(debug_json(report),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description='Import full four-player MJAI JSONL/gzip/zstd or tar archives')
    parser.add_argument('source',type=Path);parser.add_argument('destination',type=Path)
    parser.add_argument('--profile',choices=('tenhou','custom'),required=True)
    parser.add_argument('--rules',type=Path,help='RuleConfig JSON for custom profile')
    parser.add_argument('--limit',type=int);parser.add_argument('--offset',type=int,default=0)
    parser.add_argument('--samples-per-game',type=int)
    parser.add_argument('--records',action='store_true',help='Also write four observer records per round')
    args=parser.parse_args()
    try:
        if args.profile=='custom':
            if args.rules is None:raise ValueError('Custom profile requires --rules')
            rules=RuleConfig(**json.loads(args.rules.read_text(encoding='utf-8-sig')))
        else:
            if args.rules is not None:raise ValueError('Use custom profile for --rules')
            rules=tenhou_hanchan()
        report=export_mjai(args.source,args.destination,rules=rules,limit=args.limit,offset=args.offset,
                           samples_per_game=args.samples_per_game,records=args.records,
                           verified_profile=TENHOU_HANCHAN if args.profile=='tenhou' else None)
        print(json.dumps({k:report[k] for k in ('status','games','hands','decisions','samples')},indent=2))
    except (OSError,ValueError,zstandard.ZstdError) as error:parser.exit(1,f'{error}\n')


if __name__=='__main__':main()
