"""Candidate permissions from observer facts; never consult a simulator."""

from itertools import combinations

from ..contracts import Action, CandidateResult, Metric
from ..models import UNKNOWN
from ..tiles import index
from .call_rules import call_banned
from .discards import observed_discards
from .observed_facts import complete_history, event_fact, riichi_status, interrupts_ippatsu
from .scoring import evaluate, waits

CALLS = {'chi','pon','open_kan','closed_kan','added_kan','kan_declared'}
TERMINALS = {0,8,9,17,18,26,27,28,29,30,31,32,33}


def ron_furiten(request, waiting):
    p, seat = request.position, request.position.observer_seat
    river = {index(t.code) for t in p.tiles if t.zone=='river' and t.seat==seat and t.code is not UNKNOWN}
    for e in request.history:
        kind,fact=event_fact(e)
        if kind=='discard' and e.actor==seat and fact.get('code',UNKNOWN) is not UNKNOWN:
            river.add(index(fact['code']))
    if waiting & river: return True
    if any(t.code is UNKNOWN for t in p.tiles if t.zone=='river' and t.seat==seat): return UNKNOWN
    if any(e.actor==seat and event_fact(e)[0]=='discard' and event_fact(e)[1].get('code',UNKNOWN) is UNKNOWN for e in request.history): return UNKNOWN
    # Only simulator observations currently prove complete private pass coverage.
    if not complete_history(request.history,request.coverage) or not all(e.kind.startswith('sim_') for e in request.history):
        return UNKNOWN
    draw=max((e.id for e in request.history if e.actor==seat and event_fact(e)[0]=='draw'),default=0)
    ready=next((e.id for e in request.history if e.actor==seat and event_fact(e)[0]=='riichi_accepted'),None)
    return any(event_fact(e)[0]=='pass' and e.actor==seat and event_fact(e)[1].get('missed') is True
               and (e.id>draw or ready and e.id>ready) for e in request.history)


def win_candidate(request, hand, groups, tile, *, tsumo, target=None, rob=None):
    p, seat=request.position,request.position.observer_seat
    concealed=list(hand)
    if tsumo: concealed.remove(tile.code)
    waiting=waits(concealed,groups,formal=p.rules.formal_dead_waits)
    if index(tile.code) not in waiting: return None
    if rob=='closed_kan' and (not p.rules.kokushi_ankan_ron or groups or {index(c) for c in [*concealed,tile.code]}!=TERMINALS):return None
    ready=riichi_status(p,request.history,seat,request.coverage)
    known=complete_history(request.history,request.coverage)
    events=[(e,event_fact(e)[0],event_fact(e)[1]) for e in request.history]
    accepted=next((e for e,k,_ in events if e.actor==seat and k=='riichi_accepted'),None)
    first=known and not any(k in CALLS or k=='discard' and e.actor==seat for e,k,_ in events)
    replacement=bool(tsumo and isinstance(p.draw_context,dict) and p.draw_context['source']=='replacement')
    flags=dict(is_rinshan=replacement,is_chankan=rob is not None,
               is_haitei=tsumo and p.remaining_draws==0 and not replacement,
               is_houtei=not tsumo and p.remaining_draws==0 and rob is None,
               is_tenhou=bool(tsumo and first and seat==p.dealer_seat),
               is_chiihou=bool(tsumo and first and seat!=p.dealer_seat),
               is_ippatsu=bool(known and accepted and not any(e.id>accepted.id and
                   (interrupts_ippatsu(k,f,p.rules) or k=='discard' and e.actor==seat) for e,k,f in events)),
               is_daburu_riichi=bool(accepted and event_fact(accepted)[1].get('double') is True))
    indicators=[t.code for t in p.tiles if t.zone=='dora']
    score=None
    score_known=p.dealer_seat is not UNKNOWN and p.round_wind is not UNKNOWN
    if score_known:
        score=evaluate(concealed,groups,tile.code,seat=seat,dealer=p.dealer_seat,round_wind=p.round_wind,
                       tsumo=tsumo,riichi=ready is True,indicators=[c for c in indicators if c is not UNKNOWN],
                       rules=p.rules,**flags)
    restriction=False if tsumo else ron_furiten(request,waiting)
    legality='forbidden' if restriction is True else 'allowed' if score and restriction is False else 'unknown'
    if score_known and score is None and known and ready is not UNKNOWN:legality='forbidden'
    metric=Metric('conditional',score,'exact',{'scope':'confirmed_conditions_no_ura_not_final_settlement'}) if score else Metric('unavailable')
    return CandidateResult(Action('tsumo' if tsumo else 'ron',seat,target_id=target),legality,
                           {'win_score':metric},('Furiten or scoring context may be incomplete',))


def additional_actions(request):
    """Riichi/calls/kans/wins/pass. Discard shapes are supplied by analysis."""
    p,seat=request.position,request.position.observer_seat
    tiles=[t for t in p.tiles if t.zone=='hand' and t.seat==seat]
    if (p.completeness.get(f'hand:{seat}') is not True or p.completeness.get(f'groups:{seat}') is not True
            or next(v for v in p.players if v.seat==seat).untracked_hand_count!=0 or any(t.code is UNKNOWN for t in tiles)):
        return ()
    groups=[(g['kind'],tuple(t.code for t in p.tiles if t.group_id==gid)) for gid,g in p.groups.items()
            if any(t.group_id==gid and t.seat==seat for t in p.tiles)]
    if any(UNKNOWN in codes for _,codes in groups):return ()
    hand=[t.code for t in tiles]
    ready=riichi_status(p,request.history,seat,request.coverage)
    alive=UNKNOWN if p.remaining_draws is UNKNOWN else p.remaining_draws>0
    kan_count=sum('kan' in g['kind'] for g in p.groups.values())
    kan_room=(UNKNOWN if any(p.completeness.get(f'groups:{s}') is not True for s in range(4)) else kan_count<4)
    rows=[]

    def add(kind,ids=(),target=None,conditions=()):
        status='forbidden' if any(c is False for c in conditions) else 'unknown' if UNKNOWN in conditions else 'allowed'
        if status!='forbidden': rows.append(CandidateResult(Action(kind,seat,tuple(ids),target),status,
            reasons=('Permissions from visible facts; response can lose priority to another player',)))

    if p.phase=='await_discard' and p.actor==seat:
        allowed=observed_discards(p,request.history,request.coverage)
        closed=all(k=='closed_kan' for k,_ in groups)
        score=next(v.score for v in p.players if v.seat==seat)
        for tile in tiles:
            after=[t.code for t in tiles if t.id!=tile.id]
            if waits(after,groups,formal=p.rules.formal_dead_waits):
                add('riichi',(tile.id,),conditions=(UNKNOWN if allowed is UNKNOWN else tile.id in allowed,
                    UNKNOWN if ready is UNKNOWN else not ready,closed,
                    UNKNOWN if score is UNKNOWN else score>=1000,
                    UNKNOWN if p.remaining_draws is UNKNOWN else p.remaining_draws>=4))
        drawn=p.draw_context.get('tile_id') if isinstance(p.draw_context,dict) else None
        tile=next((t for t in tiles if t.id==drawn),None)
        if tile:
            if p.rules.abortive_draws and len({index(c) for c in hand} & TERMINALS)>=9:
                interrupted=any(event_fact(e)[0] in CALLS or
                    event_fact(e)[0]=='discard' and e.actor==seat for e in request.history)
                own_river=any(t.zone=='river' and t.seat==seat for t in p.tiles)
                first=False if interrupted or own_river or groups else (
                    True if complete_history(request.history,request.coverage) else UNKNOWN)
                add('abort_nine',conditions=(first,))
            win=win_candidate(request,hand,groups,tile,tsumo=True)
            if win: rows.append(win)
            for n in range(34):
                same=[t for t in tiles if index(t.code)==n]
                if len(same)==4:
                    permitted=UNKNOWN if ready is UNKNOWN else not ready or (drawn in [t.id for t in same] and
                        waits([t.code for t in tiles if t.id!=drawn],groups,formal=p.rules.formal_dead_waits)==waits([t.code for t in tiles if t not in same],
                            [*groups,('closed_kan',tuple(t.code for t in same))],formal=p.rules.formal_dead_waits))
                    add('closed_kan',[t.id for t in same],conditions=(alive,kan_room,permitted))
            for kind,codes in groups:
                if kind=='pon':
                    for t in tiles:
                        if index(t.code)==index(codes[0]):
                            add('added_kan',(t.id,),conditions=(alive,kan_room,UNKNOWN if ready is UNKNOWN else not ready))
    elif p.phase in ('await_response','await_kan_response') and isinstance(p.pending,dict):
        pending=p.pending
        source=pending['source_seat'];target=pending['trigger_event']
        if type(source) is not int or source==seat or seat in pending['responses'] or type(target) is not int:return ()
        tile=next((t for t in p.tiles if t.id==pending['tile_id']),None)
        if tile is None or tile.code is UNKNOWN:return ()
        win=win_candidate(request,hand,groups,tile,tsumo=False,target=target,
                          rob=None if pending['kind']=='discard' else pending['kind'])
        if win: rows.append(win)
        if pending['kind']=='discard':
            same=[t for t in tiles if index(t.code)==index(tile.code)]
            options=[('pon',pair) for pair in combinations(same,2)]
            if len(same)==3:add('open_kan',[t.id for t in same],target,(alive,kan_room,UNKNOWN if ready is UNKNOWN else not ready))
            if type(source) is int and seat==(source+1)%4 and index(tile.code)<27:
                for pair in combinations(tiles,2):
                    ids=sorted([index(tile.code),*(index(t.code) for t in pair)])
                    if ids[0]//9==ids[-1]//9 and ids==list(range(ids[0],ids[0]+3)):options.append(('chi',pair))
            for kind,pair in options:
                banned=call_banned(kind,[t.code for t in pair],tile.code) if p.rules.forbid_kuikae else set()
                if any(t not in pair and index(t.code) not in banned for t in tiles):
                    add(kind,[t.id for t in pair],target,(alive,UNKNOWN if ready is UNKNOWN else not ready))
        # A source may auto-skip a player with no legal response. Do not offer an
        # executable pass unless some actual response option is confirmed.
        add('pass',target=target,conditions=(True if any(c.legality=='allowed' for c in rows) else UNKNOWN,))
    return tuple(rows)
