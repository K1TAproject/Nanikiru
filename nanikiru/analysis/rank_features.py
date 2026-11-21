"""Versioned, label-free features for a shared A-rule continuation value."""

from mahjong.shanten import Shanten

from ..models import UNKNOWN
from ..rules.config import tenhou_hanchan
from ..rules.observed_facts import complete_history, riichi_status, event_fact
from ..rules.scoring import waits, evaluate
from ..tiles import counts34, index, tile34
from .efficiency import hand_codes, meld_codes, analyze_shape, visible_counts
from .fixed_policy import _bonus

FEATURE_VERSION = 'rank-visible-features-1'
PHASES = ('await_draw', 'await_discard', 'await_response', 'await_kan_response',
          'await_replacement', 'ended')
BASE_NAMES = (
    'score_0', *(f'gap_{i}' for i in range(1,4)),
    'rank', 'round_index', 'honba', 'sticks', 'remaining', 'dealer_relative',
    'tie_order', 'self_turn', 'history_complete', 'public_complete',
    *(f'riichi_{i}' for i in range(4)), *(f'melds_{i}' for i in range(4)),
    *(f'river_count_{i}' for i in range(4)),
    'closed', 'regular_shanten', 'pairs_shanten', 'orphans_shanten',
    'ukeire_fraction', 'effective_types', 'dora_count', 'red_count',
    'base_ron_min', 'base_ron_max', 'discard_furiten', 'temporary_furiten', 'riichi_furiten',
    'safe_tiles_all_riichi', 'pressure',
    'gap_to_top_x_round', 'gap_to_last_x_round', 'shanten_x_pressure',
    *(f'phase_{p}' for p in PHASES),
    *(f'hand_{i}' for i in range(34)),
)
# Each scalar has an explicit missing/not-applicable channel. Zeros are values.
FEATURE_NAMES = tuple(x for name in BASE_NAMES for x in (name, name+'_missing'))


def extract_features(request):
    """Only the observer request enters here; source identity/outcomes are ignored.

Fixed physical units, no dataset-fitted normalization or hidden future state.
Shape features of 14-equivalent hands describe the current hand, not a chosen
discard. Ukeire is only defined for 13-equivalent hands in this first version.
"""
    request.__post_init__()
    p=request.position
    if p.rules!=tenhou_hanchan():raise ValueError('Shared rank model requires verified A-rule configuration')
    seat=p.observer_seat
    values={name:None for name in BASE_NAMES}
    scores={v.seat:v.score for v in p.players}
    ordered=[scores[(seat+i)%4] for i in range(4)]
    known=all(s is not UNKNOWN for s in ordered)
    values['score_0']=None if ordered[0] is UNKNOWN else ordered[0]/30000
    for i in range(1,4):
        values[f'gap_{i}']=None if ordered[0] is UNKNOWN or ordered[i] is UNKNOWN else (ordered[0]-ordered[i])/30000
    if p.dealer_seat is not UNKNOWN:
        values['dealer_relative']=((p.dealer_seat-seat)%4)/3
        if p.hand_number is not UNKNOWN:
            initial=(p.dealer_seat-p.hand_number+1)%4
            values['tie_order']=((seat-initial)%4)/3
            if known:
                ranks=sorted(range(4),key=lambda s:(-scores[s],(s-initial)%4))
                values['rank']=(ranks.index(seat)+1)/4
    if p.round_wind is not UNKNOWN and p.hand_number is not UNKNOWN:
        values['round_index']=(('east','south','west','north').index(p.round_wind)*4+p.hand_number-1)/11
    for name,field,scale in (('honba',p.honba,10),('sticks',p.riichi_sticks,10),('remaining',p.remaining_draws,70)):
        values[name]=None if field is UNKNOWN else field/scale
    values['self_turn']=None if p.actor is UNKNOWN else float(p.actor==seat)
    values['history_complete']=float(complete_history(request.history,request.coverage))
    values['public_complete']=float(p.completeness.get('public_tiles') is True)
    ready=[]
    for i in range(4):
        other=(seat+i)%4
        flag=riichi_status(p,request.history,other,request.coverage);ready.append(flag)
        values[f'riichi_{i}']=None if flag is UNKNOWN else float(flag)
        if p.completeness.get(f'groups:{other}') is True:
            values[f'melds_{i}']=len({t.group_id for t in p.tiles if t.zone=='meld' and t.seat==other})/4
        if p.completeness.get('public_tiles') is True:
            values[f'river_count_{i}']=sum(t.zone=='river' and t.seat==other for t in p.tiles)/24
    if complete_history(request.history,request.coverage) and all(e.kind.startswith('sim_') for e in request.history):
        last_draw=max((e.id for e in request.history if e.actor==seat and event_fact(e)[0]=='draw'),default=0)
        reach=next((e.id for e in request.history if e.actor==seat and event_fact(e)[0]=='riichi_accepted'),None)
        missed=[e.id for e in request.history if e.actor==seat and event_fact(e)[0]=='pass' and event_fact(e)[1].get('missed') is True]
        values['temporary_furiten']=float(any(i>last_draw for i in missed))
        values['riichi_furiten']=float(reach is not None and any(i>reach for i in missed))
    for phase in PHASES:values[f'phase_{phase}']=None if p.phase is UNKNOWN else float(p.phase==phase)
    hand,groups=hand_codes(p),meld_codes(p)
    if hand is not UNKNOWN:
        for i,n in enumerate(counts34(hand)):values[f'hand_{i}']=n/4
    if hand is not UNKNOWN and groups is not UNKNOWN:
        values['closed']=float(all(k=='closed_kan' for k,_ in groups))
        held=[*hand,*(c for _,codes in groups for c in codes)]
        values['red_count']=sum(c[0]=='0' for c in held)/3
        indicators=[t.code for t in p.tiles if t.zone=='dora']
        if indicators and UNKNOWN not in indicators and p.completeness.get('public_tiles') is True:
            # Red identity has its own feature; dora_count counts indicator dora.
            values['dora_count']=sum(_bonus(c,indicators,p.rules)-int(c[0]=='0') for c in held)/8
        if p.phase!='ended' and len(hand) in (13-3*len(groups),14-3*len(groups)):
            counts=counts34(hand)
            values['regular_shanten']=Shanten.calculate_shanten_for_regular_hand(counts)/8
            if not groups:
                values['pairs_shanten']=Shanten.calculate_shanten_for_chiitoitsu_hand(counts)/8
                values['orphans_shanten']=Shanten.calculate_shanten_for_kokushi_hand(counts)/13
            if len(hand)==13-3*len(groups):
                seen=visible_counts(p);shape=analyze_shape(hand,groups,seen)
                values['effective_types']=len(shape['effective_tiles'])/34
                if seen is not UNKNOWN:
                    unseen=136-sum(seen)
                    values['ukeire_fraction']=shape['ukeire']/unseen if unseen else 0.0
                waiting=waits(hand,groups,formal=True)
                river=[t.code for t in p.tiles if t.zone=='river' and t.seat==seat]
                if p.completeness.get('public_tiles') is True and UNKNOWN not in river:
                    # Called-away discards remain in history and still cause furiten.
                    discarded={index(c) for c in river}
                    facts=[event_fact(e)[1].get('code',UNKNOWN) for e in request.history
                           if e.actor==seat and event_fact(e)[0]=='discard']
                    discarded.update(index(c) for c in facts if c is not UNKNOWN)
                    if waiting & discarded:values['discard_furiten']=1.0
                    elif complete_history(request.history,request.coverage) and UNKNOWN not in facts:
                        values['discard_furiten']=0.0
                if (waiting and ready[0] is not UNKNOWN and p.dealer_seat is not UNKNOWN
                        and p.round_wind is not UNKNOWN and values['dora_count'] is not None):
                    costs=[]
                    for n in waiting:
                        if counts34(held)[n]>=4:continue
                        win_tile=tile34(n)
                        if win_tile[0]=='5' and held.count(win_tile)==3:win_tile='0'+win_tile[1]
                        score=evaluate(hand,groups,win_tile,seat=seat,dealer=p.dealer_seat,
                                       round_wind=p.round_wind,rules=p.rules,indicators=indicators,riichi=ready[0])
                        costs.append(0 if score is None else score['cost']['main']/32000)
                    if costs:values['base_ron_min'],values['base_ron_max']=min(costs),max(costs)
        if all(r is not UNKNOWN for r in ready[1:]) and p.completeness.get('public_tiles') is True:
            threats=[(seat+i)%4 for i in range(1,4) if ready[i] is True]
            values['pressure']=len(threats)/3
            rivers=[{index(t.code) for t in p.tiles if t.zone=='river' and t.seat==s and t.code is not UNKNOWN} for s in threats]
            if threats and not any(t.code is UNKNOWN for t in p.tiles if t.zone=='river' and t.seat in threats):
                values['safe_tiles_all_riichi']=sum(all(index(c) in r for r in rivers) for c in hand)/14
    if known and values['round_index'] is not None:
        values['gap_to_top_x_round']=(ordered[0]-max(ordered))/30000*values['round_index']
        values['gap_to_last_x_round']=(ordered[0]-min(ordered))/30000*values['round_index']
    if values['regular_shanten'] is not None and values['pressure'] is not None:
        values['shanten_x_pressure']=values['regular_shanten']*values['pressure']
    return tuple(v for name in BASE_NAMES for v in
                 ((0.0,1.0) if values[name] is None else (float(values[name]),0.0)))
