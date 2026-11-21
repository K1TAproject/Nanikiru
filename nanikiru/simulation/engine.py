"""Deterministic single-hand state machine; all mutations go through submit.

The full state is private. Public facts are projected at emission time; later
reveals never rewrite an earlier observation. Qualifications derive from facts.
"""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
from itertools import combinations
import random
from uuid import uuid4

from ..contracts import Action
from ..models import Event, valid_seat
from ..rules.config import RuleConfig
from ..rules.call_rules import call_banned
from ..rules.discards import discard_ids
from ..rules.observed_facts import interrupts_ippatsu
from ..rules.scoring import WINDS, evaluate, waits
from ..tiles import index, physical_capacity, tile34
from .observation import ObserverRecords
from .settlement import finish_draw, finish_win

CALLS = {"chi", "pon", "open_kan", "closed_kan", "added_kan"}
TERMINALS = {0, 8, 9, 17, 18, 26, *range(27, 34)}


def inventory():
    return [c for c in [*(tile34(i) for i in range(34)), "0m", "0p", "0s"]
            for _ in range(physical_capacity(c))]


class Simulator:
    def __init__(self, *, rules=None, seed=None, wall=None, dealer=0, round_wind="east",
                 hand_number=1, honba=0, sticks=0, scores=None, round_id=None):
        self._rules = RuleConfig() if rules is None else rules
        if not isinstance(self.rules, RuleConfig) or not valid_seat(dealer) or round_wind not in WINDS:
            raise ValueError("Invalid rules/dealer/wind")
        if type(hand_number) is not int or not 1 <= hand_number <= 4:
            raise ValueError("hand_number must be 1..4")
        if any(type(n) is not int or n < 0 for n in (honba, sticks)):
            raise ValueError("honba/sticks must be nonnegative integers")
        scores = [self.rules.starting_points] * 4 if scores is None else list(scores)
        if len(scores) != 4 or any(type(n) is not int for n in scores):
            raise ValueError("Four integer scores required")
        if self.rules.bankruptcy and min(scores) < 0:
            raise ValueError("Cannot start after bankruptcy")
        if wall is None:
            wall = inventory()
            random.Random(seed).shuffle(wall)
        if Counter(wall) != Counter(inventory()):
            raise ValueError("Wall must contain the exact 136-tile inventory")
        self._initial = dict(rules=asdict(self.rules), wall=list(wall), dealer=dealer,
                             round_wind=round_wind, hand_number=hand_number,
                             honba=honba, sticks=sticks, scores=scores[:], round_id=round_id or uuid4().hex)
        self.round_id = self._initial["round_id"]
        self.version = uuid4().hex
        self._codes = {f"t{i}": c for i, c in enumerate(wall)}
        ids = list(self._codes)
        self._hands = [ids[s * 13:(s + 1) * 13] for s in range(4)]
        self._live, self._dead = ids[52:122], ids[122:]
        self._groups, self._rivers = {}, [[] for _ in range(4)]
        self._scores, self._sticks = scores, sticks
        self.dealer, self.round_wind, self.hand_number, self.honba = dealer, round_wind, hand_number, honba
        self._phase, self._actor, self._draw, self._pending = "await_draw", dealer, None, None
        self._banned = set()
        self._kan_count, self._indicator_count, self._delayed_dora = 0, 1, 0
        self._events, self._commands = [], []
        self._result = None
        self._views = ObserverRecords(self)
        self._draw_tile(dealer)
        self._check_inventory()

    @property
    def rules(self):
        return self._rules

    @property
    def command_count(self):
        return len(self._commands)

    @property
    def result(self):
        return deepcopy(self._result)

    @property
    def phase(self):
        return self._phase

    @property
    def actor(self):
        return self._actor

    def observe(self, seat):
        return self._views.position(seat)

    def record(self, seat):
        return self._views.record(seat)

    def debug_state(self):
        """Explicit omniscient test/UI endpoint. Never pass this to analysis."""
        return deepcopy({"hands": self._hands, "codes": self._codes, "groups": self._groups,
                         "rivers": self._rivers, "live": self._live, "dead": self._dead,
                         "events": self._events, "scores": self._scores, "pending": self._pending})

    def _emit(self, kind, actor=None, *, private=False, **data):
        event = Event(len(self._events) + 1, kind, actor, data=data)
        self._events.append(event)
        self._views.capture(self, event, private=private)
        return event.id

    def _own_groups(self, seat):
        return [(g["kind"], tuple(self._codes[t] for t in g["tiles"]))
                for g in self._groups.values() if g["seat"] == seat]

    def _waiting(self, seat, hand=None, groups=None):
        hand = self._hands[seat] if hand is None else hand
        return waits(tuple(self._codes[t] for t in hand), self._own_groups(seat) if groups is None else groups,
                     formal=self.rules.formal_dead_waits)

    def _riichi(self, seat):
        return next((e for e in self._events if e.kind == "riichi_accepted" and e.actor == seat), None)

    def _uninterrupted(self):
        return not any(e.kind in CALLS or e.kind == "kan_declared" for e in self._events)

    def _furiten(self, seat):
        if self._waiting(seat) & {index(self._codes[r["tile"]]) for r in self._rivers[seat]}:
            return True
        last_draw = max((e.id for e in self._events if e.actor == seat and e.kind == "draw"), default=0)
        riichi = self._riichi(seat)
        return any(e.kind == "pass" and e.actor == seat and e.data["missed"]
                   and (e.id > last_draw or riichi and e.id > riichi.id) for e in self._events)

    def _score(self, seat, tile, *, tsumo=False, rob=None, reveal_ura=False):
        hand = self._hands[seat][:]
        if tsumo:
            hand.remove(tile)
        if not tsumo and self._furiten(seat):
            return None
        if rob == "closed_kan":
            if not self.rules.kokushi_ankan_ron:
                return None
            ids = {index(self._codes[t]) for t in [*hand, tile]}
            if self._own_groups(seat) or ids != TERMINALS:
                return None
        ready = self._riichi(seat)
        ippatsu = bool(ready and self.rules.ippatsu and not any(
            e.id > ready.id and (interrupts_ippatsu(e.kind, e.data, self.rules)
                                or e.kind == "discard" and e.actor == seat) for e in self._events))
        first = not self._rivers[seat] and self._uninterrupted()
        replacement = bool(tsumo and self._draw and self._draw["source"] == "replacement")
        ura_count = self._indicator_count if self.rules.kan_ura else 1
        return evaluate(tuple(self._codes[t] for t in hand), self._own_groups(seat), self._codes[tile],
                        seat=seat, dealer=self.dealer, round_wind=self.round_wind,
                        tsumo=tsumo, riichi=bool(ready), rules=self.rules,
                        indicators=tuple(self._codes[self._dead[4 + 2*i]] for i in range(self._indicator_count)),
                        ura=tuple(self._codes[self._dead[5 + 2*i]] for i in range(ura_count))
                        if ready and reveal_ura and self.rules.ura_dora else (),
                        is_daburu_riichi=bool(ready and ready.data["double"]), is_ippatsu=ippatsu,
                        is_rinshan=replacement, is_chankan=rob is not None,
                        is_haitei=bool(tsumo and not self._live and not replacement),
                        is_houtei=bool(not tsumo and not self._live and rob is None),
                        is_tenhou=bool(tsumo and first and seat == self.dealer),
                        is_chiihou=bool(tsumo and first and seat != self.dealer))

    def _can_kan(self):
        return bool(self._live) and self._kan_count < 4

    def legal_actions(self, seat):
        if not valid_seat(seat):
            raise ValueError("Invalid seat")
        if self._phase == "ended":
            return ()
        if self._pending is not None:
            if seat in self._pending["responses"]:
                return ()
            return tuple(self._pending["options"].get(seat, ()))
        if self._actor != seat or self._phase != "await_discard":
            return ()
        ready, hand = self._riichi(seat), self._hands[seat]
        actions = []
        for t in discard_ids([(t, self._codes[t]) for t in hand], riichi=bool(ready),
                             drawn_id=self._draw["tile"] if self._draw else None, banned=self._banned):
            actions.append(Action("discard", seat, (t,)))
        if self._draw and self._score(seat, self._draw["tile"], tsumo=True):
            actions.append(Action("tsumo", seat))
        if (self.rules.abortive_draws and self._draw and not self._rivers[seat] and self._uninterrupted()
                and len({index(self._codes[t]) for t in hand} & TERMINALS) >= 9):
            actions.append(Action("abort_nine", seat))
        if not ready and self._scores[seat] >= 1000 and len(self._live) >= 4 and all(
                k == "closed_kan" for k, _ in self._own_groups(seat)):
            for a in actions[:]:
                if a.kind == "discard" and self._waiting(seat, [t for t in hand if t not in a.tile_ids]):
                    actions.append(Action("riichi", seat, a.tile_ids))
        if self._draw and self._can_kan():
            for i in range(34):
                matching = tuple(t for t in hand if index(self._codes[t]) == i)
                if len(matching) == 4:
                    remaining = [t for t in hand if t not in matching]
                    new_groups = self._own_groups(seat) + [("closed_kan", tuple(self._codes[t] for t in matching))]
                    # Fixed rule: drawn fourth copy and unchanged complete wait set.
                    if not ready or (self._draw["tile"] in matching and self._waiting(
                            seat, [t for t in hand if t != self._draw["tile"]]) == self._waiting(seat, remaining, new_groups)):
                        actions.append(Action("closed_kan", seat, matching))
            if not ready:
                for g in self._groups.values():
                    if g["seat"] == seat and g["kind"] == "pon":
                        for t in hand:
                            if index(self._codes[t]) == index(self._codes[g["tiles"][0]]):
                                actions.append(Action("added_kan", seat, (t,)))
        return tuple(actions)

    def _response_options(self, seat, source, tile, kind):
        target = self._pending["event"]
        actions = []
        rob = kind if kind != "discard" else None
        if self._score(seat, tile, rob=rob):
            actions.append(Action("ron", seat, target_id=target))
        if kind == "discard" and not self._riichi(seat) and self._live:
            hand, n = self._hands[seat], index(self._codes[tile])
            same = [t for t in hand if index(self._codes[t]) == n]
            choices = [("pon", pair) for pair in combinations(same, 2)]
            if len(same) == 3 and self._can_kan():
                actions.append(Action("open_kan", seat, tuple(same), target))
            if seat == (source + 1) % 4 and n < 27:
                for pair in combinations(hand, 2):
                    seq = sorted([n, *(index(self._codes[t]) for t in pair)])
                    if seq[0] // 9 == seq[2] // 9 and seq == list(range(seq[0], seq[0] + 3)):
                        choices.append(("chi", pair))
            for call, ids in choices:
                banned = call_banned(call, [self._codes[t] for t in ids], self._codes[tile]) if self.rules.forbid_kuikae else set()
                if any(t not in ids and index(self._codes[t]) not in banned for t in hand):
                    actions.append(Action(call, seat, tuple(ids), target))
        return tuple([*actions, Action("pass", seat, target_id=target)]) if actions else ()

    def submit(self, action):
        """Validate on original; execute on copy; failed commands are atomic."""
        if not isinstance(action, Action) or action not in self.legal_actions(action.actor):
            raise ValueError("当前阶段不允许该动作")
        other = deepcopy(self)
        other._apply(action)
        other._check_inventory()
        other._commands.append(action)
        other.version = uuid4().hex
        self.__dict__.update(other.__dict__)

    def _apply(self, action):
        seat, kind = action.actor, action.kind
        if self._pending is not None:
            self._pending["responses"][seat] = action
            missed = self._missed_shape(seat) and kind != "ron"
            self._emit("pass" if kind == "pass" else "response", seat, private=True,
                       choice=kind, missed=missed, target=self._pending["event"])
            if kind != "pass" and missed:
                self._emit("pass", seat, private=True, choice=kind, missed=True, target=self._pending["event"])
            if set(self._pending["responses"]) == set(self._pending["options"]):
                self._resolve()
        elif kind == "tsumo":
            finish_win(self, [seat], None, self._draw["tile"])
        elif kind == "abort_nine":
            finish_draw(self, "nine_terminals")
        elif kind in ("discard", "riichi"):
            tile = action.tile_ids[0]
            drawn = self._draw and self._draw["tile"] == tile
            self._hands[seat].remove(tile)
            self._rivers[seat].append(dict(tile=tile, called=False, riichi=kind == "riichi"))
            self._draw, self._banned = None, set()
            self._phase, self._actor = "await_response", None
            self._flush_dora()
            self._open_response(seat, tile, "discard", action,
                                code=self._codes[tile], tsumogiri=bool(drawn), riichi=kind == "riichi")
        elif kind in ("closed_kan", "added_kan"):
            tile = self._draw["tile"] if self._draw["tile"] in action.tile_ids else action.tile_ids[0]
            self._phase, self._actor = "await_kan_response", None
            self._open_response(seat, tile, kind, action, code=self._codes[tile])

    def _missed_shape(self, seat):
        p = self._pending
        if p["kind"] == "closed_kan":
            return self.rules.kokushi_ankan_ron and not self._own_groups(seat) and {index(self._codes[t]) for t in [*self._hands[seat], p["tile"]]} == TERMINALS
        return index(self._codes[p["tile"]]) in self._waiting(seat)

    def _open_response(self, seat, tile, kind, action, **fact):
        event_id = len(self._events) + 1
        self._pending = dict(source=seat, tile=tile, kind=kind, action=action, event=event_id,
                             options={}, responses={})
        self._emit("discard" if kind == "discard" else "kan_declared", seat,
                   tile=tile, kan_kind=kind if kind != "discard" else None, **fact)
        for s in range(4):
            if s == seat:
                continue
            options = self._response_options(s, seat, tile, kind)
            if options:
                self._pending["options"][s] = options
            elif self._missed_shape(s):
                self._emit("pass", s, private=True, choice="automatic_no_legal_response", missed=True, target=event_id)
        if not self._pending["options"]:
            self._resolve()

    def _resolve(self):
        p = self._pending
        source, tile = p["source"], p["tile"]
        responses = list(p["responses"].values())
        winners = sorted((a.actor for a in responses if a.kind == "ron"), key=lambda s: (s-source) % 4)
        if winners:
            if len(winners) == 3 and self.rules.triple_ron_draw:
                finish_draw(self, 'three_ron'); return
            finish_win(self, winners if self.rules.multiple_ron else winners[:1], source, tile,
                       rob=p["kind"] if p["kind"] != "discard" else None)
            return
        if p["kind"] != "discard":
            self._complete_kan(p["action"])
            return
        if p["action"].kind == "riichi":
            self._scores[source] -= 1000
            self._sticks += 1
            self._emit("riichi_accepted", source, double=len(self._rivers[source]) == 1 and self._uninterrupted())
        if self.rules.abortive_draws:
            if all(self._riichi(s) for s in range(4)):
                finish_draw(self, "four_riichi"); return
            owners = {g["seat"] for g in self._groups.values() if "kan" in g["kind"]}
            if self._kan_count == 4 and len(owners) > 1:
                finish_draw(self, "four_kans"); return
            firsts = [index(self._codes[r[0]["tile"]]) for r in self._rivers if r]
            if (all(len(r) == 1 for r in self._rivers) and self._uninterrupted()
                    and len(set(firsts)) == 1 and firsts[0] in range(27, 31)):
                finish_draw(self, "four_winds"); return
        calls = [a for a in responses if a.kind in CALLS]
        if calls:
            a = min(calls, key=lambda a: (1 if a.kind == "chi" else 0, (a.actor-source) % 4))
            if a.kind == "open_kan":
                self._complete_kan(a)
            else:
                self._form_group(a, tile, source)
                self._banned = call_banned(a.kind, [self._codes[t] for t in a.tile_ids], self._codes[tile]) if self.rules.forbid_kuikae else set()
                self._phase, self._actor, self._draw, self._pending = "await_discard", a.actor, None, None
                self._emit(a.kind, a.actor, tiles=list(a.tile_ids), called=tile, source=source)
            return
        self._pending = None
        if not self._live:
            finish_draw(self, "exhaustive")
        else:
            self._draw_tile((source + 1) % 4)

    def _form_group(self, action, called=None, source=None):
        seat = action.actor
        for t in action.tile_ids:
            self._hands[seat].remove(t)
        gid = f"g{len(self._groups)}"
        self._groups[gid] = dict(kind=action.kind, seat=seat, tiles=list(action.tile_ids) + ([called] if called else []),
                                 called=called, source=source)
        if called:
            next(r for r in self._rivers[source] if r["tile"] == called)["called"] = True

    def _complete_kan(self, action):
        seat = action.actor
        self._flush_dora()
        if action.kind == "added_kan":
            tile = action.tile_ids[0]
            group = next(g for g in self._groups.values() if g["seat"] == seat and g["kind"] == "pon"
                         and index(self._codes[g["tiles"][0]]) == index(self._codes[tile]))
            self._hands[seat].remove(tile)
            group["tiles"].append(tile)
            group["kind"] = "added_kan"
        elif action.kind == "open_kan":
            self._form_group(action, self._pending["tile"], self._pending["source"])
        else:
            self._form_group(action)
        self._kan_count += 1
        self._pending, self._draw = None, None
        self._phase, self._actor = "await_replacement", seat
        if self.rules.kan_dora:
            if action.kind == "closed_kan":
                self._indicator_count += 1
            else:
                self._delayed_dora += 1
        self._emit(action.kind, seat, tiles=list(action.tile_ids))
        self._draw_tile(seat, replacement=True)

    def _flush_dora(self):
        self._indicator_count += self._delayed_dora
        self._delayed_dora = 0

    def _draw_tile(self, seat, replacement=False):
        if replacement:
            tile = self._dead[self._kan_count - 1]
            self._dead[self._kan_count - 1] = self._live.pop()
        else:
            tile = self._live.pop(0)
        self._hands[seat].append(tile)
        self._phase, self._actor, self._pending = "await_discard", seat, None
        self._draw = dict(tile=tile, source="replacement" if replacement else "live", seat=seat)
        self._banned = set()
        self._emit("draw", seat, tile=tile, code=self._codes[tile], source=self._draw["source"])

    def _check_inventory(self):
        located = [*self._live, *self._dead,
                   *(t for hand in self._hands for t in hand),
                   *(t for g in self._groups.values() for t in g["tiles"]),
                   *(r["tile"] for river in self._rivers for r in river if not r["called"])]
        if len(located) != 136 or set(located) != set(self._codes):
            raise AssertionError("Physical inventory is not conserved")
        if sum(self._scores) + 1000*self._sticks != sum(self._initial["scores"]) + 1000*self._initial["sticks"]:
            raise AssertionError("Points and deposits are not conserved")
