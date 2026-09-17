import tempfile
from pathlib import Path
import unittest

from nanikiru import Game, Decision, Discard
from nanikiru.bot import choose_action
from nanikiru.efficiency import analyze_discards, compare_discard
from test_rules import fixture, WAIT, discard_draw


class BotTests(unittest.TestCase):
    def test_best_choice_is_deterministic_and_hidden_independent(self):
        g = Game(4)
        view = g.observe(0)
        legal = g.legal_actions(0)
        action = choose_action(view, legal)
        result = analyze_discards(view, [a for a in legal if isinstance(a, Discard)])
        self.assertTrue(compare_discard(result, action)["selected"]["is_best"])
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[60], wall[61] = wall[61], wall[60]
        other = Game.from_wall(wall)
        self.assertEqual(action, choose_action(other.observe(0), other.legal_actions(0)))
        self.assertEqual(view, g.observe(0))

    def test_win_and_pass_policy(self):
        g = fixture({0: WAIT}, "6s")
        self.assertEqual(choose_action(g.observe(0), g.legal_actions(0)).kind, "tsumo")
        g = fixture({1: "1m 1m 1m"}, "1m")
        discard_draw(g)
        self.assertTrue(any(isinstance(a, Decision) and a.kind == "pon" for a in g.legal_actions(1)))
        self.assertEqual(choose_action(g.observe(1), g.legal_actions(1)), Decision(1, "pass"))
        g = fixture({1: WAIT}, "6s")
        discard_draw(g)
        self.assertEqual(choose_action(g.observe(1), g.legal_actions(1)), Decision(1, "ron"))

    def test_complete_round_and_checkpoint(self):
        g = Game(87)
        for _ in range(300):
            view = g.observe(0)
            if view["result"]:
                break
            actor = view["actor"]
            legal = g.legal_actions(actor)
            # Stand-in for the human: tsumogiri / pass, bots for the other three.
            if actor == 0:
                action = next((a for a in legal if isinstance(a, Discard) and a.is_tsumogiri), None)
                action = action or next((a for a in legal if isinstance(a, Decision) and a.kind == "pass"), legal[0])
            else:
                action = choose_action(g.observe(actor), legal)
            g.submit(action)
        self.assertIsNotNone(g.observe(0)["result"])
        self.assertFalse(g.paused)
        records = [r for r in g.debug_reviews() if r["player"] != 0]
        self.assertTrue(records)
        self.assertTrue(all(r["comparison"]["selected"]["is_best"] for r in records))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bots.json"
            g.save(path)
            loaded = Game.load(path)
            self.assertEqual(loaded.debug_state(), g.debug_state())
            self.assertEqual(loaded.debug_reviews(), g.debug_reviews())

