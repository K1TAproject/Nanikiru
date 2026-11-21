"""One-way projection of simulator facts into the existing observer records."""

from copy import deepcopy
from dataclasses import asdict

from ..models import UNKNOWN, Event, PlayerInfo, RoundRecord, RoundSnapshot, TileRecord, RULE_ID, valid_seat


def snapshot(game, seat, event_ids):
    tiles = [TileRecord(t, game._codes[t], "hand", seat) for t in game._hands[seat]]
    groups = {}
    for gid, g in game._groups.items():
        groups[gid] = dict(kind=g["kind"], called_tile_id=g["called"], source_seat=g["source"])
        tiles.extend(TileRecord(t, game._codes[t], "meld", g["seat"], gid) for t in g["tiles"])
    for s, river in enumerate(game._rivers):
        tiles.extend(TileRecord(r["tile"], game._codes[r["tile"]], "river", s) for r in river if not r["called"])
    tiles.extend(TileRecord(game._dead[4+2*i], game._codes[game._dead[4+2*i]], "dora", None)
                 for i in range(game._indicator_count))
    draw = game._draw
    draw_context = None if draw is None else dict(player=draw["seat"], source=draw["source"],
                                                 tile_id=draw["tile"] if draw["seat"] == seat else UNKNOWN)
    p = game._pending
    pending = None
    if p:
        # The robbed tile is publicly declared, but still held until resolution.
        if p["kind"] != "discard" and p["source"] != seat:
            tiles.extend(TileRecord(t, game._codes[t], "declared", p["source"])
                         for t in p["action"].tile_ids)
        pending = dict(trigger_event=event_ids.get(p["event"], UNKNOWN), kind=p["kind"],
                       source_seat=p["source"], tile_id=p["tile"],
                       responses={seat: p["responses"][seat].kind} if seat in p["responses"] else {})
    players = tuple(PlayerInfo(s, game._scores[s], 0 if s == seat else
                              len(game._hands[s]) - sum(t.zone in ("hand", "declared") and t.seat == s for t in tiles)) for s in range(4))
    complete = {"public_tiles": True, **{f"{k}:{s}": True for k in ("hand", "groups") for s in range(4)}}
    return RoundSnapshot(game.round_id, RULE_ID, seat, players, tuple(tiles), groups,
                         game.round_wind, game.hand_number, game.dealer, game.honba, game._sticks,
                         game._phase, game._actor, draw_context, pending, len(game._live),
                         completeness=complete, rules=game.rules)


class ObserverRecords:
    def __init__(self, game):
        self._ids = [{} for _ in range(4)]
        self._positions = [snapshot(game, s, self._ids[s]) for s in range(4)]
        self._records = [RoundRecord(deepcopy(p), coverage={"from_start": True, "gaps": []}) for p in self._positions]

    def position(self, seat):
        if not valid_seat(seat):
            raise ValueError("Invalid observer")
        return deepcopy(self._positions[seat])

    def record(self, seat):
        self.position(seat)
        return deepcopy(self._records[seat])

    def capture(self, game, event, *, private=False):
        for seat in range(4):
            if private and event.actor != seat:
                continue
            record = self._records[seat]
            local_id = len(record.events) + 1
            self._ids[seat][event.id] = local_id
            current = snapshot(game, seat, self._ids[seat])
            before, after = asdict(self._positions[seat]), asdict(current)
            changes = {key: value for key, value in after.items() if value != before[key]}
            fact = deepcopy(event.data)
            if event.kind == "draw" and event.actor != seat:
                fact["tile"], fact["code"] = None, UNKNOWN
            if "target" in fact:
                fact["target"] = self._ids[seat].get(fact["target"], UNKNOWN)
            # Public call tile identities alone are opaque; carry their faces as facts.
            if event.kind in ("chi", "pon", "open_kan", "closed_kan", "added_kan"):
                fact["codes"] = [game._codes[t] for t in fact["tiles"]]
            observed = Event(local_id, "sim_" + event.kind, event.actor, data={"fact": fact, "changes": changes})
            record.events += (observed,)
            self._positions[seat] = current

