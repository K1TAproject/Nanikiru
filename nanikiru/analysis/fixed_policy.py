"""Human-parameter discard policy. Scores are heuristics, never probabilities."""

from dataclasses import asdict, dataclass, replace
import math

from ..contracts import Metric
from ..models import UNKNOWN
from ..rules.scoring import evaluate, waits
from ..rules.observed_facts import event_fact, complete_history, riichi_status
from ..tiles import index, physical_capacity, tile34
from .efficiency import analyze_shape, calculate_shanten, hand_codes, meld_codes, visible_counts, visible_inventory

FORMULA_VERSION = 'fixed-v2-opportunity-2'

@dataclass(frozen=True)
class Parameters:
    shanten: float = 4.0
    ukeire: float = 2.0
    lookahead: float = 3.0
    value: float = 1.5
    bonus: float = 0.25
    risk: float = 7.0
    reserve: float = 0.3
    late_defense: float = 0.8
    route: float = 2.0
    route_speed: float = 1.0
    route_ukeire: float = 1.0
    commitment: float = 0.5

    def __post_init__(self):
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in asdict(self).values()):
            raise ValueError("v2 weights must be finite nonnegative numbers")


def _ready(history, seat):
    return next((e.id for e in history if e.actor == seat and event_fact(e)[0] == "riichi_accepted"), None)


def danger(position, history, code, coverage=None):
    """Per-opponent ron evidence and uncalibrated risk index, not safe forever."""
    n = index(code)
    seen = visible_inventory(position)
    count = sum(v for c, v in seen.items() if index(c) == n)
    rows = []
    for seat in range(4):
        if seat == position.observer_seat:
            continue
        ready = _ready(history, seat)
        entry_ready = position.initial_context.get('riichi', [UNKNOWN]*4)[seat] is True
        discarded = {index(t.code) for t in position.tiles if t.zone == "river" and t.seat == seat and t.code is not UNKNOWN}
        for e in history:
            kind, fact = event_fact(e)
            c = fact.get("code", UNKNOWN)
            if kind == "discard" and c is not UNKNOWN and (e.actor == seat or entry_ready or ready and e.id > ready):
                discarded.add(index(c))
        groups = [g for gid, g in position.groups.items()
                  if any(t.group_id == gid and t.seat == seat for t in position.tiles)]
        opened = sum(g["kind"] != "closed_kan" for g in groups)
        threat = min(1.0, (0.9 if ready or entry_ready else 0.15 + 0.14*opened) + (0.05 if position.dealer_seat == seat else 0))
        evidence, tile_risk = [], 1.0
        if n in discarded:
            evidence.append("genbutsu_ron_only"); tile_risk = 0.0
        elif n >= 27:
            tile_risk = max(0.15, 0.75 - 0.18*count)
            evidence.append("honor_visible_count_heuristic")
        else:
            rank = n % 9
            if rank in (0, 8): tile_risk *= 0.7; evidence.append("terminal_heuristic")
            partners = [n+d for d in (-3, 3) if 0 <= n+d < 27 and (n+d)//9 == n//9]
            if partners and all(p in discarded for p in partners):
                tile_risk *= 0.65; evidence.append("suji_not_guaranteed_safe")
        rows.append(dict(seat=seat, riichi=riichi_status(position,history,seat,coverage),
                         threat=round(threat, 5), risk=round(threat*tile_risk, 5), evidence=evidence))
    values = sorted((r["risk"] for r in rows), reverse=True)
    return min(1.0, values[0] + 0.25*sum(values[1:])), rows


def _bonus(code, indicators, rules):
    n = index(code)
    total = int(code[0] == "0" and rules.aka_dora)
    for c in indicators:
        i = index(c)
        d = (i//9*9 + (i+1)%9) if i < 27 else (27+(i-27+1)%4 if i < 31 else 31+(i-31+1)%3)
        total += n == d
    return total


def evaluate_candidates(request, candidates, *, standing=False, assume_riichi=False):
    """Return enriched candidates and attack/defense/overall rankings."""
    weights = Parameters(**request.method_options)
    p, history = request.position, request.history
    hand, groups = hand_codes(p), meld_codes(p)
    if hand is UNKNOWN or groups is UNKNOWN:
        unavailable = Metric("unavailable", details={"missing_fields": ["hand_or_groups"]})
        return candidates, {k: unavailable for k in ("attack", "defense", "overall")}
    inventory = visible_inventory(p)
    seen = visible_counts(p)
    pool = [] if seen is UNKNOWN else [(c, physical_capacity(c)-inventory[c]) for c in
            [*(tile34(i) for i in range(34)), "0m", "0p", "0s"] if physical_capacity(c)>inventory[c]]
    total = sum(n for _, n in pool)
    indicators = [t.code for t in p.tiles if t.zone == "dora"]
    if not p.rules.kan_dora:
        indicators = indicators[:1]
    history_known = complete_history(history, request.coverage)
    own_ready = riichi_status(p, history, p.observer_seat, request.coverage)
    if assume_riichi: own_ready = True
    can_score = (p.dealer_seat is not UNKNOWN and p.round_wind is not UNKNOWN
                 and bool(indicators) and UNKNOWN not in indicators and seen is not UNKNOWN
                 and own_ready is not UNKNOWN)
    late = 0 if p.remaining_draws is UNKNOWN else max(0, min(1, (70-p.remaining_draws)/70))
    defense_weight = weights.risk*(1+weights.late_defense*late)
    enriched, scores = [], {k: {} for k in ("attack", "defense", "overall")}
    for candidate in candidates:
        metrics = dict(candidate.metrics)
        tile = None if standing else next(t for t in p.tiles if t.id == candidate.action.tile_ids[0])
        remaining = list(hand)
        if tile is not None: remaining.remove(tile.code)
        shanten = metrics["shanten"].value
        missing, terms = [], {"shanten": -weights.shanten*shanten}
        if not history_known:
            missing.append("qualification_history")
        ukeire = metrics["ukeire"].value
        if ukeire is not None and total:
            terms["ukeire"] = weights.ukeire*ukeire/total
        else: missing.append("visible_inventory")
        # Quality after an improving draw, not a second copy of current ukeire.
        if total:
            future, improving_mass = 0.0, 0
            for code, count in pool:
                drawn = [*remaining, code]
                next_shanten = calculate_shanten(drawn, groups)
                if next_shanten >= shanten:
                    continue
                if next_shanten == -1:
                    quality = 1.0
                else:
                    choices = (code,) if own_ready is True else set(drawn)
                    next_seen = list(seen); next_seen[index(code)] += 1
                    qualities = []
                    for c in choices:
                        after = list(drawn); after.remove(c)
                        if calculate_shanten(after, groups) == next_shanten:
                            qualities.append(analyze_shape(after, groups, next_seen)["ukeire"] / max(1, total-1))
                    quality = max(qualities, default=0)
                future += count*quality
                improving_mass += count
            conditional_quality = future/improving_mass if improving_mass else 0.0
            future = future/total
            metrics["lookahead_gain"] = Metric("conditional", future, "heuristic", {
                "assumption": "uniform_unseen_improving_draw_mass_times_best_next_quality_completion_is_one",
                "improving_fraction": improving_mass/total, "conditional_quality": conditional_quality,
                "not_win_probability": True, "ignores_opponent_interruption": True})
            terms["lookahead"] = weights.lookahead*future
        else:
            metrics["lookahead_gain"] = Metric("unavailable", details={"missing_fields": ["unseen_draw_pool"]})
        value_rows, weighted, mass = [], 0, 0
        if shanten == 0 and can_score:
            waiting = waits(remaining, groups)
            for code, count in pool:
                if index(code) not in waiting: continue
                score = evaluate(remaining, groups, code, seat=p.observer_seat, dealer=p.dealer_seat,
                                 round_wind=p.round_wind, tsumo=True, riichi=own_ready,
                                 indicators=indicators, ura=(), rules=p.rules)
                points = 0 if score is None else score["cost"]["main"] + score["cost"]["additional"]*2
                value_rows.append(dict(tile=code, unseen=count, points=points, score=score))
                weighted += count*points; mass += count
        if mass:
            value = weighted/mass
            metrics["conditional_value"] = Metric("conditional", value, "heuristic", {
                "scope": "unseen_weighted_tsumo_payments_given_completion", "rows": value_rows,
                "assumptions": "no_ura_no_ippatsu_no_special_win_no_honba", "not_expected_net_gain": True})
            terms["value"] = weights.value*value/8000
        else:
            status = "not_applicable" if shanten != 0 else "unavailable"
            metrics["conditional_value"] = Metric(status, details={"reason": "not_tenpai" if shanten != 0 else "missing_scoring_context_or_live_waits"})
            if shanten == 0: missing.append("conditional_value")
        # Before tenpai these are value proxies; at tenpai the scoring engine
        # already includes yaku and dora. Do not reward them a second time.
        if shanten > 0 and UNKNOWN not in indicators and indicators:
            terms["bonus"] = weights.bonus*sum(_bonus(c, indicators, p.rules) for c in remaining)
        from .routes import evaluate_routes
        routes = evaluate_routes(remaining, groups, p, seen,
                                 speed_weight=weights.route_speed,ukeire_weight=weights.route_ukeire)
        metrics['routes'] = Metric('conditional', routes, 'heuristic')
        terms['route'] = weights.route*routes['potential'] if shanten > 0 else 0.0
        risk, opponents = (0.0, []) if standing else danger(p, history, tile.code, request.coverage)
        reserve = sum(all(r["risk"] == 0 for r in danger(p, history, c, request.coverage)[1]) for c in remaining)
        metrics["danger"] = Metric("conditional", risk, "heuristic", {
            "opponents": opponents, "not_probability": True,
            "history_complete": history_known,
            "missing_fields": (["public_inventory"] if seen is UNKNOWN else []) +
                              [f"riichi:{r['seat']}" for r in opponents if r['riichi'] is UNKNOWN],
            "unobserved_riichi": "base_threat_assumption_not_confirmed_absence",
            "scope": "current_ron_risk_index"})
        attack = sum(terms.values())
        terms["risk"] = -defense_weight*risk
        terms["reserve"] = weights.reserve*reserve
        overall = sum(terms.values())
        metrics["policy_score"] = Metric("conditional", overall, "heuristic", {
            "contributions": terms, "parameters": asdict(weights), "effective_risk_weight": defense_weight,
            "formula_version": FORMULA_VERSION,
            "missing_fields": sorted(set(missing)), "not_expected_points": True})
        enriched.append(replace(candidate, metrics=metrics))
        if candidate.legality != "forbidden":
            for name, score in (("attack", attack), ("defense", -risk), ("overall", overall)):
                scores[name][candidate.action] = round(score, 9)
    rankings = {}
    for name, values in scores.items():
        tiers = {}
        for action, value in values.items(): tiers.setdefault(value, []).append(action)
        rankings[name] = Metric("conditional", tuple(tuple(tiers[v]) for v in sorted(tiers, reverse=True)), "heuristic",
                               {"method": "fixed-v2", "strategy": name, "parameters": asdict(weights), "not_probability": True}) if tiers else Metric("not_applicable")
    return tuple(enriched), rankings
