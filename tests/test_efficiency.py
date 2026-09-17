from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Decision, Discard, Game, RecordError, Tile
from nanikiru.efficiency import ANALYSIS_VERSION, analyze_discards, compare_discard, summarize_reviews, visible_counts
from test_rules import fixture, tiles, discard_draw, pass_all, WAIT


def game_with(hand=WAIT, draw="9m"):
    return fixture({0: hand}, draw, slots={130: Tile.parse("8p")})


def analysis_for(g, seat=0):
    return analyze_discards(g.observe(seat), [a for a in g.legal_actions(seat) if isinstance(a, Discard)])


def candidate(result, code, drawn=None):
    return next(c for c in result["candidates"] if str(Tile(**c["discard"]["tile"])) == code
                and (drawn is None or c["discard"]["is_tsumogiri"] == drawn))


class EfficiencyTests(unittest.TestCase):
    def test_same_shanten_loss(self):
        r = analysis_for(Game(4))
        comparison = compare_discard(r, Discard(0, Tile.parse("1m"), False))
        self.assertEqual((comparison["selected"]["shanten"], comparison["selected"]["ukeire"]), (3, 43))
        self.assertEqual(comparison["ukeire_loss"], 8)
        stats = summarize_reviews([{"comparison": comparison}])
        self.assertEqual((stats["optimal_rate"], stats["ukeire_loss_mean"]), (0, 8))

    def test_regular_shanten_ukeire_and_input_unchanged(self):
        g = game_with()
        view = g.observe(0)
        before = deepcopy(view)
        result = analyze_discards(view, [a for a in g.legal_actions(0) if isinstance(a, Discard)])
        c = candidate(result, "9m")
        self.assertEqual((c["shanten"], c["ukeire"], c["is_best"]), (0, 7, True))
        self.assertEqual(c["effective_tiles"], [{"tile": "3s", "unseen": 3}, {"tile": "6s", "unseen": 4}])
        self.assertEqual(result["version"], ANALYSIS_VERSION)
        self.assertEqual(view, before)
        ranks = [(c["shanten"], -c["ukeire"]) for c in result["candidates"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_special_hands(self):
        g = game_with("1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z", "9s")
        c = candidate(analysis_for(g), "9s")
        self.assertEqual((c["shanten"], c["ukeire"]), (0, 3))
        self.assertEqual(c["effective_tiles"], [{"tile": "1z", "unseen": 3}])
        g = game_with("1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z", "2m")
        c = candidate(analysis_for(g), "2m")
        self.assertEqual((c["shanten"], c["ukeire"], len(c["effective_tiles"])), (0, 39, 13))

    def test_open_and_closed_kan_hand_sizes(self):
        for kind, meld_tiles in (("chi", "7m 8m 9m"), ("closed_kan", "9m 9m 9m 9m")):
            view = game_with().observe(0)
            p = view["players"][0]
            p["hand"]["known_tiles"] = [asdict(t) for t in tiles("1p 2p 3p 1s 2s 3s 7z 7z 4s 5s")]
            p["hand"]["drawn_tile"] = {"tile": asdict(Tile.parse("9p"))}
            p["melds"] = [{"kind": kind, "tiles": [asdict(t) for t in tiles(meld_tiles)], "source_discard_id": None}]
            r = analyze_discards(view, [Discard(0, Tile.parse("9p"), True)])
            self.assertEqual((r["best_shanten"], r["best_ukeire"]), (0, 7))

    def test_visible_counts_claimed_red_kan_and_indicator(self):
        view = game_with().observe(0)
        view["players"][1]["melds"] = [{"kind": "pon", "tiles": [asdict(t) for t in tiles("0m 5m 5m")], "source_discard_id": "d1"}]
        view["players"][0]["discards"] = [{"id": "d1", "tile": asdict(Tile.parse("0m")), "claimed_by": 1}]
        view["players"][2]["discards"] = [{"id": "d2", "tile": asdict(Tile.parse("5m")), "claimed_by": None}]
        view["players"][3]["melds"] = [{"kind": "closed_kan", "tiles": [asdict(t) for t in tiles("8m 8m 8m 8m")], "source_discard_id": None}]
        counts = visible_counts(view)
        self.assertEqual(counts[4], 4)
        self.assertEqual(counts[7], 4)
        self.assertEqual(counts[16], 1)  # public 8p indicator
        self.assertEqual(counts[8], 1)  # current 9m draw
        r = analyze_discards(view, [Discard(0, Tile.parse("9m"), True)])
        self.assertEqual(r["visible_counts"], counts)

    def test_discard_never_returns_to_unseen_pool(self):
        g = Game(4)
        tile = g.debug_state().players[0].hand.drawn_tile.tile
        r = analysis_for(g)
        c = candidate(r, str(tile), True)
        # This hand can improve by drawing back the discarded 6s, but only 3 unseen copies remain.
        self.assertIn({"tile": "6s", "unseen": 3}, c["effective_tiles"])
        view = g.observe(0)
        view["players"][1]["discards"] = [{"tile": asdict(Tile.parse("6s")), "claimed_by": None}] * 3
        c = candidate(analyze_discards(view, [Discard(0, tile, True)]), "6s")
        self.assertNotIn("6s", [t["tile"] for t in c["effective_tiles"]])

    def test_red_fives_tie_and_duplicate_sources_are_one_choice(self):
        g = game_with("2m 3m 4m 2p 3p 4p 2s 3s 4s 6s 7s 0p 5p", "8s")
        r = analyze_discards(g.observe(0), [Discard(0, Tile.parse(t), False) for t in ("0p", "5p")])
        a, b = r["candidates"]
        self.assertEqual((a["shanten"], a["ukeire"]), (b["shanten"], b["ukeire"]))
        self.assertTrue(a["is_best"] and b["is_best"])
        g = game_with(draw="7z")
        actions = [Discard(0, Tile.parse("7z"), drawn) for drawn in (False, True)]
        r = analyze_discards(g.observe(0), actions)
        self.assertEqual(r["choice_count"], 1)
        self.assertEqual(len(r["candidates"]), 2)
        self.assertTrue(compare_discard(r, actions[0])["forced"])

    def test_hidden_wall_independence_and_omniscient_rejected(self):
        g = Game(9)
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[60], wall[61] = wall[61], wall[60]
        other = Game.from_wall(wall)
        self.assertEqual(analysis_for(g), analysis_for(other))
        legal = [a for a in g.legal_actions(0) if isinstance(a, Discard)]
        with self.assertRaises(ValueError):
            analyze_discards(g.debug_view(), legal)
        leaked = g.observe(0)
        leaked["players"][1]["hand"]["known_tiles"] = [asdict(Tile.parse("1m"))]
        with self.assertRaises(ValueError):
            analyze_discards(leaked, legal)

    def test_summary_denominators_and_shanten_priority(self):
        g = Game(4)
        r = analysis_for(g)
        c = compare_discard(r, Discard(0, Tile.parse("6s"), True))
        self.assertEqual(c["shanten_increase"], 1)
        self.assertIsNone(c["ukeire_loss"])
        self.assertGreater(c["selected"]["ukeire"], r["best_ukeire"])  # more tiles cannot override worse shanten
        best = compare_discard(r, Discard(**{**r["candidates"][0]["discard"], "tile": Tile(**r["candidates"][0]["discard"]["tile"])}))
        forced = deepcopy(best)
        forced["forced"] = True
        stats = summarize_reviews([{"comparison": value} for value in (c, best, forced)])
        self.assertEqual((stats["discards"], stats["choices"], stats["forced"]), (3, 2, 1))
        self.assertEqual(stats["optimal_rate"], .5)
        self.assertEqual(stats["shanten_increase_count"], 1)
        self.assertEqual((stats["same_shanten_count"], stats["ukeire_loss_total"]), (1, 0))
        self.assertIsNone(summarize_reviews([])["optimal_rate"])
        self.assertIsNone(summarize_reviews([{"comparison": forced}])["optimal_rate"])


class ReviewRecordTests(unittest.TestCase):
    def test_commit_replay_undo_branch_and_defensive_copy(self):
        g = Game(4)
        before = g.observe(0)
        discard_draw(g)
        records = g.review_records(0)
        self.assertEqual(records[0]["observation"], before)
        self.assertEqual(records[0]["action_index"], 1)
        self.assertEqual(g.review_records(1), [])
        pass_all(g)
        discard_draw(g)
        self.assertEqual(len(g.debug_reviews()), 2)
        self.assertEqual(g.replay(1).debug_reviews(), records)
        self.assertEqual(g.replay(0).debug_reviews(), [])
        records[0]["observation"].clear()
        self.assertTrue(g.review_records(0)[0]["observation"])
        g.rewind(0)
        self.assertEqual(g.debug_reviews(), [])
        best = next(a for a in g.legal_actions(0) if isinstance(a, Discard) and not a.is_tsumogiri)
        g.submit(best)
        self.assertFalse(g.review_records(0)[0]["comparison"]["selected"]["discard"]["is_tsumogiri"])
        before = g.debug_reviews()
        with self.assertRaises(RecordError):
            g.submit(Discard(0, best.tile, False))
        self.assertEqual(g.debug_reviews(), before)
        g.undo()
        self.assertTrue(g.paused)
        self.assertEqual(g.debug_reviews(), [])

    def test_riichi_and_forced_discard(self):
        g = fixture({0: WAIT}, "9m 9p 9s 8m 8p")
        g.submit(Decision(0, "riichi", (Tile.parse("9m"),), True))
        self.assertEqual(g.review_records(0)[0]["actual_action"]["kind"], "riichi")
        pass_all(g)
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        discard_draw(g)
        records = g.review_records(0)
        self.assertEqual(len(records), 2)
        self.assertTrue(records[-1]["comparison"]["forced"])
        self.assertEqual(summarize_reviews(records)["choices"], 1)

    def test_v3_load_validation_and_v2_migration(self):
        g = Game(4)
        discard_draw(g)
        g.stop()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "review.json"
            g.save(path)
            loaded = Game.load(path)
            self.assertEqual(g.debug_reviews(), loaded.debug_reviews())
            self.assertTrue(loaded.paused)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["version"], 5)
            for key in ("score", "version", "observation", "missing"):
                bad = deepcopy(data)
                if key == "score":
                    bad["reviews"][0]["analysis"]["best_ukeire"] += 1
                elif key == "version":
                    bad["reviews"][0]["analysis"]["version"] = "future-version"
                elif key == "observation":
                    bad["reviews"][0]["observation"]["seed"] = 4
                else:
                    bad["reviews"] = []
                path.write_text(json.dumps(bad), encoding="utf-8")
                with self.assertRaises(RecordError):
                    Game.load(path)
            data["version"] = 2
            del data["reviews"]
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertEqual(Game.load(path).debug_reviews(), g.debug_reviews())
