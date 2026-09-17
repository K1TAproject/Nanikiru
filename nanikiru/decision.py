"""Public-observation yaku paths, not completed-hand scoring or win guarantees."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict

from .call_rules import call_banned
from .efficiency import analyze_discards, analyze_shape, visible_counts
from .models import Decision, Discard, Tile
from .scoring import WINDS, index

STRATEGY_VERSION = "yaku-conservative-v1"


def encode_action(action):
    result = asdict(action)
    if isinstance(action, Decision):
        result["tiles"] = list(result["tiles"])
    result["type"] = "discard" if isinstance(action, Discard) else "decision"
    return result


def decode_action(data):
    data = dict(data)
    kind = data.pop("type")
    if kind == "discard":
        return Discard(**{**data, "tile": Tile(**data["tile"])})
    return Decision(**{**data, "tiles": tuple(Tile(**t) for t in data["tiles"])})


def hand_tiles(player):
    hand = player["hand"]
    return [Tile(**t) for t in hand["known_tiles"]] + (
        [Tile(**hand["drawn_tile"]["tile"])] if hand["drawn_tile"] else [])


def yaku_paths(observation, concealed=None):
    seen = visible_counts(observation)
    seat = observation["observer_seat"]
    p = observation["players"][seat]
    concealed = hand_tiles(p) if concealed is None else concealed
    meld_tiles = [Tile(**t) for m in p["melds"] for t in m["tiles"]]
    all_tiles = concealed + meld_tiles
    counts = Counter(index(t) for t in concealed)
    closed = all(m["kind"] == "closed_kan" for m in p["melds"])
    role_names = {31: ["白"], 32: ["发"], 33: ["中"]}
    for i, label in ((27 + WINDS.index(observation["round_wind"]), "场风"),
                     (27 + (seat - observation["dealer_seat"]) % 4, "自风")):
        role_names.setdefault(i, []).append(label)
    honors = []
    for i, roles in sorted(role_names.items()):
        fixed = any(m["kind"] != "chi" and index(Tile(**m["tiles"][0])) == i for m in p["melds"])
        n = counts[i]
        status = "condition_met" if fixed or n >= 3 else "potential" if n and 4 - seen[i] >= 3 - n else "unavailable_now"
        honors.append({"tile": str(Tile("z", i - 26)), "roles": roles, "role_count": len(roles),
                       "status": status, "concealed_count": n, "unseen": 4 - seen[i]})
    simple = lambda t: t.suit != "z" and 2 <= t.rank <= 8
    locked_terminal = any(not simple(t) for t in meld_tiles)
    tanyao = "blocked_by_meld" if locked_terminal else "condition_met" if all(map(simple, all_tiles)) else "potential"
    riichi = "condition_met" if p["riichi"] else "potential" if closed and p["score"] >= 1000 and observation["remaining_draws"] >= 4 else "unavailable_now"
    bonus = 0
    for raw in observation["dora_indicators"]:
        t = Tile(**raw)
        rank = (t.rank % 9 + 1 if t.suit != "z" else
                t.rank % 4 + 1 if t.rank <= 4 else (t.rank - 4) % 3 + 5)
        bonus += sum(x.suit == t.suit and x.rank == rank for x in all_tiles)
    red = sum(t.is_red for t in all_tiles)
    conditions = ["役牌：" + "/".join(h["roles"]) for h in honors if h["status"] == "condition_met"]
    if tanyao == "condition_met":
        conditions.append("断幺九：当前全为中张")
    if riichi == "condition_met":
        conditions.append("已立直")
    return {"closed": closed, "yakuhai": honors, "tanyao": tanyao, "riichi": riichi,
            "conditions": conditions, "has_condition": bool(conditions),
            "dora_tiles": bonus, "red_tiles": red,
            "aka_bonus_tiles": red if observation["rules"]["aka_dora_enabled"] else 0,
            "other_yaku": "not_evaluated",
            "note": "条件满足不等于已经和牌；潜在路径需要后续进张。宝牌不是役，未评估路径不代表必然无役。"}


def project_call(observation, action):
    """Conditional on a supplied legal chi/pon winning arbitration; no draws."""
    visible_counts(observation)
    view = deepcopy(observation)
    seat, pending = view["observer_seat"], view["pending"]
    if action.player != seat or action.kind not in ("chi", "pon") or pending["kind"] != "discard":
        raise ValueError("Only legal chi/pon response projections are supported")
    called = Tile(**pending["tile"])
    p = view["players"][seat]
    remaining = hand_tiles(p)
    for t in action.tiles:
        remaining.remove(t)
    river = view["players"][pending["source"]]["discards"][-1]
    river["claimed_by"] = seat
    p["melds"].append({"kind": action.kind, "tiles": [asdict(t) for t in (*action.tiles, called)],
                       "source_discard_id": river["id"]})
    p["hand"] = {"known_tiles": [asdict(t) for t in remaining], "unknown_count": 0, "drawn_tile": None}
    view.update(actor=seat, phase="await_discard", pending=None)
    banned = call_banned(action, called)
    legal = [Discard(seat, t, False) for t in sorted(set(remaining), key=str) if index(t) not in banned]
    return view, legal


def _rank(candidate):
    p = candidate["paths"]
    pairs = sum(h["role_count"] for h in p["yakuhai"]
                if h["status"] == "potential" and h["concealed_count"] == 2)
    return (candidate["shanten"], -int(p["has_condition"]), -candidate["ukeire"],
            -pairs, -(p["dora_tiles"] + p["aka_bonus_tiles"]))


def discard_candidates(observation, legal):
    basic = analyze_discards(observation, legal)
    tiles = hand_tiles(observation["players"][observation["observer_seat"]])
    candidates = []
    for c in basic["candidates"]:
        action = Discard(**{**c["discard"], "tile": Tile(**c["discard"]["tile"])})
        after = list(tiles)
        after.remove(action.tile)
        paths = yaku_paths(observation, after)
        candidates.append({"action": encode_action(action), "shanten": c["shanten"], "ukeire": c["ukeire"],
                           "effective_tiles": c["effective_tiles"], "paths": paths,
                           "reason": "；".join(paths["conditions"]) or "尚无已满足的受支持役种条件，仍可能通过后续进张形成役"})
    candidates.sort(key=_rank)  # Stable ties inherit the unchanged basic-efficiency ordering.
    for c in candidates:
        c["preferred"] = _rank(c) == _rank(candidates[0])
    return candidates


def analyze_decision(observation, legal_actions):
    """Heuristic baseline only. Supplied actions have already passed core validation."""
    visible_counts(observation)
    seat = observation["observer_seat"]
    if observation["actor"] != seat:
        raise ValueError("Analysis requires the acting player's observation")
    legal = list(legal_actions)
    discards = [a for a in legal if isinstance(a, Discard)]
    result = {"version": STRATEGY_VERSION, "player": seat, "supported": True,
              "candidates": [], "recommended": None, "reason": ""}
    if discards:
        candidates = discard_candidates(observation, discards)
        result.update(candidates=candidates, recommended=candidates[0]["action"],
                      reason="向听优先；同向听先保留已满足的有役条件，再比较进张、可发展的役牌对子和宝牌保留。" )
    elif observation["phase"] == "await_response" and Decision(seat, "pass") in legal:
        p = observation["players"][seat]
        paths = yaku_paths(observation)
        shape = analyze_shape(hand_tiles(p), p["melds"], visible_counts(observation))
        baseline = {"action": encode_action(Decision(seat, "pass")), **shape, "paths": paths,
                    "reason": "保留当前手牌；不预测下一次摸牌", "lost_closed_paths": []}
        candidates = [baseline]
        eligible = []
        for action in legal:
            if not isinstance(action, Decision) or action.kind not in ("chi", "pon"):
                continue
            projected, legal_discards = project_call(observation, action)
            choices = discard_candidates(projected, legal_discards)
            best = choices[0]
            accept = best["shanten"] < shape["shanten"] and best["paths"]["has_condition"]
            candidate = {"action": encode_action(action), "shanten": best["shanten"], "ukeire": best["ukeire"],
                         "effective_tiles": best["effective_tiles"], "paths": best["paths"],
                         "discard_candidates": choices, "planned_discard": best["action"],
                         "accepted": accept, "conditional_on_call_winning": True,
                         "lost_closed_paths": ["立直", "门前清自摸", "平和", "一杯口/二杯口"] if paths["closed"] else [],
                         "reason": "严格降低向听且保留有役条件；仅在此次鸣牌获裁决通过时成立" if accept else
                                   "保守回退为过：未同时满足严格降低向听和保留有役条件；不表示未来必然无役"}
            candidates.append(candidate)
            if accept:
                eligible.append(candidate)
        chosen = min(eligible, key=_rank) if eligible else baseline
        for c in candidates:
            c["preferred"] = c is chosen
        result.update(candidates=candidates, recommended=chosen["action"], reason=chosen["reason"])
    else:
        result.update(supported=False, reason="当前阶段不支持役种决策，回退基础策略；不主动杠或立直")
    win = next((a for a in legal if isinstance(a, Decision) and a.kind in ("tsumo", "ron")), None)
    if win:
        result.update(recommended=encode_action(win), reason="优先接受核心验证的合法和牌")
    return result
