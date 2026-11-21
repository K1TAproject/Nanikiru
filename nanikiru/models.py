"""Observer facts. UNKNOWN is unknown; None is absent/not applicable.

Records do not advance a game or derive player qualifications. Mutable containers
belong to their caller; there is no implicit copying or persistence.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, TypedDict

from .tiles import counts34, index, validate_code
from .rules.config import RuleConfig


class Unknown(str, Enum):
    VALUE = "unknown"


UNKNOWN = Unknown.VALUE
RULE_ID = "riichi-4p-v1"
Zone = Literal["hand", "river", "meld", "dora", "ura", "declared"]
MeldKind = Literal["chi", "pon", "open_kan", "closed_kan", "added_kan"]
Phase = Literal["await_draw", "await_discard", "await_response", "await_kan_response", "await_replacement", "ended"]


def valid_seat(seat):
    return type(seat) is int and 0 <= seat < 4


class GroupInfo(TypedDict):
    kind: MeldKind
    called_tile_id: str | Unknown | None
    source_seat: int | Unknown | None


class DrawContext(TypedDict):
    player: int
    tile_id: str | Unknown
    source: Literal["live", "replacement"] | Unknown


class PendingResponse(TypedDict):
    trigger_event: int | Unknown
    kind: Literal["discard", "closed_kan", "added_kan"]
    source_seat: int | Unknown
    tile_id: str | Unknown
    responses: dict[int, str | Unknown]


@dataclass(frozen=True)
class TileRecord:
    id: str
    code: str | Unknown
    zone: Zone
    seat: int | None
    group_id: str | None = None

    def __post_init__(self):
        if not isinstance(self.id, str) or not self.id:
            raise ValueError("Tile id must be nonempty")
        if self.code is not UNKNOWN:
            validate_code(self.code)
        if self.zone not in ("hand", "river", "meld", "dora", "ura", "declared"):
            raise ValueError("Invalid tile zone")
        if self.zone in ("dora", "ura"):
            if self.seat is not None:
                raise ValueError("Indicators have no player")
        elif not valid_seat(self.seat):
            raise ValueError("Player zone requires a seat")
        if self.zone == "meld":
            if not isinstance(self.group_id, str) or not self.group_id:
                raise ValueError("Meld tile requires a group")
        elif self.group_id is not None:
            raise ValueError("Only meld tiles belong to groups")


@dataclass(frozen=True)
class PlayerInfo:
    seat: int
    score: int | Unknown = UNKNOWN
    untracked_hand_count: int | Unknown = UNKNOWN

    def __post_init__(self):
        if not valid_seat(self.seat):
            raise ValueError("Invalid player seat")
        if self.score is not UNKNOWN and type(self.score) is not int:
            raise ValueError("Score must be an integer or UNKNOWN")
        n = self.untracked_hand_count
        if n is not UNKNOWN and (type(n) is not int or not 0 <= n <= 14):
            raise ValueError("Untracked hand count must be 0..14 or UNKNOWN")


@dataclass
class RoundSnapshot:
    round_id: str
    rule_id: str
    observer_seat: int
    players: tuple[PlayerInfo, ...]
    tiles: tuple[TileRecord, ...] = ()
    groups: dict[str, GroupInfo] = field(default_factory=dict)
    round_wind: str | Unknown = UNKNOWN
    hand_number: int | Unknown = UNKNOWN
    dealer_seat: int | Unknown = UNKNOWN
    honba: int | Unknown = UNKNOWN
    riichi_sticks: int | Unknown = UNKNOWN
    phase: Phase | Unknown = UNKNOWN
    actor: int | Unknown | None = UNKNOWN
    draw_context: DrawContext | Unknown | None = UNKNOWN
    pending: PendingResponse | Unknown | None = UNKNOWN
    remaining_draws: int | Unknown = UNKNOWN
    initial_context: dict = field(default_factory=dict)
    # Missing keys mean UNKNOWN; e.g. public_tiles, hand:0, groups:0.
    completeness: dict[str, bool | Unknown] = field(default_factory=dict)
    rules: RuleConfig = field(default_factory=RuleConfig)

    def __post_init__(self):
        self.validate()

    def validate(self):
        """Check structural consistency; this is not a full rules validator."""
        if not isinstance(self.rules, RuleConfig):
            raise ValueError("rules must be RuleConfig")
        if not isinstance(self.initial_context, dict):
            raise ValueError("initial_context must be a dictionary")
        if not isinstance(self.round_id, str) or not self.round_id or self.rule_id != RULE_ID:
            raise ValueError("Specify round id and supported rule_id")
        if not valid_seat(self.observer_seat):
            raise ValueError("Invalid observer")
        if len(self.players) != 4 or {p.seat for p in self.players} != set(range(4)):
            raise ValueError("Exactly four unique player seats required")
        by_id = {t.id: t for t in self.tiles}
        if len(by_id) != len(self.tiles):
            raise ValueError("Duplicate physical tile id")
        for tile in self.tiles:
            if tile.zone == "hand" and tile.seat != self.observer_seat and tile.code is not UNKNOWN:
                raise ValueError("Opponent concealed codes cannot enter an observer snapshot")
            if tile.group_id is not None and tile.group_id not in self.groups:
                raise ValueError("Missing meld group")
        for group_id, group in self.groups.items():
            kind = group["kind"]
            if kind not in ("chi", "pon", "open_kan", "closed_kan", "added_kan"):
                raise ValueError("Invalid meld kind")
            members = [t for t in self.tiles if t.group_id == group_id]
            if len(members) != (3 if kind in ("chi", "pon") else 4) or len({t.seat for t in members}) != 1:
                raise ValueError("Meld requires full membership and one owner; codes may be UNKNOWN")
            called, source = group["called_tile_id"], group["source_seat"]
            if kind == "closed_kan":
                if called is not None or source is not None:
                    raise ValueError("Closed kan has no called tile/source")
            else:
                if called is not UNKNOWN and called not in {t.id for t in members}:
                    raise ValueError("Called tile must belong to its group")
                if source is not UNKNOWN and (not valid_seat(source) or source == members[0].seat):
                    raise ValueError("Invalid meld source")
            if all(t.code is not UNKNOWN for t in members):
                ids = sorted(index(t.code) for t in members)
                if kind == "chi":
                    if ids[0] >= 27 or ids[0] // 9 != ids[-1] // 9 or ids != list(range(ids[0], ids[0] + 3)):
                        raise ValueError("Invalid chi shape")
                elif len(set(ids)) != 1:
                    raise ValueError("Pon/kan codes must match")
        counts34(t.code for t in self.tiles if t.code is not UNKNOWN)
        for key in ("dealer_seat", "actor"):
            value = getattr(self, key)
            if value is not UNKNOWN and not (key == "actor" and value is None) and not valid_seat(value):
                raise ValueError(f"Invalid {key}")
        if self.round_wind is not UNKNOWN and self.round_wind not in ("east", "south", "west", "north"):
            raise ValueError("Invalid round wind")
        if self.phase is not UNKNOWN and self.phase not in ("await_draw", "await_discard", "await_response", "await_kan_response", "await_replacement", "ended"):
            raise ValueError("Invalid phase")
        for key in ("hand_number", "honba", "riichi_sticks", "remaining_draws"):
            value = getattr(self, key)
            if value is not UNKNOWN and (type(value) is not int or value < (1 if key == "hand_number" else 0)):
                raise ValueError(f"Invalid {key}")
        if self.hand_number is not UNKNOWN and self.hand_number > 4:
            raise ValueError("Hand number must be 1..4")
        if any(v is not UNKNOWN and type(v) is not bool for v in self.completeness.values()):
            raise ValueError("Completeness must be bool or UNKNOWN")
        if isinstance(self.draw_context, dict):
            draw = self.draw_context
            if not valid_seat(draw["player"]) or draw["source"] not in ("live", "replacement", UNKNOWN):
                raise ValueError("Invalid draw context")
            if draw["tile_id"] is not UNKNOWN:
                tile = by_id.get(draw["tile_id"])
                if tile is None or tile.zone != "hand" or tile.seat != draw["player"]:
                    raise ValueError("Draw must reference that player's hand")
        elif self.draw_context is not None and self.draw_context is not UNKNOWN:
            raise ValueError("Invalid draw context")
        if self.pending is not None and self.pending is not UNKNOWN:
            if not isinstance(self.pending, dict) or self.pending["kind"] not in ("discard", "closed_kan", "added_kan"):
                raise ValueError("Invalid pending response")
            source = self.pending["source_seat"]
            if source is not UNKNOWN and not valid_seat(source):
                raise ValueError("Invalid response source")
            tile_id = self.pending["tile_id"]
            if tile_id is not UNKNOWN and tile_id not in by_id:
                raise ValueError("Missing response tile")


@dataclass
class Event:
    id: int
    kind: str
    actor: int | Unknown | None = None
    target_id: int | Unknown | None = None
    data: dict = field(default_factory=dict)

    def __post_init__(self):
        if type(self.id) is not int or self.id < 1 or not isinstance(self.kind, str) or not self.kind:
            raise ValueError("Event needs a positive local id and kind")
        if self.actor is not None and self.actor is not UNKNOWN and not valid_seat(self.actor):
            raise ValueError("Invalid event actor")
        if self.target_id is not None and self.target_id is not UNKNOWN:
            if type(self.target_id) is not int or not 0 < self.target_id < self.id:
                raise ValueError("Target must precede event; entry-time targets may be UNKNOWN")


@dataclass
class RoundRecord:
    initial_snapshot: RoundSnapshot
    events: tuple[Event, ...] = ()
    coverage: dict = field(default_factory=lambda: {"from_start": UNKNOWN, "gaps": []})
    schema_version: str = "observation-v1"

    def __post_init__(self):
        if self.schema_version != "observation-v1":
            raise ValueError("Legacy Game/Recorder formats are not observation records")
        ids = [event.id for event in self.events]
        if ids != sorted(set(ids)):
            raise ValueError("Event ids must be unique and increasing")
        if any(type(e.target_id) is int and e.target_id not in ids for e in self.events):
            raise ValueError("Missing target event")
