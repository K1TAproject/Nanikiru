"""Project one already-visible request, never an omniscient game state.

Masking is persistent within this sample: event facts cannot restore hidden
tile codes. Snapshot deltas are deliberately excluded from analysis history.
"""

from copy import deepcopy
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
import json
import math
import random

from ..models import UNKNOWN, Event, valid_seat
from ..rules.observed_facts import event_fact, riichi_status


@dataclass(frozen=True)
class ObservationConfig:
    midgame: bool = False
    history_start: int | None = None  # event count, not event id
    public_code_rate: float = 0.0
    hand_code_rate: float = 0.0
    discard_kind_rate: float = 0.0
    hidden_rivers: tuple[int, ...] = ()
    hide_order: bool = False
    hide_riichi: bool = False

    def __post_init__(self):
        for key in ('midgame', 'hide_order', 'hide_riichi'):
            if type(getattr(self, key)) is not bool: raise ValueError(f'{key} must be bool')
        if self.history_start is not None and (type(self.history_start) is not int or self.history_start < 0):
            raise ValueError('history_start must be a nonnegative event count')
        for key in ('public_code_rate', 'hand_code_rate', 'discard_kind_rate'):
            value = getattr(self, key)
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError(f'{key} must be in [0,1]')
        if len(set(self.hidden_rivers)) != len(self.hidden_rivers) or any(not valid_seat(s) for s in self.hidden_rivers):
            raise ValueError('hidden_rivers must contain unique player seats')


# Only fields actually consumed as public facts; no source extensions/deltas.
FACT_FIELDS = {'tile', 'code', 'tiles', 'codes', 'called', 'source', 'tsumogiri', 'riichi', 'kan_kind'}


def observe_request(request, config=ObservationConfig(), *, seed=0):
    """Return an independent AnalysisRequest with opaque local tile references."""
    return _observe(request, config, seed)[0]


def _observe(request, config, seed):
    config.__post_init__()
    if type(seed) not in (str, int): raise ValueError('seed must be str or int')
    result = deepcopy(request)
    result.__post_init__()
    p = result.position
    rng = random.Random(str(seed))
    start = config.history_start
    if start is None:
        start = rng.randint(1, len(result.history)) if config.midgame and result.history else 0
    if start > len(result.history): raise ValueError('history_start exceeds available history')
    if config.hide_order: start = len(result.history)
    if start or (config.midgame and config.history_start is None) or config.hide_order:
        p.initial_context = {'riichi': [riichi_status(p, result.history[:start], s, result.coverage) for s in range(4)]}
        result.coverage = {'from_start': False, 'gaps': deepcopy(result.coverage['gaps'])}
    history = result.history[start:]
    facts = []
    for event in history:
        kind, data = event_fact(event)
        fact = {k: deepcopy(v) for k, v in data.items() if k in FACT_FIELDS}
        if event.kind == 'discard':
            fact['tile'] = data.get('tile_id', UNKNOWN)
            fact['tsumogiri'] = UNKNOWN if data.get('discard_kind') is UNKNOWN else data.get('discard_kind') == 'tsumogiri'
        elif event.kind == 'draw':
            fact['tile'] = data.get('tile_id', UNKNOWN)
        prefix = 'observed_' if event.kind.startswith(('sim_', 'tenhou_', 'mjai_', 'observed_')) else ''
        facts.append((event, prefix+kind, fact))

    ids = {t.id for t in p.tiles}
    for _, _, fact in facts:
        ids.update(v for k in ('tile', 'called') if isinstance(v := fact.get(k), str) and v is not UNKNOWN)
        ids.update(fact.get('tiles', ()))
    names = list(range(len(ids))); rng.shuffle(names)
    mapping = {tid: f'o{n}' for tid, n in zip(sorted(ids), names)}
    hidden = set()
    for tile in p.tiles:
        rate = config.hand_code_rate if tile.zone == 'hand' else config.public_code_rate
        if tile.zone == 'river' and tile.seat in config.hidden_rivers or rng.random() < rate:
            hidden.add(tile.id)
    # Also hide public historical tiles no longer represented by the current river.
    sampled = {t.id for t in p.tiles}
    for event, kind, fact in facts:
        if kind.endswith('discard') and isinstance(fact.get('tile'), str):
            tid = fact['tile']
            if event.actor in config.hidden_rivers or (tid not in sampled and rng.random() < config.public_code_rate):
                hidden.add(tid)
            sampled.add(tid)
    p.tiles = tuple(replace(t, id=mapping[t.id], code=UNKNOWN if t.id in hidden else t.code) for t in p.tiles)
    if config.hidden_rivers: p.completeness['public_tiles'] = False
    if config.hide_order:
        # Indicator slots retain their spatial identity; only river order is lost.
        rivers = [t for t in p.tiles if t.zone == 'river']; rng.shuffle(rivers)
        p.tiles = tuple(rivers.pop() if t.zone == 'river' else t for t in p.tiles)
    for group in p.groups.values():
        if isinstance(group['called_tile_id'], str) and group['called_tile_id'] is not UNKNOWN:
            group['called_tile_id'] = mapping[group['called_tile_id']]
    if isinstance(p.draw_context, dict) and p.draw_context['tile_id'] is not UNKNOWN:
        p.draw_context['tile_id'] = mapping[p.draw_context['tile_id']]
    event_ids = {e.id: i+1 for i, (e, _, _) in enumerate(facts)}
    if isinstance(p.pending, dict):
        if p.pending['tile_id'] is not UNKNOWN: p.pending['tile_id'] = mapping[p.pending['tile_id']]
        p.pending['trigger_event'] = event_ids.get(p.pending['trigger_event'], UNKNOWN)
    sanitized = []
    previous_draw = {}
    for event, kind, fact in facts:
        if kind.endswith('discard') and rng.random() < config.discard_kind_rate:
            fact['tsumogiri'] = UNKNOWN
            # Otherwise equality of draw/discard entity IDs reveals tsumogiri.
            if event.actor in previous_draw:
                previous_draw[event.actor]['tile'] = UNKNOWN
                previous_draw[event.actor]['code'] = UNKNOWN
        if fact.get('tile') in hidden: fact['code'] = UNKNOWN
        if 'tiles' in fact and 'codes' in fact:
            fact['codes'] = [UNKNOWN if t in hidden else c for t, c in zip(fact['tiles'], fact['codes'])]
        for key in ('tile', 'called'):
            if isinstance(fact.get(key), str) and fact[key] is not UNKNOWN:
                fact[key] = mapping[fact[key]]
        if 'tiles' in fact: fact['tiles'] = [mapping[t] for t in fact['tiles']]
        if config.hide_riichi:
            if 'riichi' in fact: fact['riichi'] = UNKNOWN
            if kind.endswith(('riichi_accepted', 'riichi_declared')):
                kind, fact = 'observed_unclassified', {}
                result.coverage['from_start'] = False
        data = {'fact': fact} if kind.startswith('observed_') else fact
        if kind.endswith('draw'): previous_draw[event.actor] = fact
        sanitized.append(Event(event_ids[event.id], kind, event.actor,
                               None if event.target_id is None else event_ids.get(event.target_id, UNKNOWN), data))
    result.history = tuple(sanitized)
    if config.hide_riichi:
        p.initial_context = {'riichi': [UNKNOWN]*4}
        result.coverage['from_start'] = False
    version = json.dumps({'config': asdict(config), 'seed': seed}, sort_keys=True)
    result.state_version += ':obs:'+sha256(version.encode()).hexdigest()[:16]
    result.__post_init__()
    return result, mapping
