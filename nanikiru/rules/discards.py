"""Shared discard restriction calculation; no access to opponents or a wall."""

from ..models import UNKNOWN
from ..tiles import index
from .call_rules import call_banned
from .observed_facts import event_fact, riichi_status


def discard_ids(hand, *, riichi, drawn_id, banned=()):
    return tuple(tile_id for tile_id, code in hand
                 if index(code) not in banned and (not riichi or tile_id == drawn_id))


def observed_discards(position, history, coverage):
    """Require the relevant facts, not necessarily a full game prefix."""
    if position.phase != "await_discard" or position.actor != position.observer_seat:
        return UNKNOWN
    seat = position.observer_seat
    hand = [(t.id, t.code) for t in position.tiles if t.zone == "hand" and t.seat == seat]
    if (position.completeness.get(f"hand:{seat}") is not True
            or position.completeness.get(f"groups:{seat}") is not True
            or next(p for p in position.players if p.seat == seat).untracked_hand_count != 0
            or any(c is UNKNOWN for _, c in hand)):
        return UNKNOWN
    kind = lambda e: event_fact(e)[0]
    latest_own = next((e for e in reversed(history) if e.actor == seat), None)
    if latest_own and kind(latest_own) == "riichi_declared":
        return UNKNOWN  # The declaration-discard choice is sampled before this event.
    ready = riichi_status(position, history, seat, coverage)
    if ready is UNKNOWN:
        return UNKNOWN
    if not ready and (not latest_own or kind(latest_own) not in ('draw', 'chi', 'pon')):
        return UNKNOWN  # A snapshot alone cannot exclude a pending declaration.
    drawn = position.draw_context.get("tile_id") if isinstance(position.draw_context, dict) else None
    if ready and (drawn is UNKNOWN or drawn is None):
        return UNKNOWN
    banned = set()
    latest = next((e for e in reversed(history) if e.actor == seat and kind(e) in
                   ("draw", "chi", "pon", "discard")), None)
    if not ready and position.rules.forbid_kuikae and drawn in (None, UNKNOWN):
        if not latest or kind(latest) not in ('chi', 'pon'):
            return UNKNOWN
        fact = event_fact(latest)[1]
        called = next(t.code for t in position.tiles if t.id == fact["called"])
        if called is UNKNOWN or UNKNOWN in fact['codes']:
            return UNKNOWN
        banned = call_banned(kind(latest), fact["codes"], called)
    return discard_ids(hand, riichi=ready, drawn_id=drawn, banned=banned)
