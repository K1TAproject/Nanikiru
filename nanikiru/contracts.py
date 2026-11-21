"""Processing/analysis boundary records, not an analysis engine."""

from dataclasses import dataclass, field
from typing import Literal

from .models import Event, RoundSnapshot, UNKNOWN, valid_seat


@dataclass
class AnalysisRequest:
    state_version: str
    position: RoundSnapshot
    tasks: tuple[str, ...]
    history: tuple[Event, ...] = ()
    coverage: dict = field(default_factory=lambda: {"from_start": UNKNOWN, "gaps": []})
    objective: Literal["shape", "hand_value", "match_rank"] = "shape"
    method: str = "minimal-shape-v1"
    method_options: dict = field(default_factory=dict)

    def __post_init__(self):
        self.position.validate()
        validate_entry_context(self.position.initial_context)
        if (not isinstance(self.coverage, dict) or set(self.coverage) != {'from_start', 'gaps'}
                or (self.coverage['from_start'] is not UNKNOWN and type(self.coverage['from_start']) is not bool)
                or not isinstance(self.coverage['gaps'], list)):
            raise ValueError('Specify history coverage: from_start and gaps')
        if (not isinstance(self.state_version, str) or not self.state_version
                or not isinstance(self.tasks, (tuple, list)) or not self.tasks
                or any(not isinstance(t, str) or not t for t in self.tasks)):
            raise ValueError("Specify state version and requested tasks")
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("Duplicate analysis task")
        if not isinstance(self.method, str) or not self.method:
            raise ValueError("Specify analysis method")
        if not isinstance(self.method_options, dict):
            raise ValueError("method_options must be a dictionary")
        if self.objective not in ("shape", "hand_value", "match_rank"):
            raise ValueError("Invalid objective")
        ids = [e.id for e in self.history]
        if ids != sorted(set(ids)):
            raise ValueError("History must be ordered and unique")


def validate_entry_context(context):
    """Entry-time observations, not mutable copies of current qualifications."""
    if set(context) - {'riichi'}:
        raise ValueError('Unsupported initial_context fields')
    if 'riichi' in context:
        values = context['riichi']
        if (not isinstance(values, (tuple, list)) or len(values) != 4
                or any(v is not UNKNOWN and type(v) is not bool for v in values)):
            raise ValueError('Entry riichi must contain four bool/UNKNOWN values')


def request_from_dict(data):
    """Read exported requests; UNKNOWN markers are restored before validation."""
    from copy import deepcopy
    from .models import PlayerInfo, TileRecord
    from .rules.config import RuleConfig

    def decode(value):
        if isinstance(value, dict):
            if value == {'$unknown': True}: return UNKNOWN
            return {k: decode(v) for k, v in value.items()}
        if isinstance(value, list): return [decode(v) for v in value]
        return value

    try:
        data = decode(deepcopy(data))
        p = data.pop('position')
        p['rules'] = RuleConfig(**p['rules'])
        p['tiles'] = tuple(TileRecord(**t) for t in p['tiles'])
        p['players'] = tuple(PlayerInfo(**v) for v in p['players'])
        if isinstance(p.get('pending'), dict):
            p['pending']['responses'] = {int(k): v for k, v in p['pending']['responses'].items()}
        data['history'] = tuple(Event(**e) for e in data.get('history', ()))
        data['tasks'] = tuple(data['tasks'])
        return AnalysisRequest(position=RoundSnapshot(**p), **data)
    except (TypeError, KeyError, AttributeError) as error:
        raise ValueError('Malformed analysis request') from error


@dataclass(frozen=True)
class Action:
    kind: Literal["discard", "riichi", "chi", "pon", "open_kan", "closed_kan", "added_kan", "pass", "ron", "tsumo", "abort_nine"]
    actor: int
    tile_ids: tuple[str, ...] = ()
    target_id: int | None = None

    def __post_init__(self):
        if not valid_seat(self.actor) or self.kind not in ("discard", "riichi", "chi", "pon", "open_kan", "closed_kan", "added_kan", "pass", "ron", "tsumo", "abort_nine"):
            raise ValueError("Invalid action kind/actor")
        if len(set(self.tile_ids)) != len(self.tile_ids) or any(not isinstance(t, str) or not t for t in self.tile_ids):
            raise ValueError("Action tile references must be unique and nonempty")
        if self.kind in ("discard", "riichi") and len(self.tile_ids) != 1:
            raise ValueError("Discard/riichi selects exactly one tile record")
        if self.target_id is not None and (type(self.target_id) is not int or self.target_id < 1):
            raise ValueError("Invalid target event")


@dataclass
class Metric:
    status: Literal["available", "conditional", "unavailable", "not_applicable"]
    value: object = None
    basis: Literal["exact", "heuristic", "estimate"] | None = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.status not in ("available", "conditional", "unavailable", "not_applicable"):
            raise ValueError("Invalid metric status")
        if self.basis not in (None, "exact", "heuristic", "estimate"):
            raise ValueError("Invalid metric basis")
        if self.status in ("unavailable", "not_applicable"):
            if self.value is not None:
                raise ValueError("Unavailable metric cannot contain a fabricated value")
        elif self.value is None or self.basis is None:
            raise ValueError("Available metric requires value and basis")


@dataclass
class CandidateResult:
    action: Action
    legality: Literal["allowed", "forbidden", "unknown"] = "unknown"
    metrics: dict[str, Metric] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()

    def __post_init__(self):
        if self.legality not in ("allowed", "forbidden", "unknown"):
            raise ValueError("Invalid legality")


@dataclass
class AnalysisResult:
    state_version: str
    method: str
    situation: dict[str, Metric] = field(default_factory=dict)
    candidates: tuple[CandidateResult, ...] = ()
    # Keys may be overall/attack/defense. Missing key means not computed.
    recommendations: dict[str, tuple[Action, ...]] = field(default_factory=dict)

    def __post_init__(self):
        if not self.state_version or not self.method:
            raise ValueError("Result needs state version and method version")
        actions = [c.action for c in self.candidates]
        if len(set(actions)) != len(actions):
            raise ValueError("Duplicate candidate action")
        allowed = {c.action for c in self.candidates if c.legality == "allowed"}
        if any(a not in allowed for choices in self.recommendations.values() for a in choices):
            raise ValueError("Executable recommendations require allowed candidates")
