"""Shared discard restrictions after calls, independent of hidden game state."""

from .scoring import index


def call_banned(action, called):
    banned = {index(called)}
    if action.kind == "chi":
        ranks = sorted(index(t) for t in (*action.tiles, called))
        if index(called) == ranks[0] and ranks[-1] % 9 < 8:
            banned.add(ranks[-1] + 1)
        if index(called) == ranks[-1] and ranks[0] % 9 > 0:
            banned.add(ranks[0] - 1)
    return banned
