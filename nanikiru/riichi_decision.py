"""Conservative conditional riichi/dama comparison, not expected utility."""

from copy import deepcopy
from dataclasses import asdict

from .decision import encode_action
from .efficiency import visible_counts
from .models import Decision, Discard
from .tenpai import analyze_tenpai

VERSION = "riichi-conservative-v1"
ASSUMPTIONS = ["立直侧假设宣言通过并成立；实际扣棒与响应由核心处理。",
               "基础支付不含本场、供托、责任支付；立直另需1000点，并限制后续弃牌与改手。",
               "不预测里宝、一发、对手行为、和牌概率或期望收益。",
               "先沿用役种/防守弃牌；仅对同一弃牌判断立直，不重新排序吃碰与弃牌。"]


def analyze_riichi(observation, legal_actions, mode="attack"):
    if mode not in ("attack", "fold"):
        raise ValueError("Unknown decision mode")
    visible_counts(observation)
    seat = observation["observer_seat"]
    threats = [p["seat"] for p in observation["players"] if p["seat"] != seat and p["riichi"]]
    rows = []
    for action in legal_actions:
        if not isinstance(action, Decision) or action.kind != "riichi":
            continue
        cut = Discard(action.player, action.tiles[0], action.is_tsumogiri)
        if cut not in legal_actions:
            raise ValueError("Riichi must have a matching legal ordinary discard")
        dama = analyze_tenpai(observation, [cut])["candidates"][0]
        projected = deepcopy(observation)
        projected["players"][seat]["riichi"] = True
        projected.setdefault("own_status", {})["double_riichi"] = observation.get("riichi_context", {}).get("double_eligible")
        riichi = analyze_tenpai(projected, [cut])["candidates"][0]
        reason, recommend = "存在打点、等待与行动自由的取舍；保守默听，不代表实战最优", False
        if mode == "fold":
            reason = "弃和模式不主动立直"
        elif threats:
            reason = "已有明确对手立直威胁，保守默听"
        elif dama.get("ron_state") != "permitted":
            reason = "振听受限或状态未知，不自动立直"
        elif observation.get("riichi_context", {}).get("double_eligible") is None:
            reason = "宣言条件信息不足，不自动立直"
        elif not dama.get("shape_unseen", 0):
            reason = "等待已见尽，不自动立直"
        elif not any(v["ron"]["has_yaku"] for w in dama["waits"] for v in w["variants"]) and riichi["yaku_unseen"]["ron"] > 0:
            reason, recommend = "默听普通荣和全部无役；立直成立可增加有役荣和路径", True
        rows.append({"discard": asdict(cut), "dama_action": encode_action(cut), "riichi_action": encode_action(action),
                     "dama": dama, "riichi": riichi, "riichi_cost": 1000,
                     "recommend_riichi": recommend, "reason": reason})
    return {"version": VERSION, "mode": mode, "threats": threats, "assumptions": ASSUMPTIONS.copy(), "candidates": rows}


def choose_riichi_action(observation, legal_actions, mode="attack"):
    from .bot import choose_yaku_action
    from .decision import decode_action
    actions = list(legal_actions)
    chosen = choose_yaku_action(observation, actions, mode)
    if not isinstance(chosen, Discard):
        return chosen
    comparison = analyze_riichi(observation, actions, mode)
    for row in comparison["candidates"]:
        if row["discard"] == asdict(chosen) and row["recommend_riichi"]:
            return decode_action(row["riichi_action"])
    return chosen
