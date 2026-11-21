from copy import deepcopy
from dataclasses import asdict, replace
import json
import unittest

from examples.inspect_position import sample_position
from nanikiru import (
    UNKNOWN, RULE_ID, Action, AnalysisRequest, AnalysisResult, CandidateResult,
    Event, Metric, PlayerInfo, RoundRecord, RoundSnapshot, TileRecord,
)
from nanikiru.analysis.efficiency import hand_codes, meld_codes, visible_counts, visible_inventory


class ObservationTests(unittest.TestCase):
    def test_red_identity_and_unknown_are_not_nullable(self):
        tile = TileRecord("a", "0p", "hand", 0)
        self.assertEqual(set(asdict(tile)), {"id", "code", "zone", "seat", "group_id"})
        self.assertIs(TileRecord("b", UNKNOWN, "hand", 0).code, UNKNOWN)
        for code in (None, "0z", "8z", "10m", "unknown"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                TileRecord("bad", code, "hand", 0)

    def test_unknown_defaults_midgame_and_empty_coverage(self):
        view = RoundSnapshot("midgame", RULE_ID, 0, tuple(PlayerInfo(s) for s in range(4)))
        self.assertIs(view.draw_context, UNKNOWN)
        self.assertIs(view.remaining_draws, UNKNOWN)
        self.assertIs(hand_codes(view), UNKNOWN)
        self.assertIs(meld_codes(view), UNKNOWN)
        self.assertIs(visible_counts(view), UNKNOWN)
        self.assertEqual(visible_inventory(view), {})

    def test_midgame_claimed_tile_has_one_location_and_history_reference(self):
        view = sample_position()
        view.tiles += tuple(TileRecord(f"p{i}", "7p", "meld", 1, "g1") for i in range(3))
        view.groups["g1"] = {"kind": "pon", "called_tile_id": "p0", "source_seat": 2}
        view.initial_context["discards"] = {2: [{"tile_id": "p0", "discard_method": UNKNOWN}]}
        view.initial_context["riichi"] = {1: "established"}
        view.validate()
        self.assertEqual(visible_inventory(view)["7p"], 3)
        self.assertEqual(visible_counts(view)[15], 3)
        record = RoundRecord(view, coverage={"from_start": False, "gaps": []})
        raw = json.loads(json.dumps(asdict(record)))
        self.assertEqual(raw["initial_snapshot"]["initial_context"]["discards"]["2"][0]["discard_method"], "unknown")

    def test_unknown_public_face_prevents_exact_inventory_only(self):
        view = sample_position()
        view.tiles += (TileRecord("river", UNKNOWN, "river", 1),)
        self.assertIs(visible_counts(view), UNKNOWN)
        self.assertEqual(len(hand_codes(view)), 14)

    def test_unknown_own_card_and_untracked_count(self):
        view = sample_position()
        view.tiles = (replace(view.tiles[0], code=UNKNOWN), *view.tiles[1:])
        self.assertIs(hand_codes(view), UNKNOWN)
        view = sample_position()
        view.players = (PlayerInfo(0, 25000, 1), *view.players[1:])
        self.assertIs(hand_codes(view), UNKNOWN)

    def test_rejects_duplicate_ids_hidden_cards_and_inventory(self):
        view = sample_position()
        for extra in ((view.tiles[0],), (TileRecord("opp", "1p", "hand", 1),),
                      tuple(TileRecord(f"x{i}", "1z", "river", 1) for i in range(5)),
                      (TileRecord("r1", "0p", "river", 1), TileRecord("r2", "0p", "river", 2))):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                replace(view, tiles=(*view.tiles, *extra))

    def test_rejects_bad_draw_and_group_references(self):
        view = sample_position()
        with self.assertRaises(ValueError):
            replace(view, draw_context={"player": 1, "tile_id": "t13", "source": "live"})
        with self.assertRaises(ValueError):
            replace(view, tiles=(*view.tiles, TileRecord("x", "1z", "meld", 1, "missing")))
        with self.assertRaises(ValueError):
            replace(view, groups={"empty": {"kind": "pon", "called_tile_id": UNKNOWN, "source_seat": UNKNOWN}})

    def test_events_order_targets_and_new_format(self):
        view = sample_position()
        events = (Event(1, "discard", 0, data={"tile_id": "t13", "method": "drawn"}),
                  Event(2, "response_end", target_id=1))
        record = RoundRecord(view, events)
        self.assertEqual(record.schema_version, "observation-v1")
        for bad in ((events[1], events[0]), (events[0], events[0]), (events[1],)):
            with self.assertRaises(ValueError):
                RoundRecord(view, bad)
        with self.assertRaises(ValueError):
            RoundRecord(view, schema_version="nanikiru-game-v7")
        with self.assertRaises(ValueError):
            Event(1, "response", target_id=1)


class ContractTests(unittest.TestCase):
    def test_request_preserves_partial_input(self):
        view = sample_position()
        before = deepcopy(view)
        request = AnalysisRequest("revision:2", view, ("discard_shape", "danger"))
        self.assertEqual(view, before)
        self.assertIs(request.coverage["from_start"], UNKNOWN)

    def test_action_keeps_drawn_and_red_identity(self):
        self.assertNotEqual(Action("discard", 0, ("old-five",)), Action("discard", 0, ("drawn-five",)))
        self.assertNotEqual(Action("discard", 0, ("red-five",)), Action("riichi", 0, ("red-five",)))
        with self.assertRaises(ValueError):
            Action("discard", 0)

    def test_output_distinguishes_estimates_unavailable_and_zero(self):
        action = Action("discard", 0, ("t13",))
        candidate = CandidateResult(action, "allowed", {
            "shanten": Metric("available", 0, "exact"),
            "win_probability": Metric("unavailable", details={"reason": "not_implemented"}),
        })
        result = AnalysisResult("revision:2", "test-v1", candidates=(candidate,), recommendations={"overall": (action,)})
        self.assertEqual(result.candidates[0].metrics["shanten"].value, 0)
        with self.assertRaises(ValueError):
            Metric("unavailable", 0)
        with self.assertRaises(ValueError):
            Metric("available", .2)

    def test_unknown_or_forbidden_action_is_not_executable_recommendation(self):
        action = Action("discard", 0, ("t13",))
        for legality in ("unknown", "forbidden"):
            with self.subTest(legality=legality), self.assertRaises(ValueError):
                AnalysisResult("v1", "test", candidates=(CandidateResult(action, legality),), recommendations={"overall": (action,)})


if __name__ == "__main__":
    unittest.main()
