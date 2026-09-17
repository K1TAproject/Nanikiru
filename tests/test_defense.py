from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Game, Discard, Decision, Tile
from nanikiru.bot import choose_yaku_action
from nanikiru.decision import analyze_decision
from nanikiru.defense import analyze_defense, analyze_safety
from test_rules import fixture, WAIT, discard_draw, pass_all


def river(view, seat, *codes):
    view["players"][seat]["riichi"] = True
    view["players"][seat]["discards"] = [
        {"id": f"{seat}:{i}", "tile": asdict(Tile.parse(t)), "claimed_by": None} for i, t in enumerate(codes)]


class DefenseTests(unittest.TestCase):
    def test_attack_unchanged_and_fold_can_increase_shanten(self):
        g = Game(4)
        view, legal = g.observe(0), g.legal_actions(0)
        river(view, 1, "6s")
        old = analyze_decision(view, legal)
        attack = analyze_defense(view, legal, "attack")
        fold = analyze_defense(view, legal, "fold")
        self.assertEqual(attack["recommended"], old["recommended"])
        self.assertEqual(str(Tile(**fold["recommended"]["tile"])), "6s")
        self.assertNotEqual(fold["recommended"], attack["recommended"])
        # Fixed example: 6s is 4-shanten, original first choice 1z is 3-shanten.
        candidates = {str(Tile(**c["action"]["tile"])): c for c in old["candidates"]}
        self.assertEqual((candidates["6s"]["shanten"], candidates["1z"]["shanten"]), (4, 3))
        self.assertTrue(fold["candidates"][0]["covers_all_threats"])
        self.assertEqual(fold["candidates"][0]["opponents"][1]["status"], "unknown")

    def test_multi_threat_coverage_unknown_and_fallback(self):
        g = Game(4)
        view, legal = g.observe(0), g.legal_actions(0)
        original = analyze_decision(view, legal)["recommended"]
        self.assertEqual(analyze_defense(view, legal, "fold")["recommended"], original)
        river(view, 1, "6s", "1z")
        river(view, 2, "6s")
        r = analyze_defense(view, legal, "fold")
        self.assertEqual(r["candidates"][0]["covered_threats"], [1, 2])
        river(view, 3, "2z")
        r = analyze_defense(view, legal, "fold")
        self.assertFalse(r["candidates"][0]["covers_all_threats"])
        self.assertEqual(r["candidates"][0]["covered_threats"], [1, 2])
        self.assertIn("未覆盖", r["reason"])
        for seat in (1, 2, 3):
            river(view, seat, "2z")
        r = analyze_defense(view, legal, "fold")
        self.assertEqual(r["recommended"], analyze_decision(view, legal)["recommended"])
        self.assertIn("没有可确认的安全选择", r["reason"])

    def test_red_fives_and_claimed_river_still_count(self):
        g = Game(14)
        view, legal = g.observe(0), g.legal_actions(0)
        river(view, 1, "5m")
        view["players"][1]["discards"][0]["claimed_by"] = 2
        r = analyze_safety(view, legal)
        red = next(c for c in r["candidates"] if c["action"]["tile"]["is_red"] and c["action"]["tile"]["suit"] == "m")
        self.assertEqual(red["covered_threats"], [1])
        self.assertEqual(red["opponents"][0]["discard_ids"], ["1:0"])
        view["players"][1]["riichi"] = False
        self.assertEqual(analyze_safety(view, legal)["threats"], [])

    def test_fold_passes_calls_accepts_win_and_riichi_forced_discard(self):
        from test_decision import PON_HAND
        g = fixture({1: PON_HAND}, "5z")
        discard_draw(g)
        self.assertEqual(choose_yaku_action(g.observe(1), g.legal_actions(1), "attack").kind, "pon")
        self.assertEqual(choose_yaku_action(g.observe(1), g.legal_actions(1), "fold").kind, "pass")
        g = fixture({0: WAIT}, "6s")
        self.assertEqual(choose_yaku_action(g.observe(0), g.legal_actions(0), "fold").kind, "tsumo")
        g = fixture({1: WAIT}, "6s")
        discard_draw(g)
        self.assertEqual(choose_yaku_action(g.observe(1), g.legal_actions(1), "fold").kind, "ron")
        g = fixture({0: WAIT}, "9m 9p 9s 8m 8p")
        g.submit(Decision(0, "riichi", (Tile.parse("9m"),), True))
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        action = choose_yaku_action(g.observe(0), g.legal_actions(0), "fold")
        self.assertEqual(action, Discard(0, Tile.parse("8p"), True))

    def test_hidden_independence_and_record_modes(self):
        g = Game(4)
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[60], wall[61] = wall[61], wall[60]
        other = Game.from_wall(wall)
        self.assertEqual(analyze_defense(g.observe(0), g.legal_actions(0), "fold"),
                         analyze_defense(other.observe(0), other.legal_actions(0), "fold"))
        with self.assertRaises(ValueError):
            analyze_defense(g.debug_view(), g.legal_actions(0))
        g.submit(choose_yaku_action(g.observe(0), g.legal_actions(0), "fold"), mode="fold")
        first = deepcopy(g.debug_decisions())
        g.submit(Decision(1, "pass"), mode="attack")
        self.assertEqual(g.debug_decisions()[0], first[0])
        self.assertEqual(g.debug_decisions()[1]["defense"]["mode"], "attack")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "defense.json"
            g.save(path)
            self.assertEqual(Game.load(path).debug_decisions(), g.debug_decisions())
            old = json.loads(path.read_text(encoding="utf-8"))
            old["version"] = 4
            for r in old["decision_records"]:
                del r["defense"]
            path.write_text(json.dumps(old), encoding="utf-8")
            restored = Game.load(path)
            self.assertEqual(restored.debug_decisions(), old["decision_records"])
            restored.save(path)
            self.assertEqual(Game.load(path).debug_decisions(), old["decision_records"])
        self.assertEqual(g.replay(1).debug_decisions(), first)
        g.rewind(0)
        self.assertEqual(g.debug_decisions(), [])
