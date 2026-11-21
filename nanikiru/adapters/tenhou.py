"""Local four-player red-tile Tenhou XML/mjlog reader, not a downloader.

Decode source facts rather than replaying them under simulator rules. Hidden
hands are used only to validate inventory and are never emitted to opponents.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import gzip
from hashlib import sha256
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from ..contracts import Action
from ..models import UNKNOWN, Event, PlayerInfo, RoundRecord, RoundSnapshot, TileRecord, RULE_ID, valid_seat
from ..rules.config import RuleConfig, tenhou_hanchan
from ..rules.scoring import WINDS, validate_groups
from ..tiles import tile34
from ..processing.analysis_input import build_analysis_request


def code(tile):
    if type(tile) is not int or not 0 <= tile < 136:
        raise ValueError("Tenhou tile id must be 0..135")
    return {16: "0m", 52: "0p", 88: "0s"}.get(tile, tile34(tile//4))


def decode_meld(value, actor):
    """Decode the packed m field; IDs preserve red and called-copy identity."""
    if type(value) is not int or not 0 <= value <= 65535 or not valid_seat(actor):
        raise ValueError("Invalid meld encoding")
    source = (actor + (value & 3)) % 4
    if value & 4:
        packed = value >> 10
        base = packed//3
        first = (base//7)*9 + base%7
        tiles = [4*(first+i) + ((value >> (3+2*i)) & 3) for i in range(3)]
        result = dict(kind="chi", tiles=tiles, called=tiles[packed%3], source=source)
    elif value & 24:
        omitted = (value >> 5) & 3
        packed = value >> 9
        base = (packed//3)*4
        tiles = [base+i for i in range(4) if i != omitted]
        result = dict(kind="pon" if value & 8 else "added_kan", tiles=tiles if value & 8 else [*tiles,base+omitted],
                      called=tiles[packed%3], source=source, added=base+omitted)
    elif value & 32:
        raise ValueError("Three-player north extraction is not supported")
    else:
        called = value >> 8
        result = dict(kind="closed_kan" if source == actor else "open_kan",
                      tiles=list(range(called//4*4,called//4*4+4)),
                      called=None if source == actor else called, source=None if source == actor else source)
    for t in result["tiles"]: code(t)
    if result["kind"] == "chi" and source != (actor-1)%4:
        raise ValueError("Chi source is not the preceding player")
    return result


def _ints(value):
    return [int(n) for n in value.split(",")] if value else []


@dataclass
class ImportedHand:
    source_id: str
    hand_index: int
    records: tuple[RoundRecord, ...]
    decisions: tuple[tuple[int, int, Action], ...]
    outcome: dict
    sample_schema: str = 'tenhou-discard-v1'

    def samples(self, *, method="fixed-v2", method_options=None):
        """Yield discard decisions only; outcomes are separate supervised labels."""
        for seat, cursor, action in self.decisions:
            request = build_analysis_request(self.records[seat], source_version=f"{self.source_id}:{self.hand_index}",
                                             through=cursor, method=method, method_options=method_options)
            yield dict(schema_version=self.sample_schema, source_id=self.source_id, hand_index=self.hand_index, observer=seat, event_count=cursor,
                       input=request, labels=dict(action=action, outcome=deepcopy(self.outcome)))


class _Hand:
    event_prefix = 'tenhou'
    sample_schema = 'tenhou-discard-v1'
    def __init__(self, init, rules, source_id, hand_index):
        self.source_id, self.hand_index, self.rules = source_id, hand_index, rules
        seed = _ints(init["seed"])
        if len(seed) != 6 or not 0 <= seed[0] < 16:
            raise ValueError("Unsupported INIT seed")
        self.wind, self.number = WINDS[seed[0]//4], seed[0]%4+1
        self.honba, self.sticks = seed[1:3]
        self.dealer = int(init["oya"])
        self.scores = [n*100 for n in _ints(init["ten"])]
        self.initial_total = sum(self.scores)+1000*self.sticks
        self.hands = [_ints(init[f"hai{s}"]) for s in range(4)]
        if any(len(h) != 13 for h in self.hands) or len(self.scores) != 4 or not valid_seat(self.dealer):
            raise ValueError("INIT must contain four complete 13-tile hands")
        self.rivers, self.groups, self.indicators = [[] for _ in range(4)], {}, [seed[5]]
        self.phase, self.actor, self.draw, self.pending = "await_draw", self.dealer, None, None
        self.remaining, self.kan_count = 70, 0
        self.declaring, self.ready = set(), set()
        self.declaration_cursors = {}
        self.kan = None
        self.decisions, self.ends = [], []
        self._validate_inventory()
        self.positions = [self.snapshot(s) for s in range(4)]
        self.records = [RoundRecord(deepcopy(p), coverage={"from_start":True,"gaps":[]}) for p in self.positions]

    def _validate_inventory(self):
        tiles = [*(t for h in self.hands for t in h), *self.indicators,
                 *(t for g in self.groups.values() for t in g["tiles"]),
                 *(r["tile"] for river in self.rivers for r in river if not r["called"])]
        for t in tiles: code(t)
        if len(tiles) != len(set(tiles)):
            raise ValueError("Duplicate physical tile in Tenhou state")
        if not 0 <= self.remaining <= 70 or self.kan_count > 4:
            raise ValueError("Invalid live wall/kan count")
        if self.phase!='ended':
            for seat,hand in enumerate(self.hands):
                groups=sum(g['seat']==seat for g in self.groups.values())
                extra=(self.phase=='await_discard' and self.actor==seat or self.kan is not None and self.kan['seat']==seat)
                if len(hand)!=13-3*groups+int(extra):raise ValueError('Invalid concealed hand size for event phase')

    def snapshot(self, seat):
        tiles = [TileRecord(f"t{t}",code(t),"hand",seat) for t in self.hands[seat]]
        groups = {}
        for gid,g in self.groups.items():
            groups[gid] = dict(kind=g["kind"],called_tile_id=None if g["called"] is None else f"t{g['called']}",source_seat=g["source"])
            tiles.extend(TileRecord(f"t{t}",code(t),"meld",g["seat"],gid) for t in g["tiles"])
        for s,river in enumerate(self.rivers):
            tiles.extend(TileRecord(f"t{r['tile']}",code(r['tile']),"river",s) for r in river if not r['called'])
        tiles.extend(TileRecord(f"t{t}",code(t),"dora",None) for t in self.indicators)
        if self.kan and self.kan['seat'] != seat:
            tiles.extend(TileRecord(f"t{t}",code(t),"declared",self.kan['seat']) for t in self.kan['contributed'])
        players = tuple(PlayerInfo(s,self.scores[s],0 if s==seat else len(self.hands[s])-sum(
            t.zone=='declared' and t.seat==s for t in tiles)) for s in range(4))
        draw = None if self.draw is None else dict(player=self.draw['seat'],source=self.draw['source'],
                    tile_id=f"t{self.draw['tile']}" if self.draw['seat']==seat else UNKNOWN)
        return RoundSnapshot(f"{self.source_id}:{self.hand_index}",RULE_ID,seat,players,tuple(tiles),groups,
                             self.wind,self.number,self.dealer,self.honba,self.sticks,self.phase,self.actor,
                             draw,deepcopy(self.pending),self.remaining,
                             completeness={'public_tiles':True,**{f'{k}:{s}':True for k in ('hand','groups') for s in range(4)}},rules=self.rules)

    def emit(self, kind, actor=None, **fact):
        self._validate_inventory()
        for s,record in enumerate(self.records):
            current = self.snapshot(s)
            before,after=asdict(self.positions[s]),asdict(current)
            changes={k:v for k,v in after.items() if v!=before[k]}
            visible=deepcopy(fact)
            if kind=='draw' and actor!=s: visible.update(tile=None,code=UNKNOWN)
            record.events+=(Event(len(record.events)+1,self.event_prefix+'_'+kind,actor,data={'fact':visible,'changes':changes}),)
            self.positions[s]=current

    def _commit_kan(self):
        k=self.kan
        if k is None: return
        for t in k['contributed']: self.hands[k['seat']].remove(t)
        if k['kind']=='added_kan':
            g=next(g for g in self.groups.values() if g['seat']==k['seat'] and g['kind']=='pon' and set(g['tiles'])==set(k['tiles'])-set(k['contributed']))
            g.update(kind='added_kan',tiles=k['tiles'])
        else:
            self.groups[f'g{len(self.groups)}']={key:k[key] for key in ('kind','tiles','called','source','seat')}
        self.kan=None; self.pending=None; self.draw=None
        self.kan_count+=1; self.phase='await_replacement'; self.actor=k['seat']
        self.emit(k['kind'],k['seat'],tiles=[f't{t}' for t in k['contributed']],codes=[code(t) for t in k['contributed']])

    def apply_call(self, seat, g):
        kind=g['kind']
        if not valid_seat(seat) or len(g['tiles'])!=len(set(g['tiles'])):raise ValueError('Invalid call actor/physical copies')
        validate_groups([(kind,tuple(code(t) for t in g['tiles']))])
        if self.declaring:raise ValueError('Missing riichi acceptance before call')
        if kind in ('chi','pon','open_kan'):
            if (self.phase!='await_response' or not self.pending or self.pending['source_seat']!=g['source']
                    or self.pending['tile_id']!=f"t{g['called']}"):
                raise ValueError('Call does not match latest discard')
            contributed=[t for t in g['tiles'] if t!=g['called']]
        else:
            if self.phase!='await_discard' or self.actor!=seat: raise ValueError('Kan outside own turn')
            if self.draw is None:raise ValueError('Kan requires own draw')
            contributed=g['tiles'] if kind=='closed_kan' else [g['added']]
            if kind=='added_kan' and not any(x['kind']=='pon' and x['seat']==seat and set(x['tiles'])==set(g['tiles'])-set(contributed) for x in self.groups.values()):
                raise ValueError('Added kan without matching pon')
        if not set(contributed)<=set(self.hands[seat]): raise ValueError('Call tiles absent from hand')
        if kind in ('closed_kan','added_kan'):
            self.kan={**g,'seat':seat,'contributed':contributed}
            self.phase,self.actor='await_kan_response',None
            self.pending=dict(trigger_event=len(self.records[seat].events)+1,kind=kind,source_seat=seat,
                              tile_id=f't{contributed[0]}',responses={})
            self.emit('kan_declared',seat,tiles=[f't{t}' for t in contributed],codes=[code(t) for t in contributed],kan_kind=kind)
        else:
            for t in contributed:self.hands[seat].remove(t)
            next(r for r in self.rivers[g['source']] if r['tile']==g['called'])['called']=True
            self.groups[f'g{len(self.groups)}']={**{k:g[k] for k in ('kind','tiles','called','source')}, 'seat':seat}
            self.actor,self.pending,self.draw=seat,None,None
            self.phase='await_replacement' if kind=='open_kan' else 'await_discard'
            if kind=='open_kan':self.kan_count+=1
            self.emit(kind,seat,tiles=[f't{t}' for t in contributed],codes=[code(t) for t in contributed],called=f"t{g['called']}",source=g['source'])

    def apply(self, node):
        tag,a=node.tag,node.attrib
        if self.ends and tag!='AGARI':raise ValueError('Action after terminal event')
        match=re.fullmatch(r'([TUVWDEFGtuvwdefg])(\d+)',tag)
        if match:
            letter,tile=match[1].upper(),int(match[2]); code(tile)
            if letter in 'TUVW':
                if self.declaring:raise ValueError('Missing riichi acceptance before next draw')
                seat='TUVW'.index(letter)
                self._commit_kan()
                replacement=self.phase=='await_replacement'
                if self.phase=='ended' or replacement and seat!=self.actor:
                    raise ValueError('Draw after end or wrong replacement player')
                expected=(self.pending['source_seat']+1)%4 if self.pending else self.actor
                if not replacement and (self.phase not in ('await_draw','await_response') or seat!=expected):
                    raise ValueError('Unexpected draw turn')
                self.hands[seat].append(tile); self.remaining-=1
                self.draw=dict(seat=seat,tile=tile,source='replacement' if replacement else 'live')
                self.phase,self.actor,self.pending='await_discard',seat,None
                self.emit('draw',seat,tile=f't{tile}',code=code(tile),source=self.draw['source'])
            else:
                seat='DEFG'.index(letter)
                if self.phase!='await_discard' or self.actor!=seat or tile not in self.hands[seat]:
                    raise ValueError('Discard is not in acting player hand')
                action=Action('riichi' if seat in self.declaring else 'discard',seat,(f't{tile}',))
                # Predict the whole declaration-discard choice before its intent is public.
                cursor=self.declaration_cursors.get(seat,len(self.records[seat].events))
                self.decisions.append((seat,cursor,action))
                drawn=bool(self.draw and self.draw['tile']==tile)
                self.hands[seat].remove(tile); self.rivers[seat].append(dict(tile=tile,called=False))
                self.draw=None; self.phase,self.actor='await_response',None
                self.pending=dict(trigger_event=len(self.records[seat].events)+1,kind='discard',source_seat=seat,tile_id=f't{tile}',responses={})
                self.emit('discard',seat,tile=f't{tile}',code=code(tile),tsumogiri=drawn,riichi=seat in self.declaring)
        elif tag=='REACH':
            seat=int(a['who']); step=int(a['step'])
            if not valid_seat(seat): raise ValueError('Invalid REACH seat')
            if step==1:
                if self.actor!=seat or self.phase!='await_discard' or seat in self.ready or seat in self.declaring: raise ValueError('Unexpected riichi declaration')
                self.declaration_cursors[seat]=len(self.records[seat].events)
                self.declaring.add(seat); self.emit('riichi_declared',seat)
            elif step==2 and seat in self.declaring:
                if self.phase!='await_response' or self.pending['source_seat']!=seat:
                    raise ValueError('Riichi acceptance must follow its discard')
                if self.scores[seat]<1000:raise ValueError('Riichi has insufficient points')
                del self.declaration_cursors[seat]
                self.declaring.remove(seat);self.ready.add(seat);self.scores[seat]-=1000;self.sticks+=1
                self.emit('riichi_accepted',seat,double=len(self.rivers[seat])==1 and not self.groups and self.kan is None)
            else: raise ValueError('Invalid REACH sequence')
        elif tag=='DORA':
            self._commit_kan()
            if len(self.indicators)>=self.kan_count+1:raise ValueError('Dora reveal without a corresponding kan')
            tile=int(a['hai']);code(tile);self.indicators.append(tile)
            if len(self.indicators)>5: raise ValueError('Too many indicators')
            self.emit('dora',tile=f't{tile}',code=code(tile))
        elif tag=='N':
            seat=int(a['who'])
            self.apply_call(seat,decode_meld(int(a['m']),seat))
        elif tag in ('AGARI','RYUUKYOKU'):
            if self.ends and tag!='AGARI':raise ValueError('Unexpected second terminal event')
            if self.ends and any(r['kind']!='win' for r in self.ends):raise ValueError('Win after draw result')
            result={'kind':'win' if tag=='AGARI' else a.get('type','exhaustive')}
            if 'sc' not in a:raise ValueError('Complete log requires terminal score transfers')
            if (tag=='RYUUKYOKU' and a.get('type')=='reach4' and len(self.ready)==3 and len(self.declaring)==1
                    and self.phase=='await_response' and self.pending['source_seat'] in self.declaring):
                seat=self.pending['source_seat']
                self.apply(ET.Element('REACH',{'who':str(seat),'step':'2'}))
                result['normalization']='missing_fourth_reach_accepted'
            if tag=='RYUUKYOKU':
                result['revealed_hands']={s:[code(t) for t in _ints(a[f'hai{s}'])] for s in range(4) if f'hai{s}' in a}
            if 'sc' in a:
                sc=_ints(a['sc'])
                if len(sc)!=8:raise ValueError('Invalid terminal scores')
                if [x*100 for x in sc[::2]]!=self.scores:raise ValueError('Terminal score baseline disagrees with events')
                result['scores_before']=[x*100 for x in sc[::2]]
                result['score_delta']=[x*100 for x in sc[1::2]]
                self.scores=[(sc[i]+sc[i+1])*100 for i in range(0,8,2)]
                result['scores_after']=self.scores[:]
            if tag=='AGARI':
                who,source=int(a['who']),int(a['fromWho'])
                if not valid_seat(who) or not valid_seat(source):raise ValueError('Invalid win seats')
                tile=int(a['machi']);code(tile)
                if not self.ends:
                    if who==source:
                        if not self.draw or self.draw['seat']!=who or self.draw['tile']!=tile:raise ValueError('Tsumo differs from own draw')
                    elif not self.pending or self.pending['source_seat']!=source or self.pending['tile_id']!=f't{tile}':
                        raise ValueError('Ron differs from pending tile/source')
                elif (who==source or any(r['winner'] in (who,source) for r in self.ends)
                      or source!=self.ends[0]['source'] or code(tile)!=self.ends[0]['win_tile']):
                    raise ValueError('Inconsistent multiple ron')
                result.update(winner=who,source=source,win_tile=code(int(a['machi'])),
                              hand=[code(t) for t in _ints(a.get('hai',''))],
                              ten=_ints(a.get('ten','')),yaku=_ints(a.get('yaku','')),yakuman=_ints(a.get('yakuman','')),
                              ura=[code(t) for t in _ints(a.get('doraHaiUra',''))])
                self.sticks=0
            self.ends.append(result)
            self.phase,self.actor,self.pending,self.draw='ended',None,None,None
            self.kan=None
            self.emit('ended',result=result)
        else:
            raise ValueError(f'Unsupported hand event: {tag}')

    def finish(self):
        if not self.ends:raise ValueError('Incomplete hand: no terminal event')
        if sum(self.scores)+1000*self.sticks!=self.initial_total:raise ValueError('Scores plus riichi deposits are not conserved')
        return ImportedHand(self.source_id,self.hand_index,tuple(self.records),tuple(self.decisions),{'settlements':deepcopy(self.ends)},self.sample_schema)


def parse_xml(data, *, rules=None):
    """Return ImportedHand objects; fail explicitly on unsupported log variants."""
    raw=data.encode('utf-8') if isinstance(data,str) else data
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML declarations are not supported')
    try:
        root=ET.fromstring(raw)
        if root.tag!='mjloggm':raise ValueError('Expected Tenhou mjloggm XML')
        source_id=sha256(raw).hexdigest()
        output=[];current=None;source_rules=rules
        for node in root:
            if node.tag=='GO':
                mode=int(node.attrib['type'])
                if mode & 16:raise ValueError('Three-player logs are not supported')
                if mode & 2:raise ValueError('No-red logs require a different physical inventory profile')
                if mode & ~255:raise ValueError('Unsupported Tenhou game mode flags')
                if source_rules is None:
                    source_rules=replace(tenhou_hanchan(),open_tanyao=not bool(mode & 4),
                                         match_length='south' if mode & 8 else 'east')
            elif node.tag=='INIT':
                if current:
                    output.append(current.finish())
                    if [n*100 for n in _ints(node.attrib['ten'])]!=current.scores:raise ValueError('Scores inconsistent between rounds')
                    if _ints(node.attrib['seed'])[2]!=current.sticks:raise ValueError('Deposits inconsistent between rounds')
                if source_rules is None:raise ValueError('GO or explicit source rules required')
                current=_Hand(node.attrib,source_rules,source_id,len(output))
            elif node.tag in ('SHUFFLE','UN','TAIKYOKU','BYE'):continue
            elif current:current.apply(node)
            else:raise ValueError(f'Unexpected event before INIT: {node.tag}')
        if current:output.append(current.finish())
        if not output:raise ValueError('No complete hands in log')
        return tuple(output)
    except (KeyError,TypeError,IndexError,ET.ParseError,StopIteration) as error:
        raise ValueError(f'Malformed Tenhou log: {error}') from error


def load_tenhou(path, *, rules=None):
    raw=Path(path).read_bytes()
    if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
    return parse_xml(raw,rules=rules)
