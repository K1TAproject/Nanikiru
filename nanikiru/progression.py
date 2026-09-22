"""One effective own draw followed by a tenpai discard; no hidden state."""

from copy import deepcopy
from dataclasses import asdict

from .discard_rules import validate_discard
from .efficiency import analyze_discards, visible_inventory, physical_capacity
from .models import Discard, DrawnTile, HandState, Tile
from .recorder import RecordError
from .tenpai import analyze_tenpai

VERSION = "one-shanten-progression-v1"
ASSUMPTIONS = ["候选投影：期间没有其他行动改变公开信息；不预测对手行动。",
               "假设下一次自己普通摸牌，临时振听解除；舍牌及立直后振听仍分别判断。",
               "当前与后续弃牌均已见；未见枚数不是活牌山余量。",
               "仅有效摸牌后进入听牌，不搜索改良、鸣牌或第二次摸牌；无综合推荐或期望收益。"]


def analyze_progression(observation, legal_discards):
    basic = analyze_discards(observation, legal_discards)
    seen = visible_inventory(observation)
    results = []
    for candidate in basic["candidates"]:
        item = {"discard": candidate["discard"], "status": "not_applicable", "draws": []}
        results.append(item)
        if candidate["shanten"] != 1:
            continue
        item["status"] = "applicable"
        seat = observation["observer_seat"]
        if observation["players"][seat]["riichi"]:
            item.update(status="limited", reason="已立直却为一向听，不能可靠投影")
            continue
        if observation["remaining_draws"] < 4:
            item.update(status="limited", reason="活牌山不足一巡；不假设鸣牌改变摸牌顺序，无法投影下次自己普通摸牌")
            continue
        cut = Discard(**{**candidate["discard"], "tile": Tile(**candidate["discard"]["tile"])})
        projected = deepcopy(observation)
        own = projected["players"][seat]
        tiles = [Tile(**t) for t in own["hand"]["known_tiles"]]
        if own["hand"]["drawn_tile"]:
            tiles.append(Tile(**own["hand"]["drawn_tile"]["tile"]))
        tiles.remove(cut.tile)
        own["hand"]["known_tiles"] = [asdict(t) for t in tiles]
        own["discards"].append({"id": "projection:initial", "tile": asdict(cut.tile),
                                "is_tsumogiri": cut.is_tsumogiri, "is_riichi_declaration": False, "claimed_by": None})
        projected.setdefault("own_status", {})["temporary_furiten"] = False
        # First discard has occurred; this is never a double-riichi declaration.
        projected.pop("riichi_context", None)
        for effective in candidate["effective_tiles"]:
            normal = Tile.parse(effective["tile"])
            variants = [normal] + ([Tile(normal.suit, 5, True)] if normal.rank == 5 and normal.suit != "z" else [])
            for tile in variants:
                capacity = physical_capacity(tile)
                unseen = capacity - seen[tile]
                if unseen < 0:
                    raise ValueError("Visible physical tile inventory exceeded")
                if not unseen:
                    continue
                trial = deepcopy(projected)
                trial["players"][seat]["hand"]["drawn_tile"] = {"tile": asdict(tile)}
                trial["remaining_draws"] -= 1
                hand = HandState(tiles.copy(), drawn_tile=DrawnTile(tile))
                legal = []
                for action in [Discard(seat, t, False) for t in dict.fromkeys(tiles)] + [Discard(seat, tile, True)]:
                    try:
                        validate_discard(hand, own["riichi"], action)
                    except RecordError:
                        continue
                    legal.append(action)
                quality = analyze_tenpai(trial, legal)
                branches = [c for c in quality["candidates"] if c["status"] == "applicable"]
                item["draws"].append({"tile": str(tile), "unseen": unseen, "branches": branches,
                                       "limited": quality["limited"], "assumptions": quality["assumptions"]})
    return {"version": VERSION, "assumptions": ASSUMPTIONS.copy(), "candidates": results}
