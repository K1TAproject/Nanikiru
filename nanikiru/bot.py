"""Deterministic baseline policy. Receives only observation and legal actions."""

from .efficiency import analyze_discards
from .models import Decision, Discard, Tile


def choose_action(observation, legal_actions):
    actions = list(legal_actions)
    for action in actions:
        if isinstance(action, Decision) and action.kind in ("tsumo", "ron"):
            return action
    discards = [a for a in actions if isinstance(a, Discard)]
    if discards:
        best = analyze_discards(observation, discards)["candidates"][0]["discard"]
        return Discard(best["player"], Tile(**best["tile"]), best["is_tsumogiri"])
    for action in actions:
        if isinstance(action, Decision) and action.kind == "pass":
            return action
    raise ValueError("机器人没有可执行的合法动作")


def choose_yaku_action(observation, legal_actions, mode="attack"):
    from .decision import decode_action
    from .defense import analyze_defense
    actions = list(legal_actions)
    result = analyze_defense(observation, actions, mode)
    if result["recommended"] is not None:
        action = decode_action(result["recommended"])
        if action in actions:
            return action
    return choose_action(observation, actions)
