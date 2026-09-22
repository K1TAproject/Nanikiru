"""Conditional completed-hand analysis from pre-discard player information only."""

from collections import Counter
from copy import deepcopy

from .efficiency import analyze_discards, visible_inventory, physical_capacity
from .models import HandState, Meld, MeldKind, PlayerState, Tile
from .scoring import Rules, evaluate, index, ron_restrictions, tile34, waits

ANALYSIS_VERSION = "tenpai-v1-mahjong-2.0.0"
ASSUMPTIONS = {
    "timing": "拟弃牌后；仅评估普通荣和与未来普通自摸，非当前响应许可",
    "context": "当前场风、自风、门清/副露、已成立立直（含已知双立直）、公开指示牌与规则",
    "excluded": "不假设未来立直、一发、里宝、海底、河底、岭上、抢杠、天和或地和",
    "payments": "基础支付，不含本场、供托及责任支付；不是期望收益",
    "inventory": "未见枚数不是实际活牌山余量；已见尽仍展示理论等待",
    "future": "临时振听于下次自己摸牌解除；立直后振听不解除；期间新动作可能改变结论",
    "legacy": "缺少自身状态时标为未知；双立直未知时，条件点数仅按普通已成立立直计算",
}


def analyze_tenpai(observation, legal_discards):
    """Return all candidates without reordering or recommending an action."""
    basic = analyze_discards(observation, legal_discards)
    seen = visible_inventory(observation)
    seat = observation["observer_seat"]
    own = observation["players"][seat]
    status = observation.get("own_status", {})
    temporary, riichi = status.get("temporary_furiten"), status.get("riichi_furiten")
    double = status.get("double_riichi") if own["riichi"] else False
    limited = temporary is None or riichi is None or double is None
    concealed = [Tile(**t) for t in own["hand"]["known_tiles"]]
    if own["hand"]["drawn_tile"]:
        concealed.append(Tile(**own["hand"]["drawn_tile"]["tile"]))
    melds = [Meld(MeldKind(m["kind"]), [Tile(**t) for t in m["tiles"]], m["source_discard_id"])
             for m in own["melds"]]
    river = [Tile(**d["tile"]) for d in own["discards"]]
    candidates = []
    for base in basic["candidates"]:
        result = {"discard": deepcopy(base["discard"]), "shanten": base["shanten"],
                  "status": "applicable" if base["shanten"] == 0 else "not_applicable"}
        candidates.append(result)
        if base["shanten"] != 0:
            continue
        discarded = Tile(**base["discard"]["tile"])
        after = concealed.copy()
        after.remove(discarded)
        player = PlayerState(seat, own["score"], HandState(after), melds=melds, riichi=own["riichi"])
        held = Counter(after + [t for m in melds for t in m.tiles])
        held34 = Counter(index(t) for t in held.elements())
        waiting = sorted(i for i in waits(after) if held34[i] < 4)
        reasons = ron_restrictions(waiting, river + [discarded], temporary, riichi)
        blocked = any(not r.endswith("_unknown") for r in reasons)
        ron_state = "blocked" if blocked else ("unknown" if reasons else "permitted")
        future_reasons = ron_restrictions(waiting, river + [discarded], False, riichi)
        future_state = ("blocked" if any(not r.endswith("_unknown") for r in future_reasons)
                        else "unknown" if future_reasons else "permitted")
        rows = []
        for i in waiting:
            normal = tile34(i)
            variants = [normal] + ([Tile(normal.suit, 5, True)] if normal.rank == 5 and normal.suit != "z" else [])
            physical = []
            for tile in variants:
                capacity = physical_capacity(tile)
                # A completion may be exhausted in public information, but cannot
                # contain a fifth tile (or a second red five) in the winning hand.
                if held[tile] >= capacity:
                    continue
                entry = {"tile": str(tile), "unseen": capacity - seen[tile]}
                for kind in ("ron", "tsumo"):
                    value = evaluate(player, tile, tsumo=kind == "tsumo",
                                     round_wind=observation["round_wind"], dealer=observation["dealer_seat"],
                                     indicators=[Tile(**t) for t in observation["dora_indicators"]],
                                     rules=Rules(**observation["rules"]), is_daburu_riichi=double is True)
                    entry[kind] = {"has_yaku": value is not None, "value": value,
                                   "score_status": "double_riichi_unknown" if double is None else "known",
                                   "permission": ("no_yaku" if value is None else
                                                  ron_state if kind == "ron" else "permitted")}
                physical.append(entry)
            rows.append({"tile": str(normal), "unseen": sum(v["unseen"] for v in physical), "variants": physical})
        result.update(waits=rows, ron_restrictions=reasons, ron_state=ron_state,
                      ron_state_after_own_draw=future_state,
                      shape_wait_types=len(rows), shape_unseen=sum(w["unseen"] for w in rows),
                      available_wait_types=sum(w["unseen"] > 0 for w in rows),
                      yaku_unseen={kind: sum(v["unseen"] for w in rows for v in w["variants"] if v[kind]["has_yaku"])
                                   for kind in ("ron", "tsumo")})
    return {"version": ANALYSIS_VERSION, "player": seat, "limited": limited,
            "assumptions": deepcopy(ASSUMPTIONS), "candidates": candidates}
