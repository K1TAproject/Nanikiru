"""Retained shape calculations over codes and observer snapshots.

No action generation, legal-action claim, policy ranking or game progression.
"""

from collections import Counter
from functools import lru_cache

from mahjong.shanten import Shanten

from ..models import UNKNOWN
from ..tiles import counts34, tile34


@lru_cache(maxsize=32768)
def _shanten(counts, special):
    return Shanten.calculate_shanten(counts, use_chiitoitsu=special, use_kokushi=special)


def calculate_shanten(tiles, groups=()):
    """Minimum shape shanten for a 13/14-equivalent hand; -1 means complete.

Unknown input stays UNKNOWN. Declared groups (including closed kans) disable
seven-pairs/orphans shapes. This says nothing about yaku or win permission.
"""
    if tiles is UNKNOWN or groups is UNKNOWN:
        return UNKNOWN
    tiles, groups = tuple(tiles), tuple(groups)
    if UNKNOWN in tiles or any(UNKNOWN in codes for _, codes in groups):
        return UNKNOWN
    from ..rules.scoring import validate_groups
    validate_groups(groups)
    if len(tiles) not in (13 - 3 * len(groups), 14 - 3 * len(groups)):
        raise ValueError("Expected a 13/14-tile-equivalent concealed hand")
    counts34((*tiles, *(c for _, codes in groups for c in codes)))
    return _shanten(tuple(counts34(tiles)), not groups)


def hand_codes(position):
    """Known observer hand, including its drawn tile once; UNKNOWN if incomplete."""
    position.validate()
    seat = position.observer_seat
    player = next(p for p in position.players if p.seat == seat)
    codes = tuple(t.code for t in position.tiles if t.zone == "hand" and t.seat == seat)
    if player.untracked_hand_count != 0 or position.completeness.get(f"hand:{seat}") is not True or UNKNOWN in codes:
        return UNKNOWN
    return codes


def meld_codes(position):
    """Transient (kind, codes) pairs, derived from the unified table."""
    position.validate()
    seat = position.observer_seat
    if position.completeness.get(f"groups:{seat}") is not True:
        return UNKNOWN
    result = []
    for group_id, group in position.groups.items():
        members = [t for t in position.tiles if t.group_id == group_id]
        if members[0].seat == seat:
            if any(t.code is UNKNOWN for t in members):
                return UNKNOWN
            result.append((group["kind"], tuple(t.code for t in members)))
    return tuple(result)


def visible_inventory(position):
    """Known physical inventory only, never an assertion of complete coverage."""
    position.validate()
    return Counter(t.code for t in position.tiles if t.code is not UNKNOWN)


def visible_counts(position):
    """Exact known-to-observer coverage, or UNKNOWN (not incomplete counts)."""
    position.validate()
    if position.completeness.get("public_tiles") is not True or hand_codes(position) is UNKNOWN:
        return UNKNOWN
    if any(t.code is UNKNOWN for t in position.tiles if t.zone != "hand"):
        return UNKNOWN
    return counts34(visible_inventory(position).elements())


def analyze_shape(tiles, groups=(), seen=UNKNOWN):
    """13-equivalent shape. Unknown visibility still permits shape analysis.

groups contains (kind, codes) pairs. seen includes all known physical tiles,
including the proposed discard; it must not be reduced after discarding.
"""
    if tiles is UNKNOWN or groups is UNKNOWN:
        return {"shanten": UNKNOWN, "effective_tiles": UNKNOWN, "ukeire": UNKNOWN}
    tiles = tuple(tiles)
    groups = tuple(groups)
    from ..rules.scoring import validate_groups
    validate_groups(groups)
    if len(tiles) != 13 - 3 * len(groups):
        raise ValueError("Expected a 13-tile-equivalent concealed shape")
    counts = counts34(tiles)
    held = counts34((*tiles, *(c for _, codes in groups for c in codes)))
    if seen is not UNKNOWN:
        if len(seen) != 34 or any(type(n) is not int or not 0 <= n <= 4 for n in seen):
            raise ValueError("seen must contain 34 counts or UNKNOWN")
        if any(s < h for s, h in zip(seen, held)):
            raise ValueError("Visible inventory must include the held tiles")
    shanten = _shanten(tuple(counts), not groups)
    effective = []
    for i in range(34):
        if held[i] >= 4 or (seen is not UNKNOWN and seen[i] >= 4):
            continue
        trial = counts.copy()
        trial[i] += 1
        if _shanten(tuple(trial), not groups) < shanten:
            effective.append({"tile": tile34(i), "unseen": UNKNOWN if seen is UNKNOWN else 4 - seen[i]})
    return {"shanten": shanten, "effective_tiles": effective,
            "ukeire": UNKNOWN if seen is UNKNOWN else sum(t["unseen"] for t in effective)}


def analyze_discards(position, tile_ids):
    """Evaluate supplied hand references, WITHOUT asserting they are legal.

The caller decides which shape candidates to inspect. No automatic riichi,
kuikae, defense or recommendation is inferred by this retained utility.
"""
    position.validate()
    hand, groups = hand_codes(position), meld_codes(position)
    by_id = {t.id: t for t in position.tiles}
    seen = visible_counts(position)
    candidates = []
    for tile_id in dict.fromkeys(tile_ids):
        tile = by_id.get(tile_id)
        if tile is None or tile.zone != "hand" or tile.seat != position.observer_seat:
            raise ValueError("Candidate must reference the observer's hand")
        after = UNKNOWN
        if hand is not UNKNOWN:
            after = list(hand)
            after.remove(tile.code)
        candidates.append({"tile_id": tile_id, **analyze_shape(after, groups, seen)})
    return candidates
