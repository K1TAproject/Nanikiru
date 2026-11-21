"""Bounded offensive route assessment, not a forecast of winning probability."""

from mahjong.shanten import Shanten
from functools import lru_cache

from ..models import UNKNOWN
from ..tiles import counts34, index, tile34
from ..rules.scoring import WINDS


@lru_cache(maxsize=32768)
def shape_distance(counts, route):
    if route=='seven_pairs':value=Shanten.calculate_shanten_for_chiitoitsu_hand(counts)
    elif route=='orphans':value=Shanten.calculate_shanten_for_kokushi_hand(counts)
    else:value=Shanten.calculate_shanten(counts,use_chiitoitsu=False,use_kokushi=False)
    return max(0,value+1)


def route_potential(row, speed_weight=1.0, ukeire_weight=1.0):
    """Shared by runtime and offline fitting; missing inventory adds no bonus."""
    if row['han'] is None:return 0.0
    distance=row['distance']
    value=min(row['han'],4)/(1+distance)**2
    speed=speed_weight*row['speed_loss']/(1+distance)
    progress=ukeire_weight*(row['ukeire_fraction'] or 0)/(1+distance)
    return max(0.0,value-speed+progress)


def evaluate_routes(hand, groups, position, seen=UNKNOWN, *, speed_weight=1.0, ukeire_weight=1.0):
    counts = counts34(hand)
    closed = all(kind == 'closed_kan' for kind, _ in groups)
    normal = shape_distance(tuple(counts),'regular')-1
    rows = [dict(name='regular', distance=max(0,normal+1), han=None, basis='exact_shape')]
    if not groups:
        rows += [dict(name='seven_pairs',distance=shape_distance(tuple(counts),'seven_pairs'),han=2,basis='exact_shape'),
                 dict(name='orphans',distance=shape_distance(tuple(counts),'orphans'),han=13,basis='exact_shape')]
    base = max(1, normal+1)
    if closed:
        rows.append(dict(name='menzen_riichi',distance=base,han=1,basis='heuristic',
                         condition='reach_tenpai_then_meet_riichi_eligibility'))
    terminals = lambda c: index(c)>=27 or index(c)%9 in (0,8)
    if (closed or position.rules.open_tanyao) and not any(terminals(c) for _, codes in groups for c in codes):
        outside = sum(terminals(c) for c in hand)
        rows.append(dict(name='tanyao',distance=max(base,outside+1),han=1,basis='heuristic',replace_tiles=outside))
    honors = {31:1,32:1,33:1}
    if position.round_wind is not UNKNOWN: honors[27+WINDS.index(position.round_wind)] = 1
    if position.dealer_seat is not UNKNOWN:
        wind = 27+(position.observer_seat-position.dealer_seat)%4
        honors[wind] = honors.get(wind,0)+1
    fixed = counts34(c for _, codes in groups for c in codes)
    for n,han in honors.items():
        have = counts[n]+fixed[n]
        needed = max(0,3-have)
        if have<2 or (seen is not UNKNOWN and 4-seen[n]<needed): continue
        rows.append(dict(name=f'yakuhai:{n-26}z',distance=max(base,needed),han=han,basis='heuristic',
                         needed=needed,condition='complete_or_keep_value_honor_triplet'))
    if all(kind!='chi' for kind,_ in groups):
        needed_groups = 4-len(groups)
        costs=[]
        for pair in range(34):
            if fixed[pair]+2>4: continue
            triplets=sorted(max(0,3-counts[n]) for n in range(34) if n!=pair and fixed[n]+3<=4)
            costs.append(max(0,2-counts[pair])+sum(triplets[:needed_groups]))
        rows.append(dict(name='all_triplets',distance=min(costs),han=2,basis='heuristic',
                         condition='replacement_distance_ignores_future_tile_availability'))
    for suit in range(3):
        foreign=lambda c: index(c)<27 and index(c)//9!=suit
        if any(foreign(c) for _,codes in groups for c in codes): continue
        outside=sum(foreign(c) for c in hand)
        rows.append(dict(name=f'half_flush:{"mps"[suit]}',distance=max(base,outside+1),
                         han=3 if closed else 2,basis='heuristic',replace_tiles=outside))
    # Exact route-specific improvement sets for the three shape families.
    # Yaku-constrained routes retain explicit unknown improvement sets until a
    # constrained decomposition search exists; normal ukeire is not their ukeire.
    fastest=min(r['distance'] for r in rows if r['basis']=='exact_shape')
    total=None if seen is UNKNOWN else sum(4-n for n in seen)
    improvements={}
    for row in rows:
        row.update(speed_loss=max(0,row['distance']-fastest),
                   normal_distance=normal+1, effective_tiles=None, ukeire=None, ukeire_fraction=None)
        shape='regular' if row['name']=='menzen_riichi' else row['name']
        if shape not in ('regular','seven_pairs','orphans'):continue
        if shape not in improvements:
            effective=[]
            for n in range(34):
                if counts[n]+fixed[n]>=4:continue
                drawn=list(counts);drawn[n]+=1
                if shape_distance(tuple(drawn),shape)<row['distance']:effective.append(n)
            improvements[shape]=effective
        effective=improvements[shape]
        row['effective_tiles']=[tile34(n) for n in effective]
        row['ukeire']=None if total is None else sum(4-seen[n] for n in effective)
        row['ukeire_fraction']=None if total is None else (row['ukeire']/total if total else 0.0)
    # Alternative routes, not additive yaku. No implied points before completion.
    valued=[r for r in rows if r['han'] is not None]
    for row in valued:
        row['potential'] = route_potential(row,speed_weight,ukeire_weight)
    best=max(valued,key=lambda r:r['potential'],default=None)
    return dict(routes=rows, preferred=None if best is None else best['name'],
                potential=0 if best is None else best['potential'],
                scope='route_distance_and_potential_not_expected_points',
                exhaustive_yaku_search=False, feature_version='routes-v2',
                missing_fields=['route_inventory'] if total is None else [])
