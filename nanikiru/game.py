"""Full-information single-hand riichi environment, independent of any UI.

Wall convention v1: 13 clockwise rounds of single-tile dealing from the dealer,
then live draws up to index 121. Last 14 tiles are reserved; index 130 is dora.
This is a reproducible simulation convention, not Mahjong Soul's shuffle protocol.
"""

from collections import Counter
from copy import copy, deepcopy
from dataclasses import asdict, dataclass, field
from itertools import combinations
import json
import os
from pathlib import Path
import random
import secrets
import tempfile

from .models import Decision, Discard, DiscardRecord, Draw, DrawnTile, HandState, Meld, MeldKind, Phase, PlayerState, Tile
from .recorder import RecordError, require, seat_valid
from .scoring import Rules, TERMINALS, WINDS, evaluate, index, waits, ron_restrictions
from .efficiency import analyze_discards, compare_discard
from .call_rules import call_banned
from .decision import analyze_decision, STRATEGY_VERSION
from .defense import analyze_defense
from .tenpai import analyze_tenpai
from .discard_rules import validate_discard, first_uninterrupted_discard
from .riichi_decision import analyze_riichi, VERSION as RIICHI_VERSION


def make_tiles():
    return [Tile(suit, rank, suit != "z" and rank == 5 and copy == 0)
            for suit in "mpsz" for rank in range(1, 8 if suit == "z" else 10)
            for copy in range(4)]


@dataclass
class GameState:
    wall: list[Tile]
    players: list[PlayerState]
    dealer: int
    actor: int
    next_draw: int = 52
    phase: Phase = Phase.DISCARD
    hand_number: int = 1
    honba: int = 0
    riichi_sticks: int = 0
    round_wind: str = "east"
    live_end: int = 122
    kan_count: int = 0
    dora_count: int = 1
    pending_dora: int = 0
    interrupted: bool = False
    rinshan: bool = False
    last_live_draw: bool = False
    double_riichi: list[bool] = field(default_factory=lambda: [False] * 4)
    ippatsu: list[bool] = field(default_factory=lambda: [False] * 4)
    temporary_furiten: list[bool] = field(default_factory=lambda: [False] * 4)
    riichi_furiten: list[bool] = field(default_factory=lambda: [False] * 4)
    pending: dict | None = None
    kuikae: set[int] = field(default_factory=set)
    pao: dict[int, int] = field(default_factory=dict)
    result: dict | None = None

    @property
    def remaining_draws(self):
        return self.live_end - self.next_draw


class Game:
    """Privileged controller. Give agents observe() results, not this object."""

    def __init__(self, seed=None, dealer=0, *, rules=None, round_wind="east", hand_number=1,
                 honba=0, riichi_sticks=0, scores=None):
        seed = secrets.randbits(63) if seed is None else seed
        require(type(seed) is int, "Seed must be an integer")
        wall = make_tiles()
        random.Random(seed).shuffle(wall)
        self._setup(wall, dealer, rules, round_wind, hand_number, honba, riichi_sticks, scores)
        self._seed = seed

    def _setup(self, wall, dealer, rules=None, round_wind="east", hand_number=1,
               honba=0, riichi_sticks=0, scores=None):
        require(seat_valid(dealer), "Invalid dealer seat")
        require(all(isinstance(t, Tile) for t in wall) and Counter(wall) == Counter(make_tiles()),
                "Wall must contain the exact 136-tile inventory")
        self._initial_wall = list(wall)
        self._seed = None
        self.rules = rules if rules is not None else Rules()
        require(isinstance(self.rules, Rules) and round_wind in WINDS, "Invalid round rules")
        require(type(hand_number) is int and 1 <= hand_number <= 4, "Invalid hand number")
        require(all(type(n) is int and n >= 0 for n in (honba, riichi_sticks)), "Invalid round counters")
        scores = [25000] * 4 if scores is None else list(scores)
        require(len(scores) == 4 and all(type(n) is int and n >= 0 for n in scores), "Invalid starting scores")
        self._config = dict(round_wind=round_wind, hand_number=hand_number, honba=honba,
                            riichi_sticks=riichi_sticks, scores=scores)
        players = [PlayerState(i, scores[i], HandState()) for i in range(4)]
        for i, tile in enumerate(wall[:52]):
            players[(dealer + i) % 4].hand.known_tiles.append(tile)
        self._state = GameState(list(wall), players, dealer, dealer, round_wind=round_wind,
                                hand_number=hand_number, honba=honba, riichi_sticks=riichi_sticks)
        self._actions = []
        self._events = []
        self._paused = False
        self._diagnostics = []
        self._reviews = []
        self._decision_records = []
        self._draw()

    @classmethod
    def from_wall(cls, wall, dealer=0, **kwargs):
        game = cls.__new__(cls)
        game._setup(list(wall), dealer, **kwargs)
        return game

    @property
    def paused(self):
        return self._paused

    @property
    def action_count(self):
        return len(self._actions)

    def stop(self):
        self._paused = True

    def resume(self):
        self._paused = False

    def _draw(self):
        s = self._state
        tile = s.wall[s.next_draw]
        s.next_draw += 1
        s.players[s.actor].hand.drawn_tile = DrawnTile(tile)
        s.temporary_furiten[s.actor] = False
        s.rinshan = False
        s.last_live_draw = s.remaining_draws == 0
        self._events.append(Draw(s.actor, tile))

    def submit(self, action, *, policy="manual", mode="attack"):
        self._submit(action, record_review=True, policy=policy, mode=mode)

    def _candidate(self):
        # Reviews are append-only metadata, not part of rule validation. Avoid
        # copying every historical observation when probing each legal action.
        candidate = copy(self)
        candidate._state = deepcopy(self._state)
        candidate._events = list(self._events)
        candidate._actions = list(self._actions)
        candidate._reviews = list(self._reviews)
        candidate._decision_records = list(self._decision_records)
        return candidate

    def _submit(self, action, record_review, policy="manual", mode=None, *,
                review_status=True, decision_status=True, tenpai=True,
                review_context=True, decision_context=True, riichi_review=True):
        require(not self._paused, "Game paused")
        # Validate and resolve on a candidate; failures retain all prior legal responses.
        require(policy in ("manual", "unknown-legacy", "basic-efficiency-v1", STRATEGY_VERSION, RIICHI_VERSION, "ui-auto-pass-v1"), "Unknown decision policy")
        require(mode in (None, "attack", "fold"), "Unknown decision mode")
        candidate = self._candidate()
        try:
            candidate._apply(action)
            discard = action if isinstance(action, Discard) else (
                Discard(action.player, action.tiles[0], action.is_tsumogiri) if action.kind == "riichi" else None)
            if record_review and discard is not None:
                # Crucially observe self, not the candidate after the action.
                observation = self.observe(action.player)
                legal = [a for a in self.legal_actions(action.player) if isinstance(a, Discard)]
                if not review_context:
                    observation.pop("riichi_context", None)
                if not review_status:
                    observation.pop("own_status", None)
                analysis = analyze_discards(observation, legal)
                candidate._reviews.append({"action_index": self.action_count + 1, "player": action.player,
                                           "observation": observation,
                                           "actual_action": json.loads(json.dumps(self._encode_action(action))),
                                           "analysis": analysis, "comparison": compare_discard(analysis, discard)})
                if tenpai:
                    candidate._reviews[-1]["tenpai"] = analyze_tenpai(observation, legal)
            response = isinstance(action, Decision) and action.kind in ("pass", "chi", "pon", "ron")
            if record_review and (discard is not None or response or policy == RIICHI_VERSION):
                observation = self.observe(action.player)
                legal = self.legal_actions(action.player)
                if not decision_context:
                    observation.pop("riichi_context", None)
                if not decision_status:
                    observation.pop("own_status", None)
                candidate._decision_records.append({
                    "action_index": self.action_count + 1, "player": action.player, "policy": policy,
                    "observation": observation,
                    "actual_action": json.loads(json.dumps(self._encode_action(action))),
                    "analysis": analyze_decision(observation, legal)})
                if riichi_review and discard is not None:
                    record = candidate._decision_records[-1]
                    record["riichi"] = analyze_riichi(observation, legal, mode or "attack")
                if mode is not None and policy != "basic-efficiency-v1":
                    record = candidate._decision_records[-1]
                    record["defense"] = analyze_defense(observation, legal, mode, attack=record["analysis"])

        except (RecordError, ValueError, TypeError) as exc:
            self._paused = True
            self._diagnostics.append({"at": self.action_count, "action": repr(action), "error": str(exc)})
            raise RecordError(str(exc)) from exc
        candidate._actions.append(action)
        self.__dict__.update(candidate.__dict__)

    def _apply(self, action):
        require(type(action) in (Discard, Decision), "Unsupported action; draws are automatic")
        s = self._state
        require(s.phase != Phase.ENDED, "Round has ended")
        require(seat_valid(action.player) and action.player == s.actor, "Not this player's turn")
        if isinstance(action, Decision):
            require(type(action.kind) is str and type(action.tiles) is tuple
                    and all(isinstance(t, Tile) for t in action.tiles)
                    and type(action.is_tsumogiri) is bool, "Invalid decision payload")
        if s.phase == Phase.RESPONSE:
            require(isinstance(action, Decision), "Submit a response or pass")
            self._respond(action)
            return
        if isinstance(action, Discard):
            self._discard(action)
        elif action.kind == "riichi":
            require(len(action.tiles) == 1, "Choose one riichi discard")
            p = s.players[s.actor]
            require(not p.riichi and all(m.kind == MeldKind.CLOSED_KAN for m in p.melds), "Riichi requires a closed hand")
            require(p.score >= 1000 and s.remaining_draws >= 4, "Riichi requires 1000 points and a future draw")
            require(p.hand.drawn_tile is not None, "Riichi requires a draw")
            self._discard(Discard(action.player, action.tiles[0], action.is_tsumogiri), riichi=True)
            require(bool(waits(p.hand.known_tiles)), "Riichi discard must leave tenpai")
        elif action.kind in ("closed_kan", "added_kan"):
            self._declare_kan(action)
        elif action.kind == "tsumo":
            require(not action.tiles and not action.is_tsumogiri, "Tsumo takes no tile arguments")
            value = self._win_value(action.player, tsumo=True)
            require(value is not None, "Tsumo requires a complete hand with a yaku")
            self._win([action.player], None, [value])
        elif action.kind == "nine_terminals":
            p = s.players[s.actor]
            require(not action.tiles and not action.is_tsumogiri, "Nine terminals takes no tile arguments")
            require(not s.interrupted and not p.discards and p.hand.drawn_tile is not None,
                    "Nine terminals requires the uninterrupted first draw")
            require(len({index(t) for t in self._concealed(p)} & TERMINALS) >= 9, "Need nine distinct terminals/honors")
            self._abort("nine_terminals")
        else:
            raise RecordError("Action not available in this phase")

    @staticmethod
    def _concealed(p):
        return p.hand.known_tiles + ([p.hand.drawn_tile.tile] if p.hand.drawn_tile else [])

    def _discard(self, action, riichi=False):
        s = self._state
        hand = s.players[s.actor].hand
        validate_discard(hand, s.players[s.actor].riichi, action, s.kuikae)
        if not action.is_tsumogiri:
            hand.known_tiles.remove(action.tile)
            if hand.drawn_tile:
                hand.known_tiles.append(hand.drawn_tile.tile)
        hand.drawn_tile = None
        self._events.append(action)
        s.players[s.actor].discards.append(DiscardRecord(
            f"game:{len(self._events)}", action.tile, action.is_tsumogiri, riichi))
        s.kuikae.clear()
        if s.players[s.actor].riichi:
            s.ippatsu[s.actor] = False
        self._reveal_pending_dora()
        self._open_response(s.actor, action.tile, "discard", riichi=riichi)

    def _open_response(self, source, tile, kind, **extra):
        s = self._state
        s.pending = dict(source=source, tile=tile, kind=kind, responses=[], **extra)
        s.phase = Phase.RESPONSE
        s.actor = (source + 1) % 4

    def _win_value(self, seat, tsumo=False):
        s, p = self._state, self._state.players[seat]
        if tsumo:
            if p.hand.drawn_tile is None:
                return None
            tile = p.hand.drawn_tile.tile
        else:
            tile = s.pending["tile"]
            waiting = waits(p.hand.known_tiles)
            if (index(tile) not in waiting or ron_restrictions(
                    waiting, [d.tile for d in p.discards], s.temporary_furiten[seat], s.riichi_furiten[seat])):
                return None
            if s.pending["kind"] == "closed_kan":
                # Only thirteen orphans may rob a closed kan in this local ruleset.
                tiles = p.hand.known_tiles + [tile]
                if p.melds or len(tiles) != 14 or {index(t) for t in tiles} != TERMINALS:
                    return None
        first = not s.interrupted and not p.discards
        robbing = not tsumo and s.pending["kind"] != "discard"
        return evaluate(p, tile, tsumo=tsumo, round_wind=s.round_wind, dealer=s.dealer,
                        indicators=[s.wall[130 - 2 * i] for i in range(s.dora_count)],
                        ura=[s.wall[131 - 2 * i] for i in range(s.dora_count)], rules=self.rules,
                        is_ippatsu=s.ippatsu[seat], is_daburu_riichi=s.double_riichi[seat],
                        is_rinshan=tsumo and s.rinshan, is_chankan=robbing,
                        is_haitei=tsumo and s.last_live_draw and not s.rinshan,
                        is_houtei=not tsumo and not robbing and s.last_live_draw,
                        is_tenhou=tsumo and first and seat == s.dealer,
                        is_chiihou=tsumo and first and seat != s.dealer)

    def _respond(self, action):
        s, pending = self._state, self._state.pending
        require(not action.is_tsumogiri, "Response has no drawn-tile flag")
        kind = action.kind
        require(kind in ("pass", "ron", "chi", "pon", "open_kan"), "Invalid response kind")
        if kind in ("pass", "ron"):
            require(not action.tiles, "Pass/ron take no contributed tiles")
        value = self._win_value(action.player)
        if kind == "ron":
            require(value is not None, "Ron unavailable: no yaku, incomplete hand, or furiten")
        elif kind != "pass":
            self._validate_call(action)
        pending["responses"].append(action)
        # Passing a complete waiting shape causes furiten even if it lacks a yaku.
        if (kind != "ron" and index(pending["tile"]) in waits(s.players[action.player].hand.known_tiles)
                and (pending["kind"] != "closed_kan" or value is not None)):
            if s.players[action.player].riichi:
                s.riichi_furiten[action.player] = True
            else:
                s.temporary_furiten[action.player] = True
        if len(pending["responses"]) < 3:
            s.actor = (s.actor + 1) % 4
            return
        self._resolve()

    def _validate_call(self, action):
        s, pending = self._state, self._state.pending
        p, called = s.players[action.player], pending["tile"]
        require(pending["kind"] == "discard" and s.remaining_draws > 0, "Cannot call this tile")
        require(not p.riichi, "Cannot call after riichi")
        require(not self._four_kans(), "Only ron/pass after the fourth kan discard")
        require(not (Counter(action.tiles) - Counter(p.hand.known_tiles)), "Contributed tiles not in hand")
        ids = sorted(index(t) for t in (*action.tiles, called))
        if action.kind == "chi":
            require(action.player == (pending["source"] + 1) % 4, "Chi is only allowed from the upper player")
            require(len(ids) == 3 and ids[0] < 27 and ids[0] // 9 == ids[2] // 9
                    and ids == list(range(ids[0], ids[0] + 3)), "Invalid chi combination")
        else:
            require(len(ids) == (4 if action.kind == "open_kan" else 3) and len(set(ids)) == 1, "Invalid pon/kan combination")
        if action.kind == "open_kan":
            require(s.kan_count < 4, "At most four kans")
        else:
            remaining = list(p.hand.known_tiles)
            for t in action.tiles:
                remaining.remove(t)
            require(any(index(t) not in call_banned(action, called) for t in remaining), "Call leaves no legal discard (kuikae)")

    def _resolve(self):
        s, pending = self._state, self._state.pending
        source, responses = pending["source"], pending["responses"]
        winners = [a.player for a in responses if a.kind == "ron"]
        if len(winners) == 3:
            self._abort("three_ron")
            return
        if winners:
            values = [self._win_value(seat) for seat in winners]
            self._win(winners, source, values)
            return
        if pending["kind"] != "discard":
            self._complete_kan(pending["action"])
            return
        if pending["riichi"]:
            p = s.players[source]
            p.riichi = True
            p.score -= 1000
            s.riichi_sticks += 1
            s.double_riichi[source] = first_uninterrupted_discard(s.interrupted, len(p.discards) - 1)
            s.ippatsu[source] = True
            self._events.append({"type": "riichi", "player": source})
        if all(p.riichi for p in s.players):
            self._abort("four_riichi")
            return
        if self._four_kans():
            self._abort("four_kans")
            return
        if (not s.interrupted and all(len(p.discards) == 1 for p in s.players)
                and len({index(p.discards[0].tile) for p in s.players}) == 1
                and index(s.players[0].discards[0].tile) in range(27, 31)):
            self._abort("four_winds")
            return
        calls = [a for a in responses if a.kind in ("chi", "pon", "open_kan")]
        if calls:
            action = min(calls, key=lambda a: 1 if a.kind == "chi" else 0)
            self._call(action)
        elif s.remaining_draws == 0:
            self._exhaustive()
        else:
            s.pending = None
            s.actor = (source + 1) % 4
            s.phase = Phase.DISCARD
            self._draw()

    def _interrupt(self):
        self._state.interrupted = True
        self._state.ippatsu = [False] * 4

    def _call(self, action):
        s, pending = self._state, self._state.pending
        p = s.players[action.player]
        for tile in action.tiles:
            p.hand.known_tiles.remove(tile)
        discard = s.players[pending["source"]].discards[-1]
        discard.claimed_by = action.player
        p.melds.append(Meld(MeldKind(action.kind), [*action.tiles, pending["tile"]], discard.id))
        exposed = {index(m.tiles[0]) for m in p.melds if m.kind != MeldKind.CHI and m.kind != MeldKind.CLOSED_KAN}
        if ({31, 32, 33} <= exposed or {27, 28, 29, 30} <= exposed) and action.player not in s.pao:
            s.pao[action.player] = pending["source"]
        s.kuikae = call_banned(action, pending["tile"]) if action.kind != "open_kan" else set()
        s.actor, s.phase, s.pending = action.player, Phase.DISCARD, None
        self._interrupt()
        self._events.append({"type": action.kind, "player": action.player, "tiles": [asdict(t) for t in p.melds[-1].tiles],
                             "from_player": pending["source"]})
        if action.kind == "open_kan":
            self._kan_draw(closed=False)

    def _declare_kan(self, action):
        s, p = self._state, self._state.players[action.player]
        require(not action.is_tsumogiri and len(action.tiles) == 1, "Choose one tile type for kan")
        require(p.hand.drawn_tile is not None and s.remaining_draws > 0 and s.kan_count < 4, "Kan needs a draw and replacement capacity")
        tile = action.tiles[0]
        hand = self._concealed(p)
        require(tile in hand, "Kan tile not in hand")
        same = [t for t in hand if index(t) == index(tile)]
        if action.kind == "closed_kan":
            require(len(same) == 4, "Closed kan requires four matching tiles")
            if p.riichi:
                require(index(p.hand.drawn_tile.tile) == index(tile), "Riichi kan must use the drawn fourth tile")
                after = [t for t in hand if index(t) != index(tile)]
                require(waits(after) == waits(p.hand.known_tiles), "Riichi kan must preserve all waits")
        else:
            require(not p.riichi and any(m.kind == MeldKind.PON and index(m.tiles[0]) == index(tile) for m in p.melds),
                    "Added kan requires an existing pon and no riichi")
        # Announce only the prospective kan tile; no replacement/dora before robbing resolves.
        self._events.append({"type": "kan_offer", "player": action.player, "tile": asdict(tile), "kind": action.kind})
        self._open_response(action.player, tile, action.kind, action=action)

    def _complete_kan(self, action):
        s, p = self._state, self._state.players[action.player]
        tile = action.tiles[0]
        tiles = self._concealed(p)
        if action.kind == "closed_kan":
            same = [t for t in tiles if index(t) == index(tile)]
            tiles = [t for t in tiles if index(t) != index(tile)]
            p.melds.append(Meld(MeldKind.CLOSED_KAN, same))
        else:
            tiles.remove(tile)
            meld = next(m for m in p.melds if m.kind == MeldKind.PON and index(m.tiles[0]) == index(tile))
            meld.kind = MeldKind.ADDED_KAN
            meld.tiles.append(tile)
        p.hand.known_tiles, p.hand.drawn_tile = tiles, None
        s.actor, s.phase, s.pending = action.player, Phase.DISCARD, None
        self._interrupt()
        self._events.append({"type": action.kind, "player": action.player, "tile": asdict(tile)})
        self._kan_draw(closed=action.kind == "closed_kan")

    def _reveal_pending_dora(self):
        s = self._state
        s.dora_count += s.pending_dora
        s.pending_dora = 0

    def _kan_draw(self, closed):
        s = self._state
        self._reveal_pending_dora()
        # Rinshan slots 135..132; indicators 130,128,126,124,122 (ura immediately after).
        tile = s.wall[135 - s.kan_count]
        s.kan_count += 1
        s.live_end -= 1
        if self.rules.kan_dora_enabled:
            if closed:
                s.dora_count += 1
            else:
                s.pending_dora += 1
        s.players[s.actor].hand.drawn_tile = DrawnTile(tile)
        s.temporary_furiten[s.actor] = False
        s.rinshan, s.last_live_draw = True, False
        self._events.append(Draw(s.actor, tile))

    def _four_kans(self):
        s = self._state
        return s.kan_count == 4 and sum(any(len(m.tiles) == 4 for m in p.melds) for p in s.players) > 1

    def _finish(self, kind, deltas, *, winners=(), values=(), tenpai=(), dealer_continues=False, award_sticks=False):
        s = self._state
        before = [p.score for p in s.players]
        sticks = s.riichi_sticks
        recipient = winners[0] if award_sticks and winners else None
        if recipient is not None:
            deltas[recipient] += sticks * 1000
            s.riichi_sticks = 0
        for p, delta in zip(s.players, deltas):
            p.score += delta
        s.result = dict(kind=kind, winners=list(winners), values=list(values), tenpai=list(tenpai),
                        scores_before=before, scores_after=[p.score for p in s.players], deltas=deltas,
                        riichi_sticks_before=sticks, riichi_sticks_after=s.riichi_sticks, sticks_recipient=recipient,
                        dealer_continues=dealer_continues, next_dealer=s.dealer if dealer_continues else (s.dealer + 1) % 4,
                        next_honba=s.honba + 1 if dealer_continues or kind not in ("ron", "tsumo") else 0)
        s.result["starting_scores"] = list(self._config["scores"])
        s.result["round_deltas"] = [p.score - initial for p, initial in zip(s.players, self._config["scores"])]
        s.result["next_hand_number"] = s.hand_number if dealer_continues else s.hand_number % 4 + 1
        s.result["next_round_wind"] = (WINDS[(WINDS.index(s.round_wind) + 1) % 4]
                                        if not dealer_continues and s.hand_number == 4 else s.round_wind)
        s.pending, s.phase = None, Phase.ENDED
        self._events.append({"type": "end", "result": deepcopy(s.result)})

    def _win(self, winners, source, values):
        s, deltas, details = self._state, [0] * 4, []
        for n, (seat, value) in enumerate(zip(winners, values)):
            payments = {}
            cost = value["cost"]
            if source is None:
                payments = {i: cost["main"] if seat == s.dealer or i == s.dealer else cost["additional"]
                            for i in range(4) if i != seat}
            else:
                payments[source] = cost["main"]
            if seat in s.pao and any(y["yakuman"] for y in value["yaku"]):
                responsible = s.pao[seat]
                total = sum(payments.values())
                payments = {responsible: total} if source is None or source == responsible else {source: total // 2, responsible: total // 2}
                if n == 0:
                    payments[responsible] += s.honba * 300
            elif n == 0:
                for payer in payments:
                    payments[payer] += s.honba * (300 if source is not None else 100)
            for payer, amount in payments.items():
                deltas[payer] -= amount
                deltas[seat] += amount
            p = s.players[seat]
            win_tile = p.hand.drawn_tile.tile if source is None else s.pending["tile"]
            details.append(dict(player=seat, from_player=source, win_tile=asdict(win_tile),
                                hand=[asdict(t) for t in p.hand.known_tiles], melds=[asdict(m) for m in p.melds],
                                ura_indicators=[asdict(s.wall[131 - 2 * i]) for i in range(s.dora_count)] if p.riichi else [],
                                payments=payments, **value))
        self._finish("tsumo" if source is None else "ron", deltas, winners=winners, values=details,
                     dealer_continues=s.dealer in winners, award_sticks=True)

    def _abort(self, reason):
        self._finish(reason, [0] * 4, dealer_continues=True)

    def _exhaustive(self):
        s, deltas = self._state, [0] * 4
        tenpai = [p.seat for p in s.players if waits(p.hand.known_tiles)]
        nagashi = [p.seat for p in s.players if p.discards and all(index(d.tile) in TERMINALS and d.claimed_by is None for d in p.discards)]
        if nagashi:
            for winner in nagashi:
                for payer in range(4):
                    if payer != winner:
                        amount = 4000 if s.dealer in (winner, payer) else 2000
                        deltas[payer] -= amount
                        deltas[winner] += amount
        elif 0 < len(tenpai) < 4:
            deltas = [3000 // len(tenpai) if i in tenpai else -3000 // (4 - len(tenpai)) for i in range(4)]
        self._finish("nagashi_mangan" if nagashi else "exhaustive", deltas, winners=nagashi,
                     tenpai=tenpai, dealer_continues=s.dealer in tenpai)

    def legal_actions(self, seat):
        """Actions for this seat only; never expose other players' unsubmitted choices."""
        require(seat_valid(seat), "Invalid seat")
        s = self._state
        if self.paused or s.phase == Phase.ENDED or seat != s.actor:
            return []
        p, candidates = s.players[seat], []
        if s.phase == Phase.RESPONSE:
            candidates = [Decision(seat, "pass"), Decision(seat, "ron")]
            for kind, size in (("chi", 2), ("pon", 2), ("open_kan", 3)):
                candidates.extend(Decision(seat, kind, tiles) for tiles in set(combinations(sorted(p.hand.known_tiles, key=str), size)))
        else:
            for tile in set(p.hand.known_tiles):
                candidates.extend((Discard(seat, tile, False), Decision(seat, "riichi", (tile,), False)))
            if p.hand.drawn_tile:
                tile = p.hand.drawn_tile.tile
                candidates.extend((Discard(seat, tile, True), Decision(seat, "riichi", (tile,), True)))
            candidates.extend(Decision(seat, k) for k in ("tsumo", "nine_terminals"))
            for tile in set(self._concealed(p)):
                candidates.extend(Decision(seat, k, (tile,)) for k in ("closed_kan", "added_kan"))
        result = []
        for action in candidates:
            # Validate only; never resolve opponents or evaluate the next hidden draw for menus.
            try:
                if s.phase == Phase.RESPONSE:
                    if action.kind == "ron":
                        require(self._win_value(seat) is not None, "No ron")
                    elif action.kind != "pass":
                        self._validate_call(action)
                else:
                    trial = self._candidate()
                    trial._apply(action)
                result.append(action)
            except (ValueError, TypeError):
                pass
        return sorted(result, key=lambda a: json.dumps(self._encode_action(a), sort_keys=True))

    @staticmethod
    def _encode_action(action):
        return {"type": "discard" if isinstance(action, Discard) else "decision", **asdict(action)}

    def _view(self, seat, omniscient):
        s = self._state
        players = deepcopy(s.players)
        for p in players:
            if not omniscient and p.seat != seat:
                p.hand.unknown_count = len(p.hand.known_tiles)
                p.hand.known_tiles = []
                if p.hand.drawn_tile is not None:
                    p.hand.drawn_tile = DrawnTile()
        events = []
        for event in self._events:
            if isinstance(event, dict):
                events.append(deepcopy(event))
                continue
            item = {"type": "draw" if isinstance(event, Draw) else "discard", **asdict(event)}
            if isinstance(event, Draw) and not omniscient and event.player != seat:
                item["tile"] = None
            events.append(item)
        pending = None
        if s.pending:
            pending = {"source": s.pending["source"], "tile": asdict(s.pending["tile"]), "kind": s.pending["kind"],
                       "responded": [a.player for a in s.pending["responses"]],
                       "responses": [self._encode_action(a) for a in s.pending["responses"] if omniscient or a.player == seat]}
        return {
            "players": [asdict(p) for p in players], "observer_seat": seat,
            "dealer_seat": s.dealer, "actor": s.actor, "phase": s.phase.value,
            "round_wind": s.round_wind, "hand_number": s.hand_number, "honba": s.honba,
            "riichi_sticks": s.riichi_sticks, "remaining_draws": s.remaining_draws,
            "dora_indicators": [asdict(s.wall[130 - 2 * i]) for i in range(s.dora_count)], "events": events,
            "pending": pending, "result": deepcopy(s.result), "rules": asdict(self.rules),
        }

    def observe(self, seat):
        require(seat_valid(seat), "Invalid observer seat")
        view = self._view(seat, False)
        view["own_status"] = {"temporary_furiten": self._state.temporary_furiten[seat],
                              "riichi_furiten": self._state.riichi_furiten[seat],
                              "double_riichi": self._state.double_riichi[seat]}
        view["riichi_context"] = {"double_eligible": first_uninterrupted_discard(
            self._state.interrupted, len(self._state.players[seat].discards))}
        return view

    def debug_view(self):
        """Explicitly privileged output; never supply to a player policy."""
        result = self._view(None, True)
        result.update(omniscient=True, seed=self._seed,
                      remaining_wall=[asdict(t) for t in self._state.wall[self._state.next_draw:self._state.live_end]],
                      reserved_wall=[asdict(t) for t in self._state.wall[self._state.live_end:136-self._state.kan_count]],
                      diagnostics=deepcopy(self._diagnostics))
        return result

    def debug_state(self):
        return deepcopy(self._state)

    def review_records(self, seat):
        require(seat_valid(seat), "Invalid review seat")
        return deepcopy([r for r in self._reviews if r["player"] == seat])

    def debug_reviews(self):
        """All seats' private training records; privileged just like debug_view."""
        return deepcopy(self._reviews)

    def decision_records(self, seat):
        require(seat_valid(seat), "Invalid decision seat")
        return deepcopy([r for r in self._decision_records if r["player"] == seat])

    def debug_decisions(self):
        return deepcopy(self._decision_records)

    def replay(self, count):
        require(type(count) is int and 0 <= count <= self.action_count, "Invalid replay position")
        game = Game.from_wall(self._initial_wall, self._state.dealer, rules=self.rules, **self._config)
        game._seed = self._seed
        for action in self._actions[:count]:
            game._submit(action, record_review=False)
        game._reviews = deepcopy([r for r in self._reviews if r["action_index"] <= count])
        game._decision_records = deepcopy([r for r in self._decision_records if r["action_index"] <= count])
        return game

    def rewind(self, count):
        replayed = self.replay(count)
        self._state, self._events, self._actions = replayed._state, replayed._events, replayed._actions
        self._reviews = replayed._reviews
        self._decision_records = replayed._decision_records

    def undo(self):
        require(self.action_count > 0, "No action to undo")
        self.rewind(self.action_count - 1)

    def save(self, path):
        """Full debug checkpoint, not a player observation export."""
        payload = {"format": "nanikiru-game", "version": 7, "seed": self._seed,
                   "dealer": self._state.dealer, "initial_wall": [str(t) for t in self._initial_wall],
                   "actions": [self._encode_action(a) for a in self._actions], "paused": self.paused,
                   "rules": asdict(self.rules), "config": self._config, "diagnostics": self._diagnostics,
                   "reviews": self._reviews, "decision_records": self._decision_records}
        path = Path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
                temporary = f.name
                json.dump(payload, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            require(data["format"] == "nanikiru-game" and type(data["version"]) is int
                    and data["version"] in (2, 3, 4, 5, 6, 7), "Requires game format v2/v3/v4/v5/v6/v7; v1 ordinary-draw saves cannot be replayed under full rules")
            require(type(data["paused"]) is bool and (data["seed"] is None or type(data["seed"]) is int),
                    "Invalid checkpoint metadata")
            game = cls.from_wall([Tile.parse(t) for t in data["initial_wall"]], data["dealer"],
                                 rules=Rules(**data["rules"]), **data["config"])
            game._seed = data["seed"]
            policies = {r["action_index"]: r["policy"] for r in data["decision_records"]} if data["version"] >= 4 else {}
            modes = {r["action_index"]: r.get("defense", {}).get("mode") for r in data.get("decision_records", [])}
            reviews = {r["action_index"]: r for r in data.get("reviews", [])}
            decisions = {r["action_index"]: r for r in data.get("decision_records", [])}
            for a in data["actions"]:
                a = dict(a)
                kind = a.pop("type")
                require(kind in ("discard", "decision"), "Unknown saved action")
                if kind == "discard":
                    action = Discard(**{**a, "tile": Tile(**a["tile"])})
                else:
                    action = Decision(**{**a, "tiles": tuple(Tile(**t) for t in a["tiles"])})
                review = reviews.get(game.action_count + 1, {})
                decision = decisions.get(game.action_count + 1, {})
                game._submit(action, record_review=True,
                            review_status="own_status" in review.get("observation", {}),
                            decision_status="own_status" in decision.get("observation", {}),
                            review_context="riichi_context" in review.get("observation", {}),
                            decision_context="riichi_context" in decision.get("observation", {}),
                            riichi_review="riichi" in decision,
                            tenpai="tenpai" in review, policy=policies.get(game.action_count + 1, "unknown-legacy" if data["version"] < 4 else "manual"),
                            mode=modes.get(game.action_count + 1) if data["version"] >= 5 else None)
            game._paused = data["paused"]
            require(isinstance(data["diagnostics"], list), "Invalid diagnostics")
            game._diagnostics = data["diagnostics"]
            if data["version"] >= 3:
                require(data["reviews"] == game._reviews,
                        "Training records disagree with replay or use an unsupported analysis version")
            if data["version"] >= 4:
                require(data["decision_records"] == game._decision_records, "Decision records disagree with replay/version")
            return game
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise RecordError(f"Invalid game checkpoint: {exc}") from exc
