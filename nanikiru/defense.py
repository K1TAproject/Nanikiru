"""Opponent-specific genbutsu evidence; unknown is never a danger probability."""

from .decision import analyze_decision, encode_action
from .efficiency import visible_counts
from .models import Decision, Discard, Tile
from .scoring import index

DEFENSE_VERSION = "genbutsu-v1"


def analyze_safety(observation, legal_actions):
    visible_counts(observation)
    seat = observation["observer_seat"]
    opponents = [p for p in observation["players"] if p["seat"] != seat]
    threats = [p["seat"] for p in opponents if p["riichi"]]
    candidates = []
    for action in legal_actions:
        if not isinstance(action, Discard):
            continue
        if action.player != seat:
            raise ValueError("Discard must belong to the observer")
        evidence = []
        for p in opponents:
            # Claimed river tiles still count for the core's discard-furiten rule.
            matches = [d["id"] for d in p["discards"] if index(Tile(**d["tile"])) == index(action.tile)]
            evidence.append({"player": p["seat"], "status": "genbutsu" if matches else "unknown",
                             "discard_ids": matches,
                             "reason": "该家牌河同种牌，依据舍牌振听仅对该家荣和安全" if matches else "未知，无可确认的现物依据"})
        covered = [e["player"] for e in evidence if e["player"] in threats and e["status"] == "genbutsu"]
        candidates.append({"action": encode_action(action), "opponents": evidence, "covered_threats": covered,
                           "covers_all_threats": bool(threats) and len(covered) == len(threats)})
    return {"threats": threats, "candidates": candidates}


def analyze_defense(observation, legal_actions, mode="attack", *, attack=None):
    if mode not in ("attack", "fold"):
        raise ValueError("Unknown decision mode")
    legal = list(legal_actions)
    safety = analyze_safety(observation, legal)
    attack = analyze_decision(observation, legal) if attack is None else attack
    # Preserve the attack order exactly, including stable ties.
    ordered = [next(s for s in safety["candidates"] if s["action"] == c["action"])
               for c in attack["candidates"] if c["action"]["type"] == "discard"]
    reason = "进攻模式保持原役种策略；安全依据仅作展示"
    recommended = attack["recommended"]
    if mode == "fold":
        if ordered:
            ordered.sort(key=lambda c: -len(c["covered_threats"]))
            recommended = ordered[0]["action"]
            if not safety["threats"]:
                reason = "无已成立立直威胁，回退原弃牌排序；不表示全桌安全"
            elif not ordered[0]["covered_threats"]:
                reason = "没有可确认的安全选择（针对明确威胁），回退原弃牌排序"
            elif ordered[0]["covers_all_threats"]:
                reason = "弃和优先覆盖全部立直家；同等覆盖沿用原役种排序，允许增加向听"
            else:
                reason = "没有覆盖全部立直家的现物；按覆盖家数优先，未覆盖玩家仍未知，不表示全桌安全"
        else:
            passed = next((a for a in legal if isinstance(a, Decision) and a.kind == "pass"), None)
            recommended = encode_action(passed) if passed else None
            reason = "弃和不主动吃碰杠或立直；无可用弃牌时仅过或合法和牌"
    win = next((a for a in legal if isinstance(a, Decision) and a.kind in ("ron", "tsumo")), None)
    if win:
        recommended = encode_action(win)
        reason = "优先接受核心验证的合法和牌"
    return {"version": DEFENSE_VERSION, "mode": mode, "threats": safety["threats"],
            "candidates": ordered, "recommended": recommended, "reason": reason,
            "scope": "现物依据仅针对指定玩家当前荣和，不代表全桌安全，也不阻止自摸；未知不等于安全"}
