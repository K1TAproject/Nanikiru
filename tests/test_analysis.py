from copy import deepcopy
from dataclasses import replace
import unittest

from examples.inspect_position import sample_position
from nanikiru import UNKNOWN, TileRecord
from nanikiru.analysis import analyze_shanten, analyze_waits, analyze_discard_shapes, score_hand
from nanikiru.analysis.efficiency import calculate_shanten


class AnalysisTests(unittest.TestCase):
    def ready(self):
        view = sample_position()
        return replace(view, tiles=view.tiles[:-1], draw_context=None, phase="await_draw")

    def test_shanten_thirteen_fourteen_and_complete(self):
        ready = self.ready()
        self.assertEqual(analyze_shanten(ready).value, 0)
        complete = replace(ready, tiles=(*ready.tiles, TileRecord("win", "6s", "hand", 0)))
        self.assertEqual(analyze_shanten(complete).value, -1)
        self.assertEqual(analyze_shanten(sample_position()).status, "available")

    def test_shanten_specials_and_open_hand(self):
        for hand in ("1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z",
                     "1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z"):
            self.assertEqual(calculate_shanten(hand.split()), 0)
        hand = "1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split()
        for kind, codes in (("chi", ("7m", "8m", "9m")), ("closed_kan", ("9m",) * 4)):
            self.assertEqual(calculate_shanten(hand, ((kind, codes),)), 0)
        self.assertIs(calculate_shanten((UNKNOWN,)), UNKNOWN)
        with self.assertRaises(ValueError):
            calculate_shanten(("1m",))

    def test_unknown_hand_or_group_is_unavailable(self):
        for key in ("hand:0", "groups:0"):
            view = sample_position()
            view.completeness[key] = False
            self.assertEqual(analyze_shanten(view).status, "unavailable")
            self.assertEqual(analyze_waits(view).status, "unavailable")
            self.assertEqual(analyze_discard_shapes(view, ("t13",))[0].metrics["shanten"].status, "unavailable")

    def test_waits_known_unknown_and_fourteen_not_applicable(self):
        view = self.ready()
        result = analyze_waits(view)
        self.assertEqual(result.value, [{"tile": "3s", "unseen": 3}, {"tile": "6s", "unseen": 4}])
        view.completeness["public_tiles"] = False
        result = analyze_waits(view)
        self.assertEqual(result.status, "available")
        self.assertTrue(all(t["unseen"] is UNKNOWN for t in result.value))
        self.assertFalse(result.details["unseen_complete"])
        self.assertEqual(analyze_waits(sample_position()).status, "not_applicable")

    def test_publicly_exhausted_wait_remains_visible(self):
        view = self.ready()
        extra = tuple(TileRecord(f"r{i}", "6s", "river", 1) for i in range(4))
        result = analyze_waits(replace(view, tiles=(*view.tiles, *extra)))
        self.assertIn({"tile": "6s", "unseen": 0}, result.value)

    def test_discard_metrics_and_nonmutation(self):
        view = sample_position()
        before = deepcopy(view)
        result = analyze_discard_shapes(view, ("t13",))[0]
        self.assertEqual(result.legality, "unknown")
        self.assertEqual(result.action.tile_ids, ("t13",))
        self.assertEqual(result.metrics["shanten"].value, 0)
        self.assertEqual(result.metrics["ukeire"].value, 7)
        self.assertEqual(view, before)
        view.completeness["public_tiles"] = False
        result = analyze_discard_shapes(view, ("t13",))[0]
        self.assertEqual(result.metrics["shanten"].status, "available")
        self.assertEqual(result.metrics["ukeire"].status, "unavailable")

    def test_conditional_score_and_explicit_assumptions(self):
        hand = "2m 3m 4m 2p 3p 4p 2s 3s 4s 6s 7s 0p 5p".split()
        before = list(hand)
        result = score_hand(hand, (), "8s", seat=1, dealer=0, round_wind="east",
                            tsumo=False, riichi=False, indicators=())
        self.assertEqual(result.status, "conditional")
        self.assertEqual(result.basis, "exact")
        self.assertEqual(result.value["score"]["cost"]["main"], 8000)
        self.assertFalse(result.details["win_permission_checked"])
        self.assertFalse(result.details["conditions"]["is_ippatsu"])
        self.assertEqual(hand, before)

    def test_score_no_yaku_and_not_complete_are_different(self):
        hand = "1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 5s 5s".split()
        opts = dict(seat=1, dealer=1, round_wind="west", riichi=False, indicators=())
        ron = score_hand(hand, (), "2z", tsumo=False, **opts)
        self.assertEqual(ron.status, "conditional")
        self.assertEqual(ron.value, {"has_yaku": False, "score": None})
        tsumo = score_hand(hand, (), "2z", tsumo=True, **opts)
        self.assertTrue(tsumo.value["has_yaku"])
        incomplete = score_hand(hand, (), "9m", tsumo=False, **opts)
        self.assertEqual(incomplete.status, "not_applicable")

    def test_score_unknown_fields_are_not_defaulted(self):
        hand = "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split()
        opts = dict(seat=0, dealer=0, round_wind="east", tsumo=False, riichi=True)
        result = score_hand(hand, (), "6s", **opts)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(set(result.details["missing_fields"]), {"indicators", "ura"})
        result = score_hand(hand, (), "6s", indicators=(), ura=(), is_ippatsu=UNKNOWN, **opts)
        self.assertEqual(result.details["missing_fields"], ["is_ippatsu"])
        result = score_hand(hand, (), "6s", indicators=(), ura=(UNKNOWN,), **opts)
        self.assertEqual(result.details["missing_fields"], ["ura"])

    def test_conflicting_scoring_conditions_are_errors(self):
        hand = "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split()
        opts = dict(seat=0, dealer=0, round_wind="east", tsumo=False, riichi=False, indicators=())
        for flags in ({"is_rinshan": True}, {"is_ippatsu": True}, {"is_daburu_riichi": True}, {"invented": True}):
            with self.subTest(flags=flags), self.assertRaises(ValueError):
                score_hand(hand, (), "6s", **opts, **flags)


if __name__ == "__main__":
    unittest.main()
