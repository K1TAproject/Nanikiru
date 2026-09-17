"""Validated state transitions and versioned JSON replay."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile

from .models import (
    Discard, DiscardRecord, Draw, DrawnTile, HandState, Meld, MeldKind,
    Phase, PlayerState, RoundState, Tile,
)


class RecordError(ValueError):
    """Invalid snapshot, unsupported action, or corrupt recording."""


def require(condition, message):
    if not condition:
        raise RecordError(message)


def seat_valid(seat):
    return type(seat) is int and 0 <= seat < 4


def validate(state: RoundState):
    require(len(state.players) == 4, "Exactly four players required")
    require(all(seat_valid(p.seat) and p.seat == i for i, p in enumerate(state.players)),
            "Players must be ordered by unique seats 0..3")
    require(all(seat_valid(s) for s in (state.actor, state.observer_seat, state.dealer_seat)),
            "Invalid actor, observer or dealer seat")
    require(state.round_wind == "east", "Only east-round snapshots supported")
    require(type(state.hand_number) is int and 1 <= state.hand_number <= 4, "Invalid hand number")
    require(all(type(n) is int and n >= 0 for n in (state.honba, state.riichi_sticks)),
            "Invalid honba or riichi sticks")
    require(isinstance(state.phase, Phase), "Invalid phase")
    all_tiles = list(state.dora_indicators)
    discards = {}
    sources = set()
    kans = 0
    for p in state.players:
        require(type(p.score) is int and type(p.riichi) is bool, "Invalid score or riichi flag")
        h = p.hand
        require(type(h.unknown_count) is int and h.unknown_count >= 0, "Invalid unknown count")
        active_draw = state.phase == Phase.DISCARD and state.actor == p.seat
        require((h.drawn_tile is not None) == active_draw, "Draw slot does not match phase")
        require(len(p.melds) <= 4 and h.count == 13 - 3 * len(p.melds) + active_draw,
                f"Seat {p.seat}: incorrect concealed hand count")
        if p.seat == state.observer_seat:
            require(h.unknown_count == 0 and (not active_draw or h.drawn_tile.tile is not None),
                    "Observer hand must be completely known")
        else:
            require(not h.known_tiles and (not active_draw or h.drawn_tile.tile is None),
                    "Opponent concealed tiles must remain unknown")
        all_tiles.extend(h.known_tiles)
        if active_draw and h.drawn_tile.tile is not None:
            all_tiles.append(h.drawn_tile.tile)
        for d in p.discards:
            require(isinstance(d.id, str) and bool(d.id) and d.id not in discards,
                    "Discard IDs must be nonempty and unique")
            require(d.is_tsumogiri is None or type(d.is_tsumogiri) is bool, "Invalid tsumogiri flag")
            require(type(d.is_riichi_declaration) is bool, "Invalid declaration flag")
            require(d.claimed_by is None or (seat_valid(d.claimed_by) and d.claimed_by != p.seat),
                    "Invalid discard claimant")
            require(not d.is_riichi_declaration or p.riichi, "Declaration without established riichi")
            discards[d.id] = (p.seat, d)
            if d.claimed_by is None:
                all_tiles.append(d.tile)
        require(sum(d.is_riichi_declaration for d in p.discards) <= 1, "Multiple riichi declarations")
        for m in p.melds:
            require(isinstance(m.kind, MeldKind), "Invalid meld kind")
            require(all(isinstance(t, Tile) for t in m.tiles), "Invalid meld tiles")
            is_kan = m.kind not in (MeldKind.CHI, MeldKind.PON)
            require(len(m.tiles) == (4 if is_kan else 3), "Incorrect meld size")
            kans += is_kan
            bases = [(t.suit, t.rank) for t in m.tiles]
            if m.kind == MeldKind.CHI:
                ranks = sorted(t.rank for t in m.tiles)
                require(len({t.suit for t in m.tiles}) == 1 and m.tiles[0].suit != "z"
                        and ranks == list(range(ranks[0], ranks[0] + 3)), "Invalid chi")
            else:
                require(len(set(bases)) == 1, "Pon/kan tiles must match")
            require(not p.riichi or m.kind == MeldKind.CLOSED_KAN, "Riichi hand must be closed")
            if m.kind == MeldKind.CLOSED_KAN:
                require(m.source_discard_id is None, "Closed kan cannot claim a discard")
            else:
                require(isinstance(m.source_discard_id, str) and m.source_discard_id not in sources,
                        "Missing or duplicate meld source")
                sources.add(m.source_discard_id)
            all_tiles.extend(m.tiles)
    for p in state.players:
        for m in p.melds:
            if m.kind == MeldKind.CLOSED_KAN:
                continue
            require(m.source_discard_id in discards, "Meld source not in snapshot river")
            owner, d = discards[m.source_discard_id]
            require(d.claimed_by == p.seat and owner != p.seat and d.tile in m.tiles,
                    "Meld source/claimant/tile mismatch")
            require(m.kind != MeldKind.CHI or owner == (p.seat - 1) % 4, "Chi source must be upstream")
    require({key for key, (_, d) in discards.items() if d.claimed_by is not None} == sources,
            "Claimed discard has no matching meld")
    require(kans <= 4 and len(state.dora_indicators) == 1 + kans,
            "Stable snapshot requires one indicator plus one per completed kan")
    require(all(isinstance(t, Tile) for t in all_tiles), "Invalid tile object")
    base_counts = Counter((t.suit, t.rank) for t in all_tiles)
    require(all(n <= 4 for n in base_counts.values()), "More than four known copies of a tile")
    exact_counts = Counter(all_tiles)
    require(all(n <= (1 if t.is_red else 3 if t.rank == 5 and t.suit != "z" else 4)
                for t, n in exact_counts.items()), "Red/normal five inventory exceeded")
    require(0 <= state.remaining_draws <= 69, "Invalid remaining wall count")
    require(state.phase != Phase.EXHAUSTED or state.remaining_draws == 0, "Wall not exhausted")


class Recorder:
    def __init__(self, initial_state: RoundState):
        validate(initial_state)
        require(initial_state.phase == Phase.DISCARD
                and initial_state.actor == initial_state.observer_seat,
                "Entry must be observer post-draw, awaiting discard")
        self._initial = deepcopy(initial_state)
        self._state = deepcopy(initial_state)
        self._events = []
        self._paused = False

    @property
    def state(self):
        return deepcopy(self._state)

    @property
    def events(self):
        return tuple(self._events)

    @property
    def paused(self):
        return self._paused

    def stop(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def submit(self, action):
        """Validate a private candidate; commit only after all checks pass."""
        try:
            require(type(action) in (Draw, Discard), "Unsupported action; only Draw/Discard supported")
            require(not self._paused, "Recording paused; call resume()")
            require(seat_valid(action.player), "Invalid player")
            s = deepcopy(self._state)
            require(s.phase != Phase.EXHAUSTED, "Wall exhausted")
            p = s.players[action.player]
            h = p.hand
            if isinstance(action, Draw):
                require(s.phase == Phase.RESPONSE and action.player == (s.actor + 1) % 4,
                        "Draw requires next player after a discard")
                require(s.remaining_draws > 0, "No tiles remain")
                require((isinstance(action.tile, Tile) if action.player == s.observer_seat
                         else action.tile is None), "Observer draw must be known; opponent draw unknown")
                h.drawn_tile = DrawnTile(action.tile)
                s.phase, s.actor = Phase.DISCARD, action.player
            else:
                require(s.phase == Phase.DISCARD and action.player == s.actor, "Not this player's discard turn")
                require(isinstance(action.tile, Tile), "Discard tile must be known")
                require(action.is_tsumogiri is None or type(action.is_tsumogiri) is bool,
                        "Invalid tsumogiri flag")
                require(not p.riichi or action.is_tsumogiri is not False, "Established riichi forbids hand discard")
                if action.player == s.observer_seat:
                    require(type(action.is_tsumogiri) is bool, "Observer must specify tsumogiri or hand discard")
                    if action.is_tsumogiri:
                        require(action.tile == h.drawn_tile.tile, "Discard differs from drawn tile")
                    else:
                        require(action.tile in h.known_tiles, "Tile not in concealed hand")
                        h.known_tiles.remove(action.tile)
                        h.known_tiles.append(h.drawn_tile.tile)
                # Opponent draw and discard exchange one unknown tile; identities stay unknown.
                h.drawn_tile = None
                discard_id = f"event:{len(self._events) + 1}"
                used = {d.id for player in s.players for d in player.discards}
                while discard_id in used:
                    discard_id += ":new"
                p.discards.append(DiscardRecord(discard_id, action.tile, action.is_tsumogiri))
                s.phase = Phase.EXHAUSTED if s.remaining_draws == 0 else Phase.RESPONSE
            validate(s)
        except RecordError as exc:
            raise RecordError(f"Event {len(self._events) + 1}: {exc}") from exc
        self._state = s
        self._events.append(action)
        return self.state

    def replay(self, count=None):
        count = len(self._events) if count is None else count
        require(type(count) is int and 0 <= count <= len(self._events), "Invalid replay position")
        replayed = Recorder(self._initial)
        for action in self._events[:count]:
            replayed.submit(action)
        return replayed.state

    def rewind(self, count):
        state = self.replay(count)
        self._state = state
        self._events = self._events[:count]

    def undo(self):
        require(bool(self._events), "No event to undo")
        self.rewind(len(self._events) - 1)

    def save(self, path):
        payload = {
            "schema_version": 1, "initial_state": asdict(self._initial),
            "events": [{"id": i, "type": "draw" if isinstance(a, Draw) else "discard",
                        **asdict(a)} for i, a in enumerate(self._events, 1)],
            "paused": self._paused,
        }
        path = Path(path)
        # Replace only a fully written file so an interrupted save preserves the old record.
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             delete=False) as stream:
                temp_name = stream.name
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            require(type(data["schema_version"]) is int and data["schema_version"] == 1,
                    "Unsupported schema version")
            require(type(data["paused"]) is bool, "Invalid pause flag")
            s = data["initial_state"]
            for p in s["players"]:
                h = p["hand"]
                h["known_tiles"] = [Tile(**t) for t in h["known_tiles"]]
                if h["drawn_tile"] is not None:
                    t = h["drawn_tile"]["tile"]
                    h["drawn_tile"] = DrawnTile(Tile(**t) if t is not None else None)
                p["hand"] = HandState(**h)
                p["discards"] = [DiscardRecord(**{**d, "tile": Tile(**d["tile"])}) for d in p["discards"]]
                p["melds"] = [Meld(**{**m, "kind": MeldKind(m["kind"]),
                                      "tiles": [Tile(**t) for t in m["tiles"]]}) for m in p["melds"]]
            s["players"] = [PlayerState(**p) for p in s["players"]]
            s["dora_indicators"] = [Tile(**t) for t in s["dora_indicators"]]
            s["phase"] = Phase(s["phase"])
            recorder = cls(RoundState(**s))
            for i, event in enumerate(data["events"], 1):
                event = dict(event)
                require(type(event["id"]) is int and event.pop("id") == i, "Nonsequential event ID")
                kind = event.pop("type")
                require(kind in ("draw", "discard"), f"Unsupported event type at {i}: {kind}")
                event["tile"] = Tile(**event["tile"]) if event["tile"] is not None else None
                recorder.submit((Draw if kind == "draw" else Discard)(**event))
            recorder._paused = data["paused"]
            return recorder
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RecordError(f"Invalid recording: {exc}") from exc
