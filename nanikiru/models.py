"""Data types. Seats advance 0 -> 1 -> 2 -> 3; z ranks are ESWNPFC."""

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class Tile:
    suit: str
    rank: int
    is_red: bool = False

    def __post_init__(self):
        if (self.suit not in ("m", "p", "s", "z")
                or type(self.rank) is not int
                or not 1 <= self.rank <= (7 if self.suit == "z" else 9)
                or type(self.is_red) is not bool
                or (self.is_red and (self.suit == "z" or self.rank != 5))):
            raise ValueError("Invalid tile")

    @classmethod
    def parse(cls, text: str) -> "Tile":
        """1m..9m, 1p..9p, 1s..9s, 1z..7z; 0m/0p/0s are red fives."""
        if not isinstance(text, str) or len(text) != 2 or text[0] not in "0123456789":
            raise ValueError("Tile must be a two-character code")
        return cls(text[1], 5 if text[0] == "0" else int(text[0]), text[0] == "0")

    def __str__(self):
        return f'{0 if self.is_red else self.rank}{self.suit}'


@dataclass
class DrawnTile:
    tile: Tile | None = None  # Present wrapper + None means an unknown draw.


@dataclass
class HandState:
    known_tiles: list[Tile] = field(default_factory=list)
    unknown_count: int = 0
    drawn_tile: DrawnTile | None = None

    @property
    def count(self):
        return len(self.known_tiles) + self.unknown_count + (self.drawn_tile is not None)


class MeldKind(str, Enum):
    CHI = "chi"
    PON = "pon"
    OPEN_KAN = "open_kan"
    CLOSED_KAN = "closed_kan"
    ADDED_KAN = "added_kan"


@dataclass
class Meld:
    kind: MeldKind
    tiles: list[Tile]
    source_discard_id: str | None = None


@dataclass
class DiscardRecord:
    id: str
    tile: Tile
    is_tsumogiri: bool | None = None
    is_riichi_declaration: bool = False
    claimed_by: int | None = None


@dataclass
class PlayerState:
    seat: int
    score: int
    hand: HandState
    discards: list[DiscardRecord] = field(default_factory=list)
    melds: list[Meld] = field(default_factory=list)
    riichi: bool = False  # Only established riichi is accepted in a stable snapshot.


class Phase(str, Enum):
    DISCARD = "await_discard"
    RESPONSE = "await_response"
    EXHAUSTED = "wall_exhausted"
    ENDED = "ended"


@dataclass
class RoundState:
    players: list[PlayerState]
    observer_seat: int
    dealer_seat: int
    actor: int
    dora_indicators: list[Tile]
    hand_number: int = 1
    honba: int = 0
    riichi_sticks: int = 0
    round_wind: str = "east"
    phase: Phase = Phase.DISCARD

    @property
    def remaining_draws(self):
        """Stable snapshots only; all kan replacement draws must be complete."""
        outside = sum(
            p.hand.count + sum(len(m.tiles) for m in p.melds)
            + sum(d.claimed_by is None for d in p.discards)
            for p in self.players
        )
        return 136 - 14 - outside

    def seat_wind(self, seat: int):
        return ("east", "south", "west", "north")[(seat - self.dealer_seat) % 4]


@dataclass(frozen=True)
class Draw:
    player: int
    tile: Tile | None = None


@dataclass(frozen=True)
class Discard:
    player: int
    tile: Tile
    is_tsumogiri: bool | None = None


@dataclass(frozen=True)
class Decision:
    """Special action; tiles are the exact tiles contributed from this player's hand."""

    player: int
    kind: str
    tiles: tuple[Tile, ...] = ()
    is_tsumogiri: bool = False
