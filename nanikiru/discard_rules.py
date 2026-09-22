"""Discard constraints shared by the environment and public projections."""

from .models import Tile
from .recorder import require
from .scoring import index


def validate_discard(hand, riichi, action, banned=()):
    require(isinstance(action.tile, Tile) and type(action.is_tsumogiri) is bool,
            "Specify a known tile and whether it is the drawn tile")
    if action.is_tsumogiri:
        require(hand.drawn_tile is not None and action.tile == hand.drawn_tile.tile, "Not the drawn tile")
    else:
        require(action.tile in hand.known_tiles, "Tile not in concealed hand")
    require(not riichi or action.is_tsumogiri, "After riichi only discard the drawn tile")
    require(index(action.tile) not in banned, "Kuikae (swap discard after call) is forbidden")


def first_uninterrupted_discard(interrupted, prior_discards):
    return not interrupted and prior_discards == 0
