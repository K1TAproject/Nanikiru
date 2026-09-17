from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import random
import tempfile
import unittest

from nanikiru.game import Game, make_tiles
from nanikiru.models import Decision, Discard, Draw, Phase, Tile
from nanikiru.recorder import RecordError


class GameTests(unittest.TestCase):
    def assert_inventory(self, game):
        s = game.debug_state()
        actual = list(s.wall[s.next_draw:])
        for p in s.players:
            actual.extend(p.hand.known_tiles)
            if p.hand.drawn_tile:
                actual.append(p.hand.drawn_tile.tile)
            actual.extend(d.tile for d in p.discards)
        self.assertEqual(Counter(actual), Counter(make_tiles()))
        self.assertEqual(len(actual), 136)

    def test_seed_dealing_and_local_random(self):
        rng = random.getstate()
        a, b = Game(12, dealer=2), Game(12, dealer=2)
        self.assertEqual(a.debug_state(), b.debug_state())
        self.assertNotEqual(a.debug_state().wall, Game(13, dealer=2).debug_state().wall)
        self.assertEqual(random.getstate(), rng)
        self.assertEqual([p.hand.count for p in a.debug_state().players], [13, 13, 14, 13])
        self.assertEqual(a.observe(2)["remaining_draws"], 69)
        self.assert_inventory(a)

    def test_full_round_and_replay(self):
        g = Game(33)
        while g.debug_state().phase != Phase.ENDED:
            s = g.debug_state()
            if s.phase == Phase.RESPONSE:
                g.submit(Decision(s.actor, "pass"))
                continue
            h = s.players[s.actor].hand
            drawn = g.action_count % 2 == 0
            tile = h.drawn_tile.tile if drawn else h.known_tiles[0]
            before = s.remaining_draws
            g.submit(Discard(s.actor, tile, drawn))
            self.assert_inventory(g)
            self.assertEqual(g.debug_state().remaining_draws, before)
        self.assertEqual(g.action_count, 280)
        self.assertEqual(g.debug_state(), g.replay(280).debug_state())
        self.assertEqual(g.replay(0).debug_state(), Game(33).debug_state())
        g.undo()
        self.assertEqual(g.debug_state().remaining_draws, 0)
        self.assertEqual(g.debug_state().phase, Phase.RESPONSE)

    def test_failed_commands_and_pause_are_atomic(self):
        g = Game(2)
        h = g.debug_state().players[0].hand
        absent = next(t for t in make_tiles() if t not in h.known_tiles)
        for action in (Draw(0, h.drawn_tile.tile), Discard(1, h.drawn_tile.tile, True),
                       Discard(0, absent, False), Discard(0, h.drawn_tile.tile, None)):
            before = g.debug_state()
            with self.assertRaises(RecordError):
                g.submit(action)
            self.assertEqual(before, g.debug_state())
            self.assertTrue(g.paused)
            self.assertEqual(g.action_count, 0)
            g.resume()
        g.stop()
        with self.assertRaises(RecordError):
            g.submit(Discard(0, h.drawn_tile.tile, True))
        g.resume()
        g.submit(Discard(0, h.drawn_tile.tile, True))
        self.assertEqual(g.action_count, 1)

    def test_observation_hides_state_events_seed_and_aliases(self):
        g = Game(9)
        wall = g.debug_state().wall
        # Swap hidden opponent tiles and future draws, leaving player 0/public tiles unchanged.
        wall[1], wall[2] = wall[2], wall[1]
        wall[60], wall[61] = wall[61], wall[60]
        altered = Game.from_wall(wall)
        self.assertEqual(g.observe(0), altered.observe(0))
        for seat in range(4):
            view = g.observe(seat)
            self.assertNotIn("seed", view)
            self.assertNotIn("remaining_wall", view)
            for p in view["players"]:
                if p["seat"] != seat:
                    self.assertEqual(p["hand"]["known_tiles"], [])
                    if p["hand"]["drawn_tile"]:
                        self.assertIsNone(p["hand"]["drawn_tile"]["tile"])
            self.assertEqual(view["events"][0]["tile"],
                             asdict(g.debug_state().players[0].hand.drawn_tile.tile) if seat == 0 else None)
        view = g.observe(0)
        view["players"][0]["hand"]["known_tiles"].clear()
        state = g.debug_state()
        state.wall.clear()
        self.assertEqual(len(g.observe(0)["players"][0]["hand"]["known_tiles"]), 13)
        self.assertEqual(len(g.debug_state().wall), 136)

    def test_checkpoint_replay_and_bad_inventory(self):
        g = Game(-123, dealer=3)
        s = g.debug_state()
        g.submit(Discard(3, s.players[3].hand.drawn_tile.tile, True))
        g.stop()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "game.json"
            g.save(path)
            loaded = Game.load(path)
            self.assertEqual(loaded.debug_view(), g.debug_view())
            self.assertTrue(loaded.paused)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["initial_wall"][0] = data["initial_wall"][1]
            # Guarantee invalid size independent of whether those two tiles happened to match.
            data["initial_wall"].pop()
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaises(RecordError):
                Game.load(path)
