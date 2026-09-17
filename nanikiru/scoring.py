"""Local adapter for mahjong 2.0.0; no game progression or hidden-state inference."""

from collections import Counter
from dataclasses import dataclass
from functools import lru_cache

from mahjong.agari import Agari
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld as LibraryMeld

from .models import MeldKind, Tile

WINDS = ("east", "south", "west", "north")
TERMINALS = frozenset((0, 8, 9, 17, 18, 26, *range(27, 34)))


@dataclass(frozen=True)
class Rules:
    aka_dora_enabled: bool = True
    kan_dora_enabled: bool = True

    def __post_init__(self):
        if type(self.aka_dora_enabled) is not bool or type(self.kan_dora_enabled) is not bool:
            raise ValueError("Rule switches must be boolean")


def index(tile):
    return "mpsz".index(tile.suit) * 9 + tile.rank - 1


def tile34(value):
    return Tile("mpsz"[value // 9], value % 9 + 1)


@lru_cache(maxsize=4096)
def _waits(counts):
    result = set()
    for i, n in enumerate(counts):
        if n < 4:
            candidate = list(counts)
            candidate[i] += 1
            if Agari.is_agari(candidate):
                result.add(i)
    return frozenset(result)


def waits(tiles):
    counts = Counter(index(t) for t in tiles)
    return _waits(tuple(counts[i] for i in range(34)))


def evaluate(player, win_tile, *, tsumo=False, round_wind="east", dealer=0,
             indicators=(), ura=(), rules=Rules(), **flags):
    """Return serializable han/fu/base payments, or None for incomplete/no-yaku hands.

    Concealed hand excludes the winning tile. Allocate unique library tile IDs
    across hand and melds, reserving copy 0 of each five for the physical red tile.
    """
    used = set()

    def allocate(tile):
        base = index(tile) * 4
        copies = (0,) if tile.is_red else ((1, 2, 3) if tile.suit != "z" and tile.rank == 5 else range(4))
        for copy in copies:
            if base + copy not in used:
                used.add(base + copy)
                return base + copy
        raise ValueError("Hand contains more physical copies than the tile inventory")

    tiles = [allocate(t) for t in player.hand.known_tiles]
    winning = allocate(win_tile)
    tiles.append(winning)
    melds = []
    for m in player.melds:
        ids = [allocate(t) for t in m.tiles]
        tiles.extend(ids)
        kind = {MeldKind.CHI: LibraryMeld.CHI, MeldKind.PON: LibraryMeld.PON}.get(m.kind, LibraryMeld.KAN)
        melds.append(LibraryMeld(kind, ids, opened=m.kind != MeldKind.CLOSED_KAN))
    config = HandConfig(is_tsumo=tsumo, is_riichi=player.riichi,
                        player_wind=27 + (player.seat - dealer) % 4,
                        round_wind=27 + WINDS.index(round_wind),
                        options=OptionalRules(has_open_tanyao=True, has_aka_dora=rules.aka_dora_enabled,
                                              has_double_yakuman=True, kiriage=False), **flags)
    response = HandCalculator.estimate_hand_value(
        tiles, winning, melds=melds, config=config,
        dora_indicators=[index(t) * 4 for t in indicators],
        ura_dora_indicators=[index(t) * 4 for t in ura] if player.riichi else [])
    if response.error:
        return None
    return {"han": response.han, "fu": response.fu, "cost": response.cost,
            "yaku": [{"name": y.name, "han": y.han_open if response.is_open_hand else y.han_closed,
                      "yakuman": y.is_yakuman} for y in response.yaku],
            "fu_details": response.fu_details}
