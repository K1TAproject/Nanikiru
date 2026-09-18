"""Shape efficiency from a player's observation only; no Game or UI dependency."""

from copy import deepcopy
from dataclasses import asdict
from functools import lru_cache

from mahjong.shanten import Shanten

from .models import Discard, Tile

ANALYSIS_VERSION = "basic-efficiency-v1-mahjong-2.0.0"


def _index(tile):
    return "mpsz".index(tile.suit) * 9 + tile.rank - 1


def _code(i):
    return f"{i % 9 + 1}{'mpsz'[i // 9]}"


def _require(condition, message):
    if not condition:
        raise ValueError(message)


@lru_cache(maxsize=32768)
def _shanten(counts, special):
    return Shanten.calculate_shanten(counts, use_chiitoitsu=special, use_kokushi=special)


def visible_tiles(observation):
    """Physical visible tiles, with claimed river tiles counted only in melds."""
    seat = observation["observer_seat"]
    _require(type(seat) is int and 0 <= seat < 4, "A player observation is required")
    _require(not any(k in observation for k in ("seed", "wall", "remaining_wall", "reserved_wall", "omniscient")),
             "Do not pass omniscient data to efficiency analysis")
    players = observation["players"]
    _require(len(players) == 4 and [p["seat"] for p in players] == list(range(4)), "Invalid observed seats")
    tiles = []

    def add(tile):
        tiles.append(Tile(**tile))

    for p in players:
        h = p["hand"]
        if p["seat"] == seat:
            _require(h["unknown_count"] == 0, "Own hand must be known")
            for t in h["known_tiles"]:
                add(t)
            if h["drawn_tile"] is not None:
                _require(h["drawn_tile"]["tile"] is not None, "Own draw must be known")
                add(h["drawn_tile"]["tile"])
        else:
            _require(not h["known_tiles"] and (h["drawn_tile"] is None or h["drawn_tile"]["tile"] is None),
                     "Opponent concealed tiles must remain hidden")
        for meld in p["melds"]:
            for t in meld["tiles"]:
                add(t)
        for d in p["discards"]:
            if d["claimed_by"] is None:
                add(d["tile"])
    for t in observation["dora_indicators"]:
        add(t)
    return tiles


def visible_counts(observation):
    """34 counts, including the about-to-be-discarded tile exactly once."""
    counts = [0] * 34
    for tile in visible_tiles(observation):
        counts[_index(tile)] += 1
    _require(max(counts) <= 4, "Visible tile inventory exceeds four copies")
    return counts



def analyze_shape(tiles, melds, seen):
    """13-tile-equivalent shape, with a caller-supplied visible inventory."""
    counts = [0] * 34
    for tile in tiles:
        counts[_index(tile)] += 1
    special = not melds
    shanten = _shanten(tuple(counts), special)
    effective = []
    for i in range(34):
        unseen = 4 - seen[i]
        if unseen <= 0:
            continue
        trial = counts.copy()
        trial[i] += 1
        if _shanten(tuple(trial), special) < shanten:
            effective.append({"tile": _code(i), "unseen": unseen})
    return {"shanten": shanten, "effective_tiles": effective,
            "ukeire": sum(t["unseen"] for t in effective)}


def analyze_discards(observation, legal_discards):
    """Rank supplied legal Discards; rules/availability are the caller's responsibility.

    Red fives keep their action identity but share shape/count calculations with
    ordinary fives. Drawn vs concealed copies remain selectable, but do not create
    an extra strategic choice when the physical face is identical.
    """
    seen = visible_counts(observation)
    seat = observation["observer_seat"]
    _require(observation["actor"] == seat and observation["phase"] == "await_discard", "Analyze the acting player's pre-discard observation")
    p = observation["players"][seat]
    known = [Tile(**t) for t in p["hand"]["known_tiles"]]
    wrapper = p["hand"]["drawn_tile"]
    drawn = Tile(**wrapper["tile"]) if wrapper is not None else None
    all_tiles = known + ([drawn] if drawn else [])
    _require(len(all_tiles) == 14 - 3 * len(p["melds"]), "Invalid concealed tile count for discard")
    legal = list(legal_discards)
    _require(bool(legal), "At least one legal discard is required")
    candidates = []
    shapes = {}
    for action in dict.fromkeys(legal):
        _require(type(action) is Discard and action.player == seat and type(action.is_tsumogiri) is bool,
                 "Supply legal Discard objects for the observer")
        _require(action.tile == drawn if action.is_tsumogiri else action.tile in known, "Discard source does not match observation")
        i = _index(action.tile)
        if i not in shapes:
            after = list(all_tiles)
            after.remove(action.tile)
            shapes[i] = analyze_shape(after, p["melds"], seen)
        candidates.append({"discard": asdict(action), **deepcopy(shapes[i])})
    candidates.sort(key=lambda c: (c["shanten"], -c["ukeire"], _index(Tile(**c["discard"]["tile"])),
                                   c["discard"]["tile"]["is_red"], c["discard"]["is_tsumogiri"]))
    best = (candidates[0]["shanten"], candidates[0]["ukeire"])
    for c in candidates:
        c["is_best"] = (c["shanten"], c["ukeire"]) == best
    return {"version": ANALYSIS_VERSION, "player": seat, "visible_counts": seen,
            "choice_count": len({str(a.tile) for a in legal}), "candidates": candidates,
            "best_shanten": best[0], "best_ukeire": best[1]}


def compare_discard(analysis, actual):
    selected = next((c for c in analysis["candidates"] if c["discard"] == asdict(actual)), None)
    _require(selected is not None, "Actual discard is not among analyzed legal choices")
    increase = selected["shanten"] - analysis["best_shanten"]
    return {"selected": deepcopy(selected), "forced": analysis["choice_count"] == 1,
            "shanten_increase": increase,
            "ukeire_loss": analysis["best_ukeire"] - selected["ukeire"] if increase == 0 else None}


def summarize_reviews(records):
    """Exclude forced choices from quality statistics; None means no denominator."""
    records = list(records)
    choices = [r["comparison"] for r in records if not r["comparison"]["forced"]]
    losses = [c["ukeire_loss"] for c in choices if c["ukeire_loss"] is not None]
    best = sum(c["selected"]["is_best"] for c in choices)
    return {"discards": len(records), "forced": len(records) - len(choices), "choices": len(choices),
            "optimal_choices": best, "optimal_rate": best / len(choices) if choices else None,
            "shanten_increase_count": sum(c["shanten_increase"] > 0 for c in choices),
            "same_shanten_count": len(losses), "ukeire_loss_total": sum(losses),
            "ukeire_loss_mean": sum(losses) / len(losses) if losses else None}
