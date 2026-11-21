"""akochan full-visible-record bridge; no wall or opponent hand access."""
from ..contracts import Action
from ..models import UNKNOWN
from ..tiles import index
from .external import JsonProcess


def pai(code):
    if code is UNKNOWN:return '?'
    if code.endswith('z'):return ('E','S','W','N','P','F','C')[int(code[0])-1]
    return '5'+code[1]+'r' if code[0]=='0' else code


def visible_log(record):
    p=record.initial_snapshot;seat=p.observer_seat
    hands=[['?']*13 for _ in range(4)]
    hands[seat]=[pai(t.code) for t in p.tiles if t.zone=='hand' and t.seat==seat]
    indicators=[pai(t.code) for t in p.tiles if t.zone=='dora']
    log=[dict(type='start_game',id=seat,kyoku_first=0,aka_flag=True),
         dict(type='start_kyoku',bakaze=('E','S','W','N')[('east','south','west','north').index(p.round_wind)],
              kyoku=p.hand_number,honba=p.honba,kyotaku=p.riichi_sticks,oya=p.dealer_seat,
              scores=[v.score for v in p.players],tehais=hands,dora_marker=indicators[0])]
    codes={t.id:t.code for t in p.tiles};pons={};last_discard=None;declared=False
    for event in record.events:
        kind=event.kind.removeprefix('sim_');fact=event.data['fact'];changes=event.data['changes'];actor=event.actor
        if 'tiles' in changes:
            codes.update({t['id']:t['code'] for t in changes['tiles']})
            new=[pai(t['code']) for t in changes['tiles'] if t['zone']=='dora']
        else:new=indicators
        additions=[dict(type='dora',dora_marker=c) for c in new[len(indicators):]]
        indicators=new
        # Delayed open-kan indicators appear before the following discard.
        if kind=='discard':log.extend(additions);additions=[]
        if kind=='draw':log.append(dict(type='tsumo',actor=actor,pai=pai(fact['code'])))
        elif kind=='discard':
            if fact['riichi']:log.append(dict(type='reach',actor=actor))
            last_discard=dict(type='dahai',actor=actor,pai=pai(fact['code']),tsumogiri=fact['tsumogiri'])
            log.append(last_discard)
        elif kind=='riichi_accepted':log.append(dict(type='reach_accepted',actor=actor))
        elif kind in ('chi','pon','open_kan'):
            called=pai(codes[fact['called']]) if 'called' in fact else last_discard['pai']
            source=fact.get('source',last_discard['actor'])
            consumed=[pai(c) for c in fact['codes']]
            log.append(dict(type={'open_kan':'daiminkan'}.get(kind,kind),actor=actor,target=source,pai=called,consumed=consumed))
            if kind=='pon':pons[actor,index(codes[fact['called']])]=[called,*consumed]
        elif kind=='kan_declared' and fact['kan_kind']=='added_kan':
            log.append(dict(type='kakan',actor=actor,pai=pai(fact['code']),consumed=pons[actor,index(fact['code'])]))
            declared=True
        elif kind=='closed_kan':log.append(dict(type='ankan',actor=actor,consumed=[pai(c) for c in fact['codes']]))
        elif kind=='added_kan':
            if not declared:raise ValueError('Added kan without visible declaration')
            declared=False
        # Private passes stay private and do not become MJAI public actions.
        log.extend(additions)
    return log


def translate(moves, request):
    if not isinstance(moves,list) or not moves:raise ValueError('akochan returned no move sequence')
    p=request.position;seat=p.observer_seat;move=moves[0];kind=move['type']
    if any(m.get('actor',seat)!=seat for m in moves):raise ValueError('akochan actor mismatch')
    target=p.pending['trigger_event'] if isinstance(p.pending,dict) else None
    pending_source=p.pending['source_seat'] if target is not None else None
    if 'target' in move and kind!='hora' and move['target']!=pending_source:raise ValueError('akochan target mismatch')
    if kind=='none':return Action('pass',seat,target_id=target),[]
    if kind=='hora':
        expected=seat if target is None else pending_source
        if move['target']!=expected:raise ValueError('akochan win target mismatch')
        return Action('tsumo' if target is None else 'ron',seat,target_id=target),[]
    if kind=='ryukyoku':
        if move.get('reason')!='kyushukyuhai':raise ValueError('Unsupported voluntary draw')
        return Action('abort_nine',seat),[]
    if kind=='reach':
        if len(moves)!=2 or moves[1]['type']!='dahai':raise ValueError('Missing riichi discard')
        move=moves[1];wanted=[move['pai']];action_kind='riichi'
    elif kind=='dahai':wanted=[move['pai']];action_kind='discard'
    elif kind in ('chi','pon','daiminkan','ankan','kakan'):
        wanted=[move['pai']] if kind=='kakan' else move['consumed']
        action_kind={'daiminkan':'open_kan','ankan':'closed_kan','kakan':'added_kan'}.get(kind,kind)
    else:raise ValueError('Unsupported akochan action: '+kind)
    hand=[t for t in p.tiles if t.zone=='hand' and t.seat==seat]
    if move.get('tsumogiri'):
        drawn=p.draw_context['tile_id'] if p.draw_context else None
        hand=[t for t in hand if t.id==drawn]
    selected=[]
    for code in wanted:
        tile=next((t for t in hand if t.id not in selected and pai(t.code)==code),None)
        if tile is None:raise ValueError('akochan selected unavailable tile: '+code)
        selected.append(tile.id)
    if kind in ('chi','pon','daiminkan') and pai(next(t.code for t in p.tiles if t.id==p.pending['tile_id']))!=move['pai']:
        raise ValueError('akochan called tile mismatch')
    follow=moves[1:] if kind in ('chi','pon') else []
    if follow and (len(follow)!=1 or follow[0]['type']!='dahai'):raise ValueError('Invalid post-call discard')
    return Action(action_kind,seat,tuple(selected),target if kind in ('chi','pon','daiminkan') else None),follow


class AkochanBot(JsonProcess):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.follow=None

    def decide(self, turn, record):
        p=turn.request.position
        if self.follow:
            round_id,moves=self.follow;self.follow=None
            if round_id==p.round_id and p.phase=='await_discard' and p.draw_context is None and p.actor==p.observer_seat:
                return translate(moves,turn.request)[0]
        moves=self.ask(dict(record=visible_log(record),seat=p.observer_seat))
        action,follow=translate(moves,turn.request)
        if follow:self.follow=(p.round_id,follow)
        return action
