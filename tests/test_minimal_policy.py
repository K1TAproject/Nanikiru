from copy import deepcopy
import random
import unittest

from nanikiru.analysis import analyze_discard_shapes
from nanikiru.contracts import Action, CandidateResult, Metric
from nanikiru.analysis.minimal_policy import rank_discard_candidates
from nanikiru.tiles import counts34, index
from test_ui.positions import make_position, random_position, replace_tile, sorted_hand


def candidate(tile_id, shanten, ukeire, legality="unknown"):
    return CandidateResult(Action("discard", 0, (tile_id,)), legality, {
        name: Metric("unavailable") if value is None else Metric("available", value, "exact")
        for name, value in (("shanten", shanten), ("ukeire", ukeire))
    })


class MinimalPolicyTests(unittest.TestCase):
    def test_shanten_first_ukeire_second_and_ties(self):
        candidates = (candidate("a", 1, 50), candidate("b", 0, 3),
                      candidate("c", 0, 7), candidate("d", 0, 7))
        before = deepcopy(candidates)
        result = rank_discard_candidates(candidates)
        self.assertEqual([[a.tile_ids[0] for a in tier] for tier in result.value], [["c", "d"], ["b"], ["a"]])
        self.assertEqual(candidates, before)
        self.assertEqual(result.basis, "heuristic")

    def test_missing_ukeire_compares_shanten_only(self):
        result = rank_discard_candidates((candidate("a", 0, 0), candidate("b", 0, None), candidate("c", 1, 99)))
        self.assertEqual(result.status, "conditional")
        self.assertEqual(len(result.value[0]), 2)
        self.assertEqual(result.details["criteria"], ("shanten",))

    def test_missing_shape_and_forbidden_candidates(self):
        self.assertEqual(rank_discard_candidates((candidate("a", None, None),)).status, "unavailable")
        allowed = candidate("a", 1, 8, "allowed")
        result = rank_discard_candidates((candidate("b", 0, 12, "forbidden"), allowed))
        self.assertEqual(result.value, ((allowed.action,),))
        self.assertEqual(rank_discard_candidates(()).status, "not_applicable")
        with self.assertRaises(ValueError):
            rank_discard_candidates((allowed, allowed))

    def test_indicator_inventory_and_draw_identity(self):
        position = make_position("1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s")
        rows = analyze_discard_shapes(position, [t.id for t in position.tiles if t.zone == "hand"])
        best = rank_discard_candidates(rows).value[0]
        self.assertEqual([a.tile_ids for a in best], [("draw",)])
        row = next(c for c in rows if c.action.tile_ids == ("draw",))
        self.assertEqual(row.metrics["shanten"].value, 0)
        self.assertEqual(row.metrics["ukeire"].value, 6)

    def test_random_positions_inventory_sorted_hand_and_no_hidden_wall(self):
        rng = random.Random(420)
        for _ in range(30):
            position = random_position(rng)
            self.assertEqual(len(position.tiles), 15)
            counts34(t.code for t in position.tiles)
            hand = sorted_hand(position)
            self.assertEqual(len(hand), 13)
            self.assertEqual([index(t.code) for t in hand], sorted(index(t.code) for t in hand))
            self.assertNotIn("draw", [t.id for t in hand])
            self.assertTrue(all(t.seat in (0, None) for t in position.tiles))

    def test_manual_edit_keeps_identity_and_rejects_invalid_inventory(self):
        original = make_position("1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s")
        before = deepcopy(original)
        edited = replace_tile(original, "h0", "0p")
        self.assertEqual(next(t.code for t in edited.tiles if t.id == "h0"), "0p")
        self.assertEqual(original, before)
        self.assertEqual(edited.draw_context, original.draw_context)
        with self.assertRaises(ValueError):
            replace_tile(edited, "draw", "0p")
        with self.assertRaises(ValueError):
            replace_tile(edited, "missing", "1m")
        with self.assertRaises(ValueError):
            replace_tile(edited, "draw", "invalid")


if __name__ == "__main__":
    unittest.main()
