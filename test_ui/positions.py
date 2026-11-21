"""Synthetic visible positions, without opponent hands or a retained wall."""

import random
from dataclasses import replace

from nanikiru.models import PlayerInfo, RoundSnapshot, TileRecord, RULE_ID
from nanikiru.tiles import index, physical_capacity, tile34

TILE_CODES = tuple(sorted([tile34(i) for i in range(34)] + ["0m", "0p", "0s"],
                          key=lambda code: (index(code), code[0] != "0")))


def random_position(rng=None):
    rng = rng if rng is not None else random.Random()
    deck = [code for code in TILE_CODES for _ in range(physical_capacity(code))]
    dealt = rng.sample(deck, 15)
    return make_position(dealt[:13], dealt[13], dealt[14])


def make_position(hand, drawn, indicator):
    """13 concealed + one drawn + one indicator; no prior public discards."""
    hand = tuple(hand)
    if len(hand) != 13:
        raise ValueError("The sandbox requires thirteen concealed tiles")
    return RoundSnapshot(
        round_id="single-position", rule_id=RULE_ID, observer_seat=0,
        players=tuple(PlayerInfo(s, 25000, 0 if s == 0 else 13) for s in range(4)),
        tiles=(*[TileRecord(f"h{i}", code, "hand", 0) for i, code in enumerate(hand)],
               TileRecord("draw", drawn, "hand", 0),
               TileRecord("indicator", indicator, "dora", None)),
        phase="await_discard", actor=0,
        draw_context={"player": 0, "tile_id": "draw", "source": "live"}, pending=None,
        completeness={"hand:0": True, "groups:0": True, "public_tiles": True},
    )


def sorted_hand(position):
    drawn = position.draw_context["tile_id"]
    return sorted((t for t in position.tiles if t.zone == "hand" and t.id != drawn),
                  key=lambda t: (index(t.code), t.code[0] != "0", t.id))


def replace_tile(position, tile_id, code):
    """Edit a sandbox slot, validating inventory before returning a new view."""
    if tile_id not in {t.id for t in position.tiles}:
        raise ValueError("Unknown tile slot")
    return replace(position, tiles=tuple(replace(t, code=code) if t.id == tile_id else t for t in position.tiles))
