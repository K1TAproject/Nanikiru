from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Game, Decision, Discard, Tile, RecordError, Rules
from nanikiru.bot import choose_action, choose_yaku_action
from nanikiru.decision import STRATEGY_VERSION, analyze_decision, project_call, yaku_paths
from test_rules import fixture, discard_draw, pass_all, tiles

PON_HAND = "5z 5z 2m 3m 4m 3p 4p 5p 6s 7s 9m 1z 9p"


class DecisionTests(unittest.TestCase):
    def test_yaku_bots_finish_single_round(self):
        g = Game(14)
        for _ in range(400):
            view = g.observe(0)
            if view["result"]:
                break
            actor = view["actor"]
            legal = g.legal_actions(actor)
            action = choose_yaku_action(g.observe(actor), legal)
            self.assertIn(action, legal)
            g.submit(action, policy=STRATEGY_VERSION)
        self.assertIsNotNone(g.observe(0)["result"])
        self.assertFalse(g.paused)
        self.assertTrue(all(r["policy"] == STRATEGY_VERSION for r in g.debug_decisions()))

    def test_fixed_discard_comparison_keeps_red_only_when_enabled(self):
        g = Game(14)
        v, legal = g.observe(0), g.legal_actions(0)
        self.assertEqual(str(choose_action(v, legal).tile), "0m")
        self.assertEqual(str(choose_yaku_action(v, legal).tile), "2z")
        v["rules"]["aka_dora_enabled"] = False
        self.assertEqual(choose_yaku_action(v, legal), choose_action(v, legal))

    def test_winds_pairs_triplets_and_unseen_path(self):
        g = fixture({0: "1z 1z 1z 2z 2z 5z 5z 3z 4z 1m 2m 3m 9p"}, "9s")
        v = g.observe(0)
        profile = yaku_paths(v)
        east = next(h for h in profile["yakuhai"] if h["tile"] == "1z")
        self.assertEqual((east["status"], east["role_count"]), ("condition_met", 2))
        white = next(h for h in profile["yakuhai"] if h["tile"] == "5z")
        self.assertEqual(white["status"], "potential")
        self.assertNotIn("2z", [h["tile"] for h in profile["yakuhai"]])
        v["round_wind"] = "south"
        self.assertEqual(next(h for h in yaku_paths(v)["yakuhai"] if h["tile"] == "2z")["status"], "potential")
        v["players"][1]["discards"] = [{"tile": asdict(Tile.parse("5z")), "claimed_by": None}] * 2
        self.assertEqual(next(h for h in yaku_paths(v)["yakuhai"] if h["tile"] == "5z")["status"], "unavailable_now")
        for wind in ("east", "south", "west", "north"):
            v["round_wind"] = wind
            self.assertTrue(any("场风" in h["roles"] for h in yaku_paths(v)["yakuhai"]))

    def test_bonus_is_not_yaku_and_rule_switches(self):
        hand = "0m 2m 3m 4m 6m 7m 8m 1p 2p 3p 7s 8s 9s"
        g = fixture({1: hand}, "1z", slots={130: Tile.parse("4m")}, rules=Rules(False, False))
        p = yaku_paths(g.observe(1))
        self.assertEqual((p["dora_tiles"], p["red_tiles"], p["aka_bonus_tiles"]), (1, 1, 0))
        self.assertFalse(p["has_condition"])
        v = g.observe(1)
        v["rules"]["aka_dora_enabled"] = True
        self.assertEqual(yaku_paths(v)["aka_bonus_tiles"], 1)
        self.assertEqual(yaku_paths(v)["conditions"], p["conditions"])
        self.assertNotIn("han", p)
        self.assertNotIn("points", p)

    def test_pon_accepts_yakuhai_but_not_guest_wind(self):
        for honor, expected in (("5z", "pon"), ("3z", "pass")):
            g = fixture({1: PON_HAND.replace("5z", honor)}, honor)
            discard_draw(g)
            view, legal = g.observe(1), g.legal_actions(1)
            before = g.debug_view()
            result = analyze_decision(view, legal)
            self.assertEqual(choose_action(view, legal).kind, "pass")
            self.assertEqual(choose_yaku_action(view, legal).kind, expected)
            call = next(c for c in result["candidates"] if c["action"]["kind"] == "pon")
            self.assertLess(call["shanten"], result["candidates"][0]["shanten"])
            self.assertEqual(call["accepted"], expected == "pon")
            self.assertIn("立直", call["lost_closed_paths"])
            self.assertEqual(g.debug_view(), before)

    def test_call_projection_matches_core_kuikae_and_red(self):
        g = fixture({1: "0m 5m 6m 7m 8m 2p 3p 4p 6s 7s 8s 2z 3z"}, "5m")
        discard_draw(g)
        before = g.debug_view()
        actions = [a for a in g.legal_actions(1) if isinstance(a, Decision) and a.kind in ("chi", "pon")]
        self.assertTrue(any(any(t.is_red for t in a.tiles) for a in actions))
        for action in actions:
            projected, legal = project_call(g.observe(1), action)
            trial = g.replay(g.action_count)
            trial.submit(action)
            pass_all(trial)
            actual = [a for a in trial.legal_actions(1) if isinstance(a, Discard)]
            self.assertEqual(set(legal), set(actual))
            self.assertEqual(projected["players"][1]["hand"], trial.observe(1)["players"][1]["hand"])
            from nanikiru.efficiency import analyze_discards
            self.assertEqual(analyze_discards(projected, legal), analyze_discards(trial.observe(1), actual))
        self.assertEqual(g.debug_view(), before)

    def test_open_tanyao_and_closed_kan_paths(self):
        g = fixture({1: "2m 3m 3p 4p 5p 6s 7s 8s 4m 4m 6p 7p 8p"}, "4m")
        discard_draw(g)
        action = next(a for a in g.legal_actions(1) if isinstance(a, Decision) and a.kind == "chi")
        result = analyze_decision(g.observe(1), g.legal_actions(1))
        chi = next(c for c in result["candidates"] if c["action"]["kind"] == "chi")
        self.assertEqual(chi["shanten"], result["candidates"][0]["shanten"])
        self.assertFalse(chi["accepted"])  # Same shanten is not enough under this conservative policy.
        view, legal = project_call(g.observe(1), action)
        p = yaku_paths(view)
        self.assertFalse(p["closed"])
        self.assertEqual(p["tanyao"], "condition_met")
        self.assertEqual(p["riichi"], "unavailable_now")
        view["players"][1]["melds"] = [{"kind": "closed_kan", "tiles": [asdict(t) for t in tiles("1z 1z 1z 1z")]}]
        p = yaku_paths(view)
        self.assertTrue(p["closed"])
        self.assertEqual(p["riichi"], "potential")
        self.assertEqual(p["tanyao"], "blocked_by_meld")

    def test_hidden_information_and_legal_wins(self):
        g = Game(4)
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[60], wall[61] = wall[61], wall[60]
        other = Game.from_wall(wall)
        self.assertEqual(analyze_decision(g.observe(0), g.legal_actions(0)),
                         analyze_decision(other.observe(0), other.legal_actions(0)))
        with self.assertRaises(ValueError):
            analyze_decision(g.debug_view(), g.legal_actions(0))
        from test_rules import WAIT
        g = fixture({0: WAIT}, "6s")
        self.assertEqual(choose_yaku_action(g.observe(0), g.legal_actions(0)).kind, "tsumo")

    def test_records_branch_v4_and_old_saves(self):
        g = fixture({1: PON_HAND}, "5z")
        discard_draw(g)
        view = g.observe(1)
        action = choose_yaku_action(view, g.legal_actions(1))
        g.submit(action, policy=STRATEGY_VERSION)
        record = g.decision_records(1)[0]
        self.assertEqual(record["observation"], view)
        self.assertEqual(record["policy"], STRATEGY_VERSION)
        self.assertEqual(len(g.debug_reviews()), 1)  # Calls do not enter basic discard statistics.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "game.json"
            g.save(path)
            self.assertEqual(Game.load(path).debug_decisions(), g.debug_decisions())
            data = json.loads(path.read_text(encoding="utf-8"))
            bad = deepcopy(data)
            bad["decision_records"][0]["analysis"]["version"] = "unknown"
            path.write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(RecordError):
                Game.load(path)
            data["version"] = 3
            del data["decision_records"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Game.load(path).debug_reviews(), g.debug_reviews())
        self.assertEqual(g.replay(1).decision_records(1), [])
        g.undo()
        self.assertEqual(g.decision_records(1), [])
        g.submit(Decision(1, "pass"))
        self.assertEqual(g.decision_records(1)[0]["actual_action"]["kind"], "pass")
