"""Compare visible action branches on the same v2 heuristic score scale."""

from copy import deepcopy
from dataclasses import replace

from ..contracts import CandidateResult, Metric
from ..models import UNKNOWN
from ..rules.call_rules import call_banned
from ..rules.visible_actions import additional_actions
from ..tiles import index
from .basic import analyze_discard_shapes
from .efficiency import analyze_shape, hand_codes, meld_codes, visible_counts
from .fixed_policy import Parameters, danger, evaluate_candidates


def standing_candidate(request, candidate):
    p=request.position
    shape=analyze_shape(hand_codes(p),meld_codes(p),visible_counts(p))
    metrics={key:Metric('unavailable') if value is UNKNOWN else Metric('available',value,'exact')
             for key,value in shape.items()}
    return replace(candidate,metrics=metrics)


def post_call(request, action):
    result=deepcopy(request);p=result.position;seat=p.observer_seat
    contributed=set(action.tile_ids)
    called=p.pending['tile_id'] if action.kind in ('chi','pon','open_kan') else None
    source=p.pending['source_seat'] if called else None
    gid='hypothetical-call'
    while gid in p.groups:gid+='-'
    if action.kind=='added_kan':
        code=next(t.code for t in p.tiles if t.id in contributed)
        gid=next(g for g,v in p.groups.items() if v['kind']=='pon' and any(
            t.group_id==g and t.seat==seat and index(t.code)==index(code) for t in p.tiles))
        p.groups[gid]['kind']='added_kan'
    else:
        p.groups[gid]=dict(kind=action.kind,called_tile_id=called,source_seat=source)
    p.tiles=tuple(replace(t,zone='meld',seat=seat,group_id=gid) if t.id in contributed or t.id==called else t for t in p.tiles)
    p.draw_context=None;p.pending=None;p.phase='await_discard' if action.kind in ('chi','pon') else 'await_replacement';p.actor=seat
    p.validate()
    return result


def with_cost(candidate, cost, details):
    metrics=deepcopy(candidate.metrics)
    metric=metrics['policy_score']
    metric.details['contributions']['commitment']=-cost
    metric.value-=cost
    metrics['action_evaluation']=Metric('conditional',details,'heuristic')
    return replace(candidate,metrics=metrics)


def rank_actions(candidates, strategy):
    tiers={}
    for c in candidates:
        score=c.metrics.get('policy_score')
        if c.legality=='forbidden' or score is None or score.value is None:continue
        terms=score.details.get('contributions',{})
        value=score.value
        if strategy=='attack':value=sum(v for k,v in terms.items() if k not in ('risk','reserve'))
        elif strategy=='defense':value=-c.metrics.get('danger',Metric('available',0,'heuristic')).value
        key=(c.action.kind in ('ron','tsumo'),round(value,9))
        tiers.setdefault(key,[]).append(c.action)
    return Metric('conditional',tuple(tuple(tiers[k]) for k in sorted(tiers,reverse=True)),'heuristic',
                  {'scope':'action_preference_not_expected_net_points','strategy':strategy}) if tiers else Metric('unavailable')


def evaluate_actions(request, discards):
    weights=Parameters(**request.method_options)
    extra=additional_actions(request)
    evaluated=list(discards)
    for candidate in extra:
        action=candidate.action
        if action.kind=='abort_nine':
            evaluated.append(replace(candidate,metrics={
                'policy_score':Metric('unavailable',details={'reason':'round_continuation_value_required'}),
                'action_evaluation':Metric('conditional',{
                    'outcome':'nine_terminals','dealer_repeats':True,
                    'assumption':'legal_declaration_then_rule_settlement'},'exact')}))
            continue
        if action.kind in ('tsumo','ron'):
            score=candidate.metrics['win_score'].value
            points=0 if not score else score['cost']['main']+(2*score['cost']['additional'] if action.kind=='tsumo' else 0)
            metrics={**candidate.metrics,'policy_score':Metric('conditional',1000+points/8000,'heuristic',
                      {'contributions':{'win':1000+points/8000},'terminal_priority':True})}
            evaluated.append(replace(candidate,metrics=metrics));continue
        if action.kind=='riichi':
            base=next((c for c in discards if c.action.tile_ids==action.tile_ids),None)
            if base is None:continue
            row=replace(base,action=action,legality=candidate.legality,reasons=candidate.reasons)
            row=evaluate_candidates(request,(row,),assume_riichi=True)[0][0]
            code=next(t.code for t in request.position.tiles if t.id==action.tile_ids[0])
            threat=max(r['threat'] for r in danger(request.position,request.history,code,request.coverage)[1])
            row=with_cost(row,weights.value/8+weights.commitment*threat,
                          {'assumption':'riichi_accepted_no_ura_or_ippatsu','deposit':1000,'lock_threat':threat})
        elif action.kind in ('chi','pon'):
            branch=post_call(request,action)
            p=branch.position
            called=next(t.code for t in request.position.tiles if t.id==request.position.pending['tile_id'])
            codes=[t.code for t in request.position.tiles if t.id in action.tile_ids]
            banned=call_banned(action.kind,codes,called) if p.rules.forbid_kuikae else set()
            ids=[t.id for t in p.tiles if t.zone=='hand' and t.seat==p.observer_seat and index(t.code) not in banned]
            followups=tuple(replace(c,legality=candidate.legality) for c in analyze_discard_shapes(p,ids))
            followups,_=evaluate_candidates(branch,followups)
            if not followups:continue
            best=max(followups,key=lambda c:c.metrics['policy_score'].value)
            row=replace(candidate,metrics=best.metrics)
            row=with_cost(row,weights.commitment,{'follow_up_discard':best.action,
                          'follow_up_candidates':followups,
                          'assumption':'call_wins_priority_then_best_v2_legal_discard','kuikae_checked':True})
        else:
            branch=request if action.kind=='pass' else post_call(request,action)
            row=standing_candidate(branch,candidate)
            row=evaluate_candidates(branch,(row,),standing=True)[0][0]
            row=with_cost(row,0 if action.kind=='pass' else weights.commitment,
                         {'assumption':'unchanged_hand' if action.kind=='pass' else
                          'pre_replacement_shape_uniform_unseen_lookahead_no_new_dora_or_robbery_value'})
        evaluated.append(row)
    rankings={name:rank_actions(evaluated,name) for name in ('attack','defense','overall')}
    allowed=[c for c in evaluated if c.legality=='allowed']
    recommendations={}
    uncertain_win=any(c.action.kind in ('tsumo','ron') and c.legality=='unknown' for c in evaluated)
    if not uncertain_win:
        for name in rankings:
            permitted=rank_actions(allowed,name)
            if permitted.value:recommendations[name]=permitted.value[0]
    return tuple(evaluated),rankings,recommendations
