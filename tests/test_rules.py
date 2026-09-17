from collections import Counter
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest

from nanikiru import Decision, Discard, Game, Phase, RecordError, Rules, Tile
from nanikiru.game import make_tiles
from nanikiru.models import HandState, Meld, MeldKind, PlayerState
from nanikiru.scoring import evaluate, waits


def tiles(text):
    return [Tile.parse(t) for t in text.split()]


def fixture(hands=None, draws=(), slots=None, **kwargs):
    """Build a real 136-tile wall, with specified hands/live/replacement tiles."""
    fixed = dict(slots or {})
    for seat, hand in (hands or {}).items():
        for n, tile in enumerate(tiles(hand)):
            fixed[seat + 4 * n] = tile
    for n, tile in enumerate(tiles(draws) if isinstance(draws, str) else draws):
        fixed[52 + n] = tile
    remaining = make_tiles()
    for tile in fixed.values():
        remaining.remove(tile)
    # Distribute unrequested tiles predictably, rather than creating fake private state.
    import random
    random.Random(719).shuffle(remaining)
    wall = [fixed[i] if i in fixed else remaining.pop() for i in range(136)]
    return Game.from_wall(wall, **kwargs)


def pass_all(g):
    while g.debug_state().phase == Phase.RESPONSE:
        g.submit(Decision(g.debug_state().actor, "pass"))


def discard_draw(g):
    s = g.debug_state()
    g.submit(Discard(s.actor, s.players[s.actor].hand.drawn_tile.tile, True))


WAIT = "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s"


class RulesTests(unittest.TestCase):
    def assert_inventory(self, g):
        s = g.debug_state()
        actual = s.wall[s.next_draw:s.live_end] + s.wall[s.live_end:136-s.kan_count]
        for p in s.players:
            actual += p.hand.known_tiles
            if p.hand.drawn_tile:
                actual.append(p.hand.drawn_tile.tile)
            actual += [t for m in p.melds for t in m.tiles]
            actual += [d.tile for d in p.discards if d.claimed_by is None]
        self.assertEqual(Counter(actual), Counter(make_tiles()))

    def assert_roundtrip(self, g):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "round.json"
            g.save(path)
            loaded = Game.load(path)
            self.assertEqual(loaded.debug_view(), g.debug_view())
            self.assertEqual(loaded.paused, g.paused)
        self.assertEqual(g.replay(g.action_count).debug_state(), g.debug_state())

    def test_random_seed_and_round_wind(self):
        a, b = Game(), Game()
        self.assertNotEqual(a.debug_view()["seed"], b.debug_view()["seed"])
        self.assertEqual(Game(a.debug_view()["seed"]).debug_state(), a.debug_state())
        self.assertEqual(Game(round_wind="west").observe(0)["round_wind"], "west")

    def test_response_order_atomic_pause_and_checkpoint(self):
        g = fixture({1: "2m 3m", 2: "1m 1m"}, "1m")
        discard_draw(g)
        before = g.debug_state()
        g.submit(Decision(1, "chi", tuple(tiles("2m 3m"))))
        self.assertEqual(g.debug_state().next_draw, before.next_draw)
        before = g.debug_state()
        with self.assertRaises(RecordError):
            g.submit(Decision(2, "chi", tuple(tiles("1m 1m"))))
        self.assertEqual(g.debug_state(), before)
        self.assertTrue(g.paused)
        self.assert_roundtrip(g)
        self.assertEqual(g.observe(3)["pending"]["responses"], [])
        self.assertEqual(len(g.observe(1)["pending"]["responses"]), 1)
        g.resume()
        g.submit(Decision(2, "pon", tuple(tiles("1m 1m"))))
        g.submit(Decision(3, "pass"))
        self.assertEqual(g.debug_state().actor, 2)
        self.assertEqual(g.debug_state().players[1].melds, [])
        self.assertEqual(g.debug_state().players[2].melds[0].kind, MeldKind.PON)
        self.assert_inventory(g)
        self.assert_roundtrip(g)

    def test_chi_kuikae_and_no_draw(self):
        g = fixture({1: "2m 3m 4m 1m"}, "1m")
        discard_draw(g)
        g.submit(Decision(1, "chi", tuple(tiles("2m 3m"))))
        pass_all(g)
        s = g.debug_state()
        self.assertIsNone(s.players[1].hand.drawn_tile)
        self.assertEqual(s.remaining_draws, 69)
        for code in ("1m", "4m"):
            with self.assertRaisesRegex(RecordError, "Kuikae"):
                g.submit(Discard(1, Tile.parse(code), False))
            self.assertEqual(s, g.debug_state())
            g.resume()
        action = next(a for a in g.legal_actions(1) if isinstance(a, Discard))
        g.submit(action)
        pass_all(g)
        self.assertEqual(g.debug_state().actor, 2)
        self.assert_inventory(g)

    def test_open_kan_deferred_dora_and_replacement(self):
        for enabled in (True, False):
            g = fixture({1: "1m 1m 1m"}, "1m", slots={135: Tile.parse("2z")}, rules=Rules(kan_dora_enabled=enabled))
            discard_draw(g)
            g.submit(Decision(1, "open_kan", tuple(tiles("1m 1m 1m"))))
            pass_all(g)
            s = g.debug_state()
            self.assertEqual(s.players[1].hand.drawn_tile.tile, Tile.parse("2z"))
            self.assertEqual((s.kan_count, s.remaining_draws, s.dora_count), (1, 68, 1))
            discard_draw(g)
            self.assertEqual(g.debug_state().dora_count, 2 if enabled else 1)
            self.assert_inventory(g)
            self.assert_roundtrip(g)

    def test_closed_kan_and_added_kan(self):
        g = fixture({0: "1m 1m 1m"}, "1m")
        g.submit(Decision(0, "closed_kan", (Tile.parse("1m"),)))
        self.assertEqual(g.debug_state().kan_count, 0)
        pass_all(g)
        self.assertEqual(g.debug_state().dora_count, 2)
        self.assertEqual(g.debug_state().players[0].melds[0].kind, MeldKind.CLOSED_KAN)
        self.assert_inventory(g)
        self.assert_roundtrip(g)

        g = fixture({1: "1m 1m 1m"}, "1m 9p 9s 9m 8s")
        discard_draw(g)
        g.submit(Decision(1, "pon", tuple(tiles("1m 1m"))))
        pass_all(g)
        g.submit(next(a for a in g.legal_actions(1) if isinstance(a, Discard) and a.tile != Tile.parse("1m")))
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        g.submit(Decision(1, "added_kan", (Tile.parse("1m"),)))
        pass_all(g)
        self.assertEqual(g.debug_state().players[1].melds[0].kind, MeldKind.ADDED_KAN)
        self.assert_inventory(g)
        self.assert_roundtrip(g)

    def test_riichi_establishment_restrictions_and_replay(self):
        g = fixture({0: WAIT}, "9m 9p 9s 8m 8p")
        g.submit(Decision(0, "riichi", (Tile.parse("9m"),), True))
        self.assertFalse(g.debug_state().players[0].riichi)
        self.assertEqual(g.debug_state().players[0].score, 25000)
        pass_all(g)
        s = g.debug_state()
        self.assertTrue(s.players[0].riichi)
        self.assertEqual((s.players[0].score, s.riichi_sticks), (24000, 1))
        self.assertTrue(s.double_riichi[0])
        self.assertTrue(s.ippatsu[0])
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        with self.assertRaisesRegex(RecordError, "After riichi"):
            g.submit(Discard(0, Tile.parse("1m"), False))
        g.resume()
        discard_draw(g)
        self.assertFalse(g.debug_state().ippatsu[0])
        self.assert_roundtrip(g)

    def test_ron_and_riichi_declaration_ron_does_not_pay_deposit(self):
        # Dealer waits on a different tile; its riichi declaration discard deals in.
        g = fixture({0: "1m 1m 1m 2m 2m 2m 3m 3m 3m 4m 4m 4m 5m", 1: WAIT}, "6s", honba=2, riichi_sticks=1)
        g.submit(Decision(0, "riichi", (Tile.parse("6s"),), True))
        g.submit(Decision(1, "ron"))
        pass_all(g)
        r = g.debug_state().result
        self.assertEqual(r["kind"], "ron")
        self.assertEqual(r["winners"], [1])
        self.assertEqual(r["scores_before"], [25000] * 4)
        self.assertEqual(sum(r["deltas"]), 1000)
        self.assertEqual(r["values"][0]["payments"][0], r["values"][0]["cost"]["main"] + 600)
        self.assertFalse(g.debug_state().players[0].riichi)
        self.assert_roundtrip(g)

    def test_tsumo_tenhou_and_ended_rejects(self):
        g = fixture({0: WAIT}, "6s")
        g.submit(Decision(0, "tsumo"))
        r = g.debug_state().result
        self.assertEqual(r["kind"], "tsumo")
        self.assertTrue(any(y["name"] == "Tenhou" for y in r["values"][0]["yaku"]))
        self.assertEqual(r["deltas"], [48000, -16000, -16000, -16000])
        self.assert_roundtrip(g)
        with self.assertRaises(RecordError):
            g.submit(Decision(0, "tsumo"))

    def test_nine_terminals_and_four_winds(self):
        g = fixture({0: "1m 9m 1p 9p 1s 9s 1z 2z 3z"})
        g.submit(Decision(0, "nine_terminals"))
        self.assertEqual(g.debug_state().result["kind"], "nine_terminals")
        self.assert_roundtrip(g)
        g = fixture(draws="1z 1z 1z 1z")
        for _ in range(4):
            discard_draw(g)
            pass_all(g)
        self.assertEqual(g.debug_state().result["kind"], "four_winds")
        self.assertEqual(g.debug_state().result["deltas"], [0] * 4)
        self.assert_roundtrip(g)

    def test_four_riichi(self):
        hands = {seat: " ".join([f"{r}{suit}" for r in range(1, 5) for _ in range(3)] + [f"5{suit}"])
                 for seat, suit in enumerate("mpsz")}
        g = fixture(hands, "9m 9p 9s 6z")
        for seat in range(4):
            tile = g.debug_state().players[seat].hand.drawn_tile.tile
            g.submit(Decision(seat, "riichi", (tile,), True))
            pass_all(g)
        self.assertEqual(g.debug_state().result["kind"], "four_riichi")
        self.assertEqual(g.debug_state().riichi_sticks, 4)
        self.assertEqual([p.score for p in g.debug_state().players], [24000] * 4)
        self.assert_roundtrip(g)

    def test_complete_manual_round_exhaustion(self):
        g = Game(87)
        while g.debug_state().phase != Phase.ENDED:
            if g.debug_state().phase == Phase.RESPONSE:
                pass_all(g)
            else:
                discard_draw(g)
            self.assert_inventory(g)
        self.assertEqual(g.debug_state().result["kind"], "exhaustive")
        self.assertEqual(g.action_count, 280)
        self.assertEqual(sum(p.score for p in g.debug_state().players), 100000)
        self.assert_roundtrip(g)

    def test_temporary_and_discard_furiten(self):
        g = fixture({1: WAIT}, "6s 9m 6s 9p 6s")
        discard_draw(g)
        self.assertTrue(any(a.kind == "ron" for a in g.legal_actions(1)))
        g.submit(Decision(1, "pass"))
        self.assertTrue(g.debug_state().temporary_furiten[1])

        pass_all(g)
        self.assertFalse(g.debug_state().temporary_furiten[1])
        discard_draw(g)
        pass_all(g)
        discard_draw(g)
        pass_all(g)
        self.assertTrue(g.debug_state().temporary_furiten[1])
        discard_draw(g)
        pass_all(g)
        discard_draw(g)
        g.submit(Decision(1, "pass"))
        self.assertTrue(g.debug_state().temporary_furiten[1])

    def test_double_and_triple_ron(self):
        hands = {1: WAIT, 2: "5z 5z 5z 7m 8m 9m 7p 8p 9p 6z 6z 4s 5s",
                 3: "1z 1z 1z 4m 5m 6m 4p 5p 6p 2z 2z 4s 5s"}
        for triple in (False, True):
            g = fixture(hands, "6s", honba=2, riichi_sticks=2)
            discard_draw(g)
            for seat in (1, 2, 3):
                g.submit(Decision(seat, "ron" if seat != 3 or triple else "pass"))
            r = g.debug_state().result
            if triple:
                self.assertEqual(r["kind"], "three_ron")
                self.assertEqual(r["deltas"], [0] * 4)
                self.assertEqual(r["riichi_sticks_after"], 2)
            else:
                self.assertEqual(r["winners"], [1, 2])
                self.assertEqual(r["sticks_recipient"], 1)
                self.assertEqual(r["values"][1]["payments"][0], r["values"][1]["cost"]["main"])
                self.assertEqual(sum(r["deltas"]), 2000)
            self.assert_roundtrip(g)

    def test_own_discard_and_riichi_furiten(self):
        g = fixture({1: WAIT}, "9m 6s 6s")
        for _ in range(2):
            discard_draw(g)
            pass_all(g)
        discard_draw(g)
        g.submit(Decision(3, "pass"))
        g.submit(Decision(0, "pass"))
        with self.assertRaisesRegex(RecordError, "furiten"):
            g.submit(Decision(1, "ron"))
        g.resume()
        pass_all(g)

        g = fixture({1: WAIT}, "9m 9p 6s 8p 8m 8s")
        discard_draw(g)
        pass_all(g)
        g.submit(Decision(1, "riichi", (Tile.parse("9p"),), True))
        pass_all(g)
        discard_draw(g)
        pass_all(g)
        self.assertTrue(g.debug_state().riichi_furiten[1])
        for _ in range(2):
            discard_draw(g)
            pass_all(g)
        self.assertEqual(g.debug_state().actor, 1)
        self.assertTrue(g.debug_state().riichi_furiten[1])
        self.assert_roundtrip(g)

    def test_riichi_closed_kan_and_changed_wait_rejected(self):
        hand = "1m 1m 1m 2p 3p 4p 2s 3s 4s 5z 5z 6s 7s"
        g = fixture({0: hand}, "9p 9m 9s 8m 1m")
        g.submit(Decision(0, "riichi", (Tile.parse("9p"),), True))
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        g.submit(Decision(0, "closed_kan", (Tile.parse("1m"),)))
        pass_all(g)
        self.assertTrue(g.debug_state().players[0].riichi)
        self.assertEqual(g.debug_state().kan_count, 1)
        self.assertFalse(g.debug_state().ippatsu[0])
        self.assert_inventory(g)
        self.assert_roundtrip(g)

        hand = "1m 1m 1m 2m 3m 4m 5m 6m 7m 8m 9m 9m 9m"
        g = fixture({0: hand}, "9p 9s 8s 8p 1m")
        g.submit(Decision(0, "riichi", (Tile.parse("9p"),), True))
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        with self.assertRaisesRegex(RecordError, "preserve all waits"):
            g.submit(Decision(0, "closed_kan", (Tile.parse("1m"),)))

    def test_robbing_closed_kan_only_kokushi(self):
        orphan = "9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 7z"
        g = fixture({0: "1m 1m 1m", 1: orphan}, "1m")
        g.submit(Decision(0, "closed_kan", (Tile.parse("1m"),)))
        g.submit(Decision(1, "ron"))
        pass_all(g)
        self.assertEqual(g.debug_state().result["kind"], "ron")
        self.assertEqual(g.debug_state().kan_count, 0)
        self.assertEqual(g.debug_state().dora_count, 1)
        self.assert_roundtrip(g)

    def test_robbing_added_kan(self):
        # Seat 2 waits on 1m; seat 1 first pons it, later offers the fourth copy.
        hand = "2m 3m 1p 2p 3p 1s 2s 3s 5z 5z 5z 7z 7z"
        g = fixture({1: "1m 1m 1m", 2: hand}, "1m 9p 9s 9m 8s")
        discard_draw(g)
        g.submit(Decision(1, "pon", tuple(tiles("1m 1m"))))
        pass_all(g)
        g.submit(next(a for a in g.legal_actions(1) if isinstance(a, Discard) and a.tile != Tile.parse("1m")))
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        g.submit(Decision(1, "added_kan", (Tile.parse("1m"),)))
        g.submit(Decision(2, "ron"))
        pass_all(g)
        r = g.debug_state().result
        self.assertEqual(r["winners"], [2])
        self.assertEqual(g.debug_state().kan_count, 0)
        self.assertTrue(any(y["name"] == "Chankan" for y in r["values"][0]["yaku"]))
        self.assert_roundtrip(g)

    def test_four_kans_timing_and_single_player_exception(self):
        # Four closed kans across two seats; fourth replacement discard must still allow ron.
        g = fixture({0: "1m 1m 1m 2m 2m 2m 2m", 1: "1p 1p 1p 1p 2p 2p 2p 2p"}, "1m 9s")
        for code in ("1m", "2m"):
            g.submit(Decision(0, "closed_kan", (Tile.parse(code),)))
            pass_all(g)
        discard_draw(g)
        pass_all(g)
        for code in ("1p", "2p"):
            g.submit(Decision(1, "closed_kan", (Tile.parse(code),)))
            pass_all(g)
        self.assertIsNone(g.debug_state().result)
        discard_draw(g)
        self.assertIsNone(g.debug_state().result)
        pass_all(g)
        self.assertEqual(g.debug_state().result["kind"], "four_kans")
        self.assert_inventory(g)
        self.assert_roundtrip(g)

        g = fixture({0: "1m 1m 1m 2m 2m 2m 3m 3m 3m 4m 4m 4m 5z"}, "1m",
                    slots={135: Tile.parse("2m"), 134: Tile.parse("3m"), 133: Tile.parse("4m"), 132: Tile.parse("6z")})
        for code in ("1m", "2m", "3m", "4m"):
            g.submit(Decision(0, "closed_kan", (Tile.parse(code),)))
            pass_all(g)
        discard_draw(g)
        pass_all(g)
        self.assertIsNone(g.debug_state().result)
        self.assertEqual(g.debug_state().actor, 1)
        self.assert_inventory(g)
        self.assert_roundtrip(g)

    def test_exhaustive_tenpai_and_nagashi(self):
        g = fixture({0: WAIT})
        while g.debug_state().phase != Phase.ENDED:
            if g.debug_state().phase == Phase.RESPONSE:
                pass_all(g)
            else:
                discard_draw(g)
        r = g.debug_state().result
        self.assertIn(0, r["tenpai"])
        self.assertEqual(r["kind"], "exhaustive")
        self.assertTrue(r["dealer_continues"])
        self.assertEqual(sum(r["deltas"]), 0)
        self.assert_roundtrip(g)

        # Ensure every draw at seat 0 is a terminal/honor without four-winds interruption.
        bank = [t for t in make_tiles() if t.suit == "z" or t.rank in (1, 9)]
        slots = {52 + 4 * n: bank[n] for n in range(18)}
        g = fixture(slots=slots)
        while g.debug_state().phase != Phase.ENDED:
            if g.debug_state().phase == Phase.RESPONSE:
                pass_all(g)
            else:
                discard_draw(g)
        r = g.debug_state().result
        self.assertEqual(r["kind"], "nagashi_mangan")
        self.assertIn(0, r["winners"])
        self.assertEqual(sum(r["deltas"]), 0)
        self.assert_roundtrip(g)

    def test_no_yaku_bonus_only_cannot_ron(self):
        hand = "1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 5s 5s"
        g = fixture({2: hand}, "2z", slots={130: Tile.parse("1z")})
        discard_draw(g)
        g.submit(Decision(1, "pass"))
        with self.assertRaisesRegex(RecordError, "no yaku"):
            g.submit(Decision(2, "ron"))

    def test_pao_ron_split_and_honba(self):
        g = fixture({1: "5z 5z 6z 6z 7z 7z 2p 2p 2m 3m 4s 5s 6s"}, "5z 6z 7z 9m 4m", honba=1)
        for call, discard in (("5z", "4s"), ("6z", "5s"), ("7z", "6s")):
            discard_draw(g)
            while g.debug_state().actor != 1:
                g.submit(Decision(g.debug_state().actor, "pass"))
            g.submit(Decision(1, "pon", (Tile.parse(call),) * 2))
            pass_all(g)
            g.submit(Discard(1, Tile.parse(discard), False))
            pass_all(g)
        self.assertEqual(g.debug_state().pao, {1: 2})
        discard_draw(g)
        pass_all(g)
        discard_draw(g)
        g.submit(Decision(0, "pass"))
        g.submit(Decision(1, "ron"))
        pass_all(g)
        r = g.debug_state().result
        self.assertEqual(r["values"][0]["payments"], {3: 16000, 2: 16300})
        self.assertEqual(r["deltas"], [0, 32300, -16300, -16000])
        self.assert_inventory(g)
        self.assert_roundtrip(g)

    def test_undo_error_requires_explicit_resume(self):
        g = Game(53)
        discard_draw(g)
        g.submit(Decision(1, "pass"))
        with self.assertRaises(RecordError):
            g.submit(Decision(1, "pass"))
        g.undo()
        self.assertTrue(g.paused)
        self.assertEqual(g.debug_state().actor, 1)
        self.assertEqual(g.debug_state().pending["responses"], [])
        with self.assertRaisesRegex(RecordError, "paused"):
            g.submit(Decision(1, "pass"))
        g.resume()
        g.submit(Decision(1, "pass"))
        self.assert_roundtrip(g)

    def test_last_live_draw_can_tsumo_before_exhaustive_settlement(self):
        g = fixture({1: WAIT}, "9m 9p 9s 8m", slots={121: Tile.parse("6s")})
        while not (g.debug_state().phase == Phase.DISCARD and g.debug_state().remaining_draws == 0):
            if g.debug_state().phase == Phase.RESPONSE:
                pass_all(g)
            else:
                discard_draw(g)
        self.assertEqual(g.debug_state().actor, 1)
        g.submit(Decision(1, "tsumo"))
        r = g.debug_state().result
        self.assertTrue(any(y["name"] == "Haitei Raoyue" for y in r["values"][0]["yaku"]))
        self.assertEqual(r["kind"], "tsumo")
        self.assertEqual(sum(r["deltas"]), 0)
        self.assert_roundtrip(g)


class ScoringTests(unittest.TestCase):
    def test_open_and_closed_kan_fu_and_rinshan(self):
        p = PlayerState(1, 25000, HandState(tiles("2p 3p 4p 2s 3s 4s 6s 7s 5p 5p")),
                        melds=[Meld(MeldKind.OPEN_KAN, tiles("1m 1m 1m 1m"))])
        r = evaluate(p, Tile.parse("8s"), tsumo=True, is_rinshan=True)
        self.assertEqual((r["han"], r["fu"], r["cost"]["main"], r["cost"]["additional"]), (1, 40, 700, 400))
        p.melds[0].kind = MeldKind.CLOSED_KAN
        r = evaluate(p, Tile.parse("8s"), tsumo=True, is_rinshan=True)
        self.assertEqual((r["han"], r["fu"], r["cost"]["main"], r["cost"]["additional"]), (2, 60, 2000, 1000))

    def test_known_hands_and_aka_switch(self):
        p = PlayerState(1, 25000, HandState(tiles("2m 3m 4m 2p 3p 4p 2s 3s 4s 6s 7s 0p 5p")))
        a = evaluate(p, Tile.parse("8s"), dealer=0, rules=Rules(True))
        b = evaluate(p, Tile.parse("8s"), dealer=0, rules=Rules(False))
        self.assertEqual((a["han"], b["han"], b["fu"]), (5, 4, 30))
        self.assertEqual(b["cost"]["main"], 7700)
        self.assertEqual(a["cost"]["main"], 8000)
        c = evaluate(p, Tile.parse("8s"), dealer=0, rules=Rules(False), indicators=tiles("4p"))
        self.assertEqual(c["han"], b["han"] + 2)  # red and ordinary five both count as normal dora

    def test_special_shapes_no_yaku_and_wind(self):
        p = PlayerState(1, 25000, HandState(tiles("1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z")))
        r = evaluate(p, Tile.parse("1z"))
        self.assertEqual((r["han"], r["fu"], r["cost"]["main"]), (2, 25, 1600))
        p.hand.known_tiles = tiles("1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z")
        r = evaluate(p, Tile.parse("1m"))
        self.assertEqual(r["cost"]["main"], 64000)
        p.hand.known_tiles = tiles("1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 5s 5s")
        self.assertIsNone(evaluate(p, Tile.parse("2z"), dealer=1, round_wind="west"))
        r = evaluate(p, Tile.parse("2z"), dealer=1, round_wind="south")
        self.assertEqual(r["han"], 1)
