from copy import deepcopy
import unittest

from examples.inspect_position import sample_position
from nanikiru import UNKNOWN
from nanikiru.rules.call_rules import call_banned
from nanikiru.analysis.efficiency import analyze_discards, analyze_shape
from nanikiru.rules.scoring import evaluate, ron_restrictions, waits
from nanikiru.tiles import counts34, index, physical_capacity, tile34


WAIT = "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split()


class CalculationTests(unittest.TestCase):
    def test_tile_codes_and_capacity(self):
        self.assertEqual(index("0p"), index("5p"))
        self.assertEqual(counts34(["0p", "5p", "5p", "5p"])[13], 4)
        self.assertEqual((physical_capacity("0p"), physical_capacity("5p")), (1, 3))
        self.assertEqual([index(tile34(i)) for i in range(34)], list(range(34)))
        with self.assertRaises(ValueError):
            counts34(["5p"] * 4)

    def test_regular_shape_and_unchanged_input(self):
        view = sample_position()
        before = deepcopy(view)
        result = analyze_discards(view, ("t13",))[0]
        self.assertEqual((result["shanten"], result["ukeire"]), (0, 7))
        self.assertEqual(result["effective_tiles"], [{"tile": "3s", "unseen": 3}, {"tile": "6s", "unseen": 4}])
        self.assertEqual(view, before)

    def test_unknown_inventory_keeps_shanten_not_fake_ukeire(self):
        view = sample_position()
        view.completeness["public_tiles"] = False
        result = analyze_discards(view, ("t13",))[0]
        self.assertEqual(result["shanten"], 0)
        self.assertIs(result["ukeire"], UNKNOWN)
        self.assertTrue(all(t["unseen"] is UNKNOWN for t in result["effective_tiles"]))

    def test_unknown_hand_does_not_guess(self):
        view = sample_position()
        view.completeness["hand:0"] = False
        result = analyze_discards(view, ("t13",))[0]
        self.assertIs(result["shanten"], UNKNOWN)
        self.assertIs(result["ukeire"], UNKNOWN)

    def test_special_hands(self):
        pairs = "1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z".split()
        orphans = "1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z".split()
        self.assertEqual(waits(pairs), {index("1z")})
        self.assertEqual(len(waits(orphans)), 13)
        self.assertEqual(analyze_shape(pairs, seen=counts34(pairs))["ukeire"], 3)
        self.assertEqual(analyze_shape(orphans, seen=counts34(orphans))["ukeire"], 39)

    def test_meld_and_fourth_owned_copy_limit(self):
        # Complete concealed melds plus a single 1z, with a declared 1z pon.
        hand = "1m 2m 3m 1p 2p 3p 1s 2s 3s 1z".split()
        groups = (("pon", ("1z", "1z", "1z")),)
        self.assertEqual(waits(hand, groups), set())
        self.assertNotIn("1z", [t["tile"] for t in analyze_shape(hand, groups)["effective_tiles"]])

    def test_invalid_shape_and_visible_counts(self):
        for seen in ([0] * 34, [4] * 33, [5] * 34):
            with self.subTest(seen=seen), self.assertRaises(ValueError):
                analyze_shape(WAIT, seen=seen)
        with self.assertRaises(ValueError):
            analyze_shape(["1m"])
        with self.assertRaises(ValueError):
            analyze_discards(sample_position(), ("missing",))

    def test_furiten_unknown_and_known_evidence(self):
        waiting = waits(WAIT)
        self.assertEqual(ron_restrictions(waiting, ["3s"], False, False, discards_complete=False), ["discard_furiten"])
        self.assertEqual(ron_restrictions(waiting, [], UNKNOWN, False, discards_complete=False),
                         ["discard_furiten_unknown", "temporary_furiten_unknown"])
        self.assertEqual(ron_restrictions(waiting, [], False, False, discards_complete=True), [])
        with self.assertRaises(ValueError):
            ron_restrictions(waiting, [], None, False, discards_complete=True)

    def test_kuikae_codes(self):
        self.assertEqual(call_banned("chi", ("2m", "3m"), "1m"), {index("1m"), index("4m")})
        self.assertEqual(call_banned("chi", ("1m", "2m"), "3m"), {index("3m")})
        self.assertEqual(call_banned("pon", ("0p", "5p"), "5p"), {index("5p")})

    def test_known_scoring_red_and_dora(self):
        hand = "2m 3m 4m 2p 3p 4p 2s 3s 4s 6s 7s 0p 5p".split()
        opts = dict(seat=1, dealer=0, round_wind="east")
        red = evaluate(hand, (), "8s", **opts)
        plain = evaluate(hand, (), "8s", aka_dora_enabled=False, **opts)
        self.assertEqual((red["han"], plain["han"], plain["fu"]), (5, 4, 30))
        self.assertEqual((red["cost"]["main"], plain["cost"]["main"]), (8000, 7700))
        dora = evaluate(hand, (), "8s", indicators=("4p",), aka_dora_enabled=False, **opts)
        self.assertEqual(dora["han"], plain["han"] + 2)

    def test_open_closed_kan_scoring(self):
        hand = "2p 3p 4p 2s 3s 4s 6s 7s 5p 5p".split()
        for kind, expected in (("open_kan", (1, 40, 700, 400)), ("closed_kan", (2, 60, 2000, 1000))):
            with self.subTest(kind=kind):
                result = evaluate(hand, ((kind, ("1m",) * 4),), "8s", seat=1, dealer=0,
                                  round_wind="east", tsumo=True, is_rinshan=True)
                self.assertEqual((result["han"], result["fu"], result["cost"]["main"], result["cost"]["additional"]), expected)

    def test_special_scoring_and_no_yaku(self):
        opts = dict(seat=1, dealer=0, round_wind="east")
        hand = "1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z".split()
        result = evaluate(hand, (), "1z", **opts)
        self.assertEqual((result["han"], result["fu"], result["cost"]["main"]), (2, 25, 1600))
        hand = "1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z".split()
        self.assertEqual(evaluate(hand, (), "1m", **opts)["cost"]["main"], 32000)
        hand = "1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 5s 5s".split()
        self.assertIsNone(evaluate(hand, (), "2z", seat=1, dealer=1, round_wind="west"))
        self.assertEqual(evaluate(hand, (), "2z", seat=1, dealer=1, round_wind="south")["han"], 1)

    def test_scoring_does_not_accept_unknown_conditions(self):
        with self.assertRaises(ValueError):
            evaluate(WAIT, (), "6s", seat=0, dealer=0, round_wind="east", riichi=UNKNOWN)


if __name__ == "__main__":
    unittest.main()
