"""Small synchronous dispatcher over actual implemented methods only."""

from copy import deepcopy
from dataclasses import replace

from ..contracts import AnalysisResult, Metric
from ..models import UNKNOWN
from ..rules.discards import observed_discards
from .basic import analyze_discard_shapes, analyze_shanten, analyze_waits
from .efficiency import hand_codes, meld_codes
from .minimal_policy import rank_discard_candidates

METHOD_TASKS = {
    "basic-v1": ("shanten", "tenpai", "discard_shape"),
    "minimal-shape-v1": ("shanten", "tenpai", "discard_shape", "recommend"),
    "fixed-v2": ("shanten", "tenpai", "discard_shape", "recommend"),
}


def analyze(request):
    """Return AnalysisResult without advancing state or querying hidden data.

    Invalid/unsupported requests raise ValueError. Missing facts produce Metrics.
    Complete simulated observations can establish discard permissions; manual
    records without the needed rule history remain conditional preferences.
    """
    request = deepcopy(request)
    request.__post_init__()
    if request.method not in METHOD_TASKS:
        raise ValueError(f"未实现分析方法：{request.method}")
    if request.method == "fixed-v2":
        from .fixed_policy import Parameters
        try:
            Parameters(**request.method_options)
        except TypeError as error:
            raise ValueError("Unknown fixed-v2 parameter") from error
    elif request.method_options:
        raise ValueError("This method does not accept parameters")
    unsupported = set(request.tasks) - set(METHOD_TASKS[request.method])
    if unsupported:
        raise ValueError(f"方法 {request.method} 不支持任务：{', '.join(sorted(unsupported))}")
    if request.objective != "shape":
        raise ValueError(f"方法 {request.method} 尚不支持目标：{request.objective}")
    position = request.position
    situation, candidates = {}, ()
    recommendations = {}
    if "shanten" in request.tasks:
        situation["shanten"] = analyze_shanten(position)
    if "tenpai" in request.tasks:
        situation["waits"] = analyze_waits(position)
    if set(request.tasks) & {"discard_shape", "recommend"}:
        if position.phase is UNKNOWN or position.actor is UNKNOWN:
            context = Metric("unavailable", details={"reason": "unknown_action_context"})
        elif position.phase != "await_discard" or position.actor != position.observer_seat:
            context = Metric("not_applicable", details={"reason": "not_observer_discard_turn"})
        else:
            ids = tuple(t.id for t in position.tiles if t.zone == "hand" and t.seat == position.observer_seat)
            candidates = analyze_discard_shapes(position, ids)
            allowed = observed_discards(position, request.history, request.coverage)
            if allowed is not UNKNOWN:
                candidates = tuple(replace(c, legality="allowed" if c.action.tile_ids[0] in allowed else "forbidden",
                                           reasons=("Complete observed discard restrictions",)) for c in candidates)
            if hand_codes(position) is UNKNOWN or meld_codes(position) is UNKNOWN:
                context = Metric("unavailable", details={"reason": "incomplete_hand_or_groups"})
            else:
                context = Metric("available", len(candidates), "exact", {"scope": "hypothetical_discards"})
        situation["discard_shape"] = context
        if "recommend" in request.tasks:
            if context.status != "available":
                situation["discard_ranking"] = deepcopy(context)
            else:
                if request.method == "fixed-v2":
                    from .fixed_policy import evaluate_candidates
                    candidates, rankings = evaluate_candidates(request, candidates)
                    situation.update({name+"_ranking": value for name, value in rankings.items()})
                    ranking = rankings["overall"]
                    for name, value in rankings.items():
                        if value.value and all(c.legality != "unknown" for c in candidates):
                            recommendations[name] = value.value[0]
                else:
                    ranking = rank_discard_candidates(candidates)
                if ranking.status == "available":
                    ranking = replace(ranking, status="conditional")
                ranking.details["scope"] = "heuristic_discard_preference" if request.method == "fixed-v2" else "shape_preference_only"
                situation["discard_ranking"] = ranking
                if ranking.value and all(c.legality != "unknown" for c in candidates):
                    recommendations["overall"] = ranking.value[0]
    if request.method=='fixed-v2' and 'recommend' in request.tasks:
        from .action_policy import evaluate_actions
        candidates,rankings,recommendations=evaluate_actions(request,candidates)
        situation.update({name+'_action_ranking':value for name,value in rankings.items()})
        situation['action_ranking']=rankings['overall']
        unvalued=tuple(c.action for c in candidates if c.legality!='forbidden' and
                      (c.metrics.get('policy_score') is None or c.metrics['policy_score'].value is None))
        situation['action_value_coverage']=Metric('available',{
            'unvalued_actions':unvalued,'all_candidates_scored':not unvalued,
            'objective':'heuristic_not_match_rank'},'exact')
    return AnalysisResult(request.state_version, request.method, situation, candidates, recommendations)
