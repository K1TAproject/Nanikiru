"""Retained mahjong 2.0.0 adapter: conditional values, not win permission."""

from functools import lru_cache

from mahjong.agari import Agari
from mahjong.hand_calculating.hand import HandCalculator
from mahjong.hand_calculating.hand_config import HandConfig, OptionalRules
from mahjong.meld import Meld as LibraryMeld

from ..models import UNKNOWN, valid_seat
from ..tiles import counts34, index
from .config import RuleConfig

WINDS = ("east", "south", "west", "north")


def validate_groups(groups):
    """Validate transient (kind, codes) pairs; no second stored meld model."""
    if len(groups) > 4:
        raise ValueError("At most four groups")
    for kind, codes in groups:
        if kind not in ("chi", "pon", "open_kan", "closed_kan", "added_kan"):
            raise ValueError("Invalid group kind")
        if len(codes) != (3 if kind in ("chi", "pon") else 4):
            raise ValueError("Invalid group size")
        ids = sorted(index(c) for c in codes)
        if kind == "chi":
            if ids[0] >= 27 or ids[0] // 9 != ids[-1] // 9 or ids != list(range(ids[0], ids[0] + 3)):
                raise ValueError("Invalid chi")
        elif len(set(ids)) != 1:
            raise ValueError("Pon/kan must contain matching codes")


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


def waits(tiles, groups=(), *, formal=False):
    """Shape-completing indices, excluding a fifth tile in own hand/groups."""
    if tiles is UNKNOWN or groups is UNKNOWN:
        return UNKNOWN
    tiles, groups = tuple(tiles), tuple(groups)
    validate_groups(groups)
    if len(tiles) != 13 - 3 * len(groups):
        raise ValueError("Expected a 13-tile-equivalent shape")
    counts = counts34(tiles)
    held = counts34((*tiles, *(c for _, codes in groups for c in codes)))
    return frozenset(i for i in _waits(tuple(counts)) if formal or held[i] < 4)


def ron_restrictions(waiting, discarded, temporary, riichi, *, discards_complete):
    """Condition helper only; caller supplies derived/initial furiten facts."""
    discarded = tuple(discarded)
    reasons = []
    if discards_complete is not UNKNOWN and type(discards_complete) is not bool:
        raise ValueError("Discard coverage must be bool or UNKNOWN")
    if waiting is not UNKNOWN and set(waiting) & {index(t) for t in discarded if t is not UNKNOWN}:
        reasons.append("discard_furiten")
    elif waiting is UNKNOWN or discards_complete is not True or UNKNOWN in discarded:
        reasons.append("discard_furiten_unknown")
    for name, value in (("temporary_furiten", temporary), ("riichi_furiten", riichi)):
        if value is True:
            reasons.append(name)
        elif value is UNKNOWN:
            reasons.append(name + "_unknown")
        elif value is not False:
            raise ValueError("Furiten state must be boolean or UNKNOWN")
    return reasons


def evaluate(concealed, groups, win_tile, *, seat, dealer, round_wind,
             tsumo=False, riichi=False, indicators=(), ura=(), aka_dora_enabled=True, rules=None, **flags):
    """Conditional han/fu/base payments, or None for incomplete/no-yaku hands.

Inputs are codes; groups are (kind, codes) pairs. Concealed excludes win_tile.
Omitted flags assume false, not unknown. This never checks response/furiten,
awards deposits/honba, assigns pao, or predicts future ura.
"""
    concealed, groups = tuple(concealed), tuple(groups)
    indicators, ura = tuple(indicators), tuple(ura)
    rules = RuleConfig(aka_dora=aka_dora_enabled) if rules is None else rules
    if not isinstance(rules, RuleConfig):
        raise ValueError("rules must be RuleConfig")
    if not rules.kan_dora:
        indicators = indicators[:1]
    if not rules.ura_dora:
        ura = ()
    elif not rules.kan_ura or not rules.kan_dora:
        ura = ura[:1]
    if not rules.ippatsu and "is_ippatsu" in flags:
        flags["is_ippatsu"] = False
    validate_groups(groups)
    if not valid_seat(seat) or not valid_seat(dealer) or round_wind not in WINDS:
        raise ValueError("Scoring requires known seats and round wind")
    if any(type(v) is not bool for v in (tsumo, riichi, aka_dora_enabled, *flags.values())):
        raise ValueError("Scoring conditions must be known booleans")
    if len(concealed) != 13 - 3 * len(groups):
        raise ValueError("Concealed must exclude winning tile")
    counts34((*concealed, win_tile, *(c for _, codes in groups for c in codes), *indicators, *ura))
    used = set()

    def allocate(code):
        base = index(code) * 4
        copies = (0,) if code[0] == "0" else ((1, 2, 3) if code in ("5m", "5p", "5s") else range(4))
        for copy in copies:
            if base + copy not in used:
                used.add(base + copy)
                return base + copy
        raise ValueError("Hand exceeds physical inventory")

    tiles = [allocate(t) for t in concealed]
    winning = allocate(win_tile)
    tiles.append(winning)
    melds = []
    for group_kind, codes in groups:
        # The library derives a chi block from its first tile. Observation
        # order (called tile first, for example) is not numeric tile order.
        ids = sorted(allocate(t) for t in codes)
        tiles.extend(ids)
        kind = {"chi": LibraryMeld.CHI, "pon": LibraryMeld.PON}.get(group_kind, LibraryMeld.KAN)
        melds.append(LibraryMeld(kind, ids, opened=group_kind != "closed_kan"))
    config = HandConfig(is_tsumo=tsumo, is_riichi=riichi,
                        player_wind=27 + (seat - dealer) % 4,
                        round_wind=27 + WINDS.index(round_wind),
                        options=OptionalRules(
                            has_open_tanyao=rules.open_tanyao, has_aka_dora=rules.aka_dora,
                            has_double_yakuman=rules.double_yakuman,
                            kazoe_limit=0 if rules.kazoe_yakuman else 1,
                            kiriage=rules.kiriage), **flags)
    response = HandCalculator.estimate_hand_value(
        tiles, winning, melds=melds, config=config,
        dora_indicators=[index(t) * 4 for t in indicators],
        ura_dora_indicators=[index(t) * 4 for t in ura] if riichi else [])
    if response.error:
        if response.error in (HandCalculator.ERR_HAND_NOT_WINNING, HandCalculator.ERR_NO_YAKU):
            return None
        raise ValueError(f"Invalid scoring conditions: {response.error}")
    return {"han": response.han, "fu": response.fu, "cost": response.cost,
            "yaku": [{"name": y.name, "han": y.han_open if response.is_open_hand else y.han_closed,
                      "yakuman": y.is_yakuman} for y in response.yaku],
            "fu_details": response.fu_details}
