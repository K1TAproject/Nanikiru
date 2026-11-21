from copy import deepcopy
from dataclasses import replace
import json
import unittest
from unittest.mock import patch

from event_ui.initial import make_record
from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json, require_current_result, summarize_result
from nanikiru.models import UNKNOWN, Event, RoundRecord
from nanikiru.processing import Session
from nanikiru.processing.analysis_input import build_analysis_request, extract_player_view


def initial(**kwargs):
    return make_record("1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s", **kwargs)


def discard(session):
    session.append("discard", actor=0, data={"tile_id": "entry-draw", "code": "9m", "discard_kind": UNKNOWN})


class AnalysisPipelineTests(unittest.TestCase):
    def test_prefix_excludes_future_even_unsupported_payloads(self):
        record = initial()
        future = replace(record, events=(Event(1, "unimplemented_future", 1, data={"secret": "FUTURE_SECRET"}),))
        before = deepcopy(future)
        a = build_analysis_request(record, source_version="r", through=0)
        b = build_analysis_request(future, source_version="r", through=0)
        self.assertEqual(a, b)
        self.assertNotIn("FUTURE_SECRET", debug_json(b))
        self.assertEqual(future, before)

    def test_extract_copies_and_rejects_observer_switch(self):
        record = initial()
        view, history, coverage = extract_player_view(record, 0, observer_seat=0)
        view.completeness["public_tiles"] = False
        coverage["from_start"] = False
        self.assertTrue(record.initial_snapshot.completeness["public_tiles"])
        self.assertTrue(record.coverage["from_start"])
        self.assertEqual(history, ())
        for seat in (1, True):
            with self.assertRaises(ValueError):
                extract_player_view(record, observer_seat=seat)
        for through in (-1, 1, True):
            with self.assertRaises(ValueError):
                extract_player_view(record, through)

    def test_entry_extensions_and_private_responses_do_not_leak(self):
        record = initial()
        record.initial_snapshot.draw_context["secret"] = "HIDDEN"
        record.initial_snapshot.completeness["private_hint"] = True
        request = build_analysis_request(record, source_version="v")
        self.assertNotIn("HIDDEN", debug_json(request))
        self.assertNotIn("private_hint", debug_json(request))
        record.initial_snapshot.initial_context["secret"] = "HIDDEN"
        with self.assertRaises(ValueError):
            build_analysis_request(record, source_version="v")
        session = Session(initial())
        discard(session)
        position = session.position
        position.pending["trigger_event"] = UNKNOWN
        position.pending["responses"] = {1: "PRIVATE_RESPONSE"}
        record = RoundRecord(position, coverage={"from_start": False, "gaps": []})
        request = build_analysis_request(record, source_version="v")
        self.assertEqual(request.position.pending["responses"], {})
        self.assertNotIn("PRIVATE_RESPONSE", debug_json(request))

    def test_minimal_method_returns_conditional_ranking_not_permission(self):
        request = build_analysis_request(initial(), source_version="r")
        result = analyze(request)
        self.assertEqual(result.state_version, "r:0")
        self.assertEqual(result.method, "minimal-shape-v1")
        ranking = result.situation["discard_ranking"]
        self.assertEqual((ranking.status, ranking.basis), ("conditional", "heuristic"))
        self.assertEqual(ranking.value[0][0].tile_ids, ("entry-draw",))
        self.assertEqual(len(result.candidates), 14)
        self.assertTrue(all(c.legality == "unknown" for c in result.candidates))
        self.assertEqual(result.recommendations, {})

    def test_basic_method_and_task_selection_do_not_run_other_work(self):
        request = build_analysis_request(initial(), source_version="r", method="basic-v1", tasks=("shanten",))
        with patch("nanikiru.analysis.dispatcher.analyze_waits", side_effect=AssertionError("unrequested")):
            result = analyze(request)
        self.assertEqual(set(result.situation), {"shanten"})
        self.assertEqual(result.candidates, ())
        self.assertEqual(result.method, "basic-v1")

    def test_unsupported_method_task_goal_do_not_fallback(self):
        request = build_analysis_request(initial(), source_version="r")
        for changed in (replace(request, method="neural-v1"), replace(request, tasks=("forecast",)),
                        replace(request, method="basic-v1"), replace(request, objective="match_rank")):
            with self.subTest(changed=changed.method), self.assertRaises(ValueError):
                analyze(changed)
        with self.assertRaises(ValueError):
            build_analysis_request(initial(), source_version="r", tasks="shanten")
        with self.assertRaises(ValueError):
            build_analysis_request(initial(), source_version="r", tasks=("shanten", "shanten"))

    def test_unknown_public_coverage_only_disables_ukeire_ranking(self):
        request = build_analysis_request(initial(from_start=False), source_version="r")
        result = analyze(request)
        self.assertEqual(result.situation["shanten"].status, "available")
        self.assertTrue(all(c.metrics["ukeire"].status == "unavailable" for c in result.candidates))
        self.assertEqual(result.situation["discard_ranking"].details["criteria"], ("shanten",))
        self.assertIn("仅比较向听", summarize_result(request, result))

    def test_unknown_hand_is_not_a_fake_zero(self):
        record = initial()
        p = record.initial_snapshot
        p.tiles = tuple(replace(t, code=UNKNOWN) if t.id == "h0" else t for t in p.tiles)
        request = build_analysis_request(record, source_version="r")
        result = analyze(request)
        self.assertEqual(result.situation["shanten"].status, "unavailable")
        self.assertIsNone(result.situation["shanten"].value)
        self.assertEqual(result.situation["discard_ranking"].status, "unavailable")

    def test_non_own_turn_and_unknown_phase_are_distinct(self):
        session = Session(initial())
        discard(session)
        request = build_analysis_request(session.record, source_version=session.state_version)
        result = analyze(request)
        self.assertEqual(result.situation["waits"].status, "available")
        self.assertEqual(result.situation["discard_ranking"].status, "not_applicable")
        self.assertEqual(result.candidates, ())
        session.append("halt", data={"reason": "pon"})
        request = build_analysis_request(session.record, source_version=session.state_version)
        self.assertEqual(analyze(request).situation["discard_ranking"].status, "unavailable")

    def test_repeatability_nonmutation_and_version_guard(self):
        session = Session(initial())
        request = build_analysis_request(session.record, source_version=session.state_version)
        before = deepcopy(request)
        first = analyze(request)
        self.assertEqual(first, analyze(request))
        self.assertEqual(request, before)
        discard(session)
        after = build_analysis_request(session.record, source_version=session.state_version)
        with self.assertRaises(ValueError):
            require_current_result(first, after.state_version, after.method)
        with self.assertRaises(ValueError):
            require_current_result(first, request.state_version, "basic-v1")
        session.undo()
        restored = build_analysis_request(session.record, source_version=session.state_version)
        self.assertNotEqual(restored.state_version, request.state_version)

    def test_debug_serializes_actual_objects_and_output_does_not_recompute(self):
        request = build_analysis_request(initial(), source_version="r")
        result = analyze(request)
        payload = json.loads(debug_json(request))
        self.assertEqual(payload["position"]["remaining_draws"], {"$unknown": True})
        self.assertIsNone(payload["position"]["pending"])
        self.assertEqual(payload["method"], request.method)
        self.assertEqual(json.loads(debug_json(result))["recommendations"], {})
        selected = next(c for c in result.candidates if c.action.tile_ids == ("entry-draw",))
        selected.metrics["ukeire"].value = 123
        self.assertIn("123 枚", summarize_result(request, result, "entry-draw"))


if __name__ == "__main__":
    unittest.main()
