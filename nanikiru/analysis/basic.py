"""Small internal analysis interfaces. No dispatcher, legal actions or policy."""

from ..contracts import Action, CandidateResult, Metric
from .efficiency import (
    analyze_discards, calculate_shanten, hand_codes, meld_codes, visible_counts,
)
from ..models import UNKNOWN
from ..rules.scoring import evaluate, waits
from ..rules.config import RuleConfig
from ..tiles import index, tile34, validate_code


def analyze_shanten(position) -> Metric:
    """Current 13/14-equivalent hand; unknown coverage cannot become zero."""
    hand, groups = hand_codes(position), meld_codes(position)
    missing = [name for name, value in (("hand", hand), ("groups", groups)) if value is UNKNOWN]
    if missing:
        return Metric("unavailable", details={"missing_fields": missing})
    return Metric("available", calculate_shanten(hand, groups), "exact",
                  {"scope": "shape_only", "complete_value": -1})


def analyze_waits(position) -> Metric:
    """Shape waits on a 13-equivalent hand, not a legal ron/tsumo claim.

All physical shape waits remain listed, including publicly exhausted ones.
Unseen counts are UNKNOWN when public coverage is incomplete.
"""
    hand, groups = hand_codes(position), meld_codes(position)
    missing = [name for name, value in (("hand", hand), ("groups", groups)) if value is UNKNOWN]
    if missing:
        return Metric("unavailable", details={"missing_fields": missing})
    if len(hand) == 14 - 3 * len(groups):
        return Metric("not_applicable", details={"reason": "discard_or_remove_winning_tile_first"})
    waiting = waits(hand, groups)
    seen = visible_counts(position)
    value = [{"tile": tile34(i), "unseen": UNKNOWN if seen is UNKNOWN else 4 - seen[i]}
             for i in sorted(waiting)]
    return Metric("available", value, "exact", {
        "scope": "shape_only", "unseen_complete": seen is not UNKNOWN,
        "formal_tenpai": bool(waits(hand, groups, formal=position.rules.formal_dead_waits)),
        "missing_fields": ["visible_inventory"] if seen is UNKNOWN else [],
    })


def analyze_discard_shapes(position, tile_ids) -> tuple[CandidateResult, ...]:
    """Evaluate specified own-hand references; never infer action permission."""
    result = []
    for row in analyze_discards(position, tile_ids):
        metrics = {}
        for name in ("shanten", "effective_tiles", "ukeire"):
            value = row[name]
            if value is UNKNOWN:
                missing = "hand_or_groups" if row["shanten"] is UNKNOWN else "visible_inventory"
                metrics[name] = Metric("unavailable", details={"missing_fields": [missing]})
            else:
                details = {"scope": "after_hypothetical_discard"}
                if name == "effective_tiles" and row["ukeire"] is UNKNOWN:
                    details.update(unseen_complete=False, missing_fields=["visible_inventory"])
                metrics[name] = Metric("available", value, "exact", details)
        action = Action("discard", position.observer_seat, (row["tile_id"],))
        result.append(CandidateResult(action, "unknown", metrics,
                                      ("Shape comparison does not establish legal action permission",)))
    return tuple(result)


def score_hand(concealed, groups, win_tile, *, seat, dealer, round_wind,
               tsumo, riichi, indicators=UNKNOWN, ura=UNKNOWN,
               aka_dora_enabled=True, rules=None, **flags) -> Metric:
    """Value of a hypothetical completed hand under explicit conditions.

concealed EXCLUDES win_tile. () indicators/ura means explicitly assuming none;
UNKNOWN means not known. Omitted special flags assume false and are reported.
No furiten/response checks, honba, deposits, pao or probability calculation.
"""
    defaults = {name: False for name in (
        "is_daburu_riichi", "is_ippatsu", "is_rinshan", "is_chankan",
        "is_haitei", "is_houtei", "is_tenhou", "is_chiihou",
    )}
    if rules is not None and not isinstance(rules, RuleConfig):
        raise ValueError("rules must be RuleConfig")
    if flags.keys() - defaults.keys():
        raise ValueError("Unsupported scoring flag")
    conditions = {**defaults, **flags}
    if conditions["is_daburu_riichi"] is True and riichi is False:
        raise ValueError("Double riichi requires established/assumed riichi")
    # Non-riichi hands cannot count ura; it is irrelevant even if unknown.
    ura_relevant = (riichi is not False or conditions["is_daburu_riichi"] is not False) and (rules is None or rules.ura_dora)
    inputs = {"concealed": concealed, "groups": groups, "win_tile": win_tile,
              "seat": seat, "dealer": dealer, "round_wind": round_wind,
              "tsumo": tsumo, "riichi": riichi, "indicators": indicators,
              "aka_dora_enabled": aka_dora_enabled, **conditions}
    if ura_relevant:
        inputs["ura"] = ura
    missing = [name for name, value in inputs.items() if value is UNKNOWN]
    for name in ("concealed", "indicators", "ura"):
        if name in inputs and inputs[name] is not UNKNOWN and UNKNOWN in inputs[name]:
            missing.append(name)
    if groups is not UNKNOWN and any(UNKNOWN in codes for _, codes in groups):
        missing.append("groups")
    if missing:
        return Metric("unavailable", details={"missing_fields": missing})
    if any(type(value) is not bool for value in (tsumo, riichi, aka_dora_enabled, *conditions.values())):
        raise ValueError("Scoring flags must be bool or UNKNOWN")
    rules = RuleConfig(aka_dora=aka_dora_enabled) if rules is None else rules
    validate_code(win_tile)
    concealed, groups = tuple(concealed), tuple((k, tuple(c)) for k, c in groups)
    indicators, ura = tuple(indicators), tuple(ura) if ura_relevant else ()
    # evaluate validates supplied conditions; its None covers only shape/no-yaku.
    value = evaluate(concealed, groups, win_tile, seat=seat, dealer=dealer,
                     round_wind=round_wind, tsumo=tsumo, riichi=riichi,
                     indicators=indicators, ura=ura, aka_dora_enabled=aka_dora_enabled, rules=rules,
                     **conditions)
    if index(win_tile) not in waits(concealed, groups):
        return Metric("not_applicable", details={"reason": "not_a_winning_shape"})
    details = {"scope": "conditional_base_payments", "win_permission_checked": False,
               "conditions": {"seat": seat, "dealer": dealer, "round_wind": round_wind,
                              "tsumo": tsumo, "riichi": riichi, "indicators": indicators,
                              "ura": ura, "aka_dora_enabled": aka_dora_enabled, "rules": rules, **conditions}}
    return Metric("conditional", {"has_yaku": value is not None, "score": value}, "exact", details)
