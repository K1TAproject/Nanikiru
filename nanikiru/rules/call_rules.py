"""Shared discard restrictions after calls, independent of hidden game state."""

from ..tiles import index
from .scoring import validate_groups


def call_banned(kind, contributed, called):
    """Code-based kuikae restriction for an already established chi/pon."""
    if kind not in ("chi", "pon"):
        raise ValueError("Kuikae helper accepts chi/pon only")
    contributed = tuple(contributed)
    validate_groups(((kind, (*contributed, called)),))
    banned = {index(called)}
    if kind == "chi":
        ranks = sorted(index(t) for t in (*contributed, called))
        if index(called) == ranks[0] and ranks[-1] % 9 < 8:
            banned.add(ranks[-1] + 1)
        if index(called) == ranks[-1] and ranks[0] % 9 > 0:
            banned.add(ranks[0] - 1)
    return banned
