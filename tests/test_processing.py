from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from event_ui.initial import make_record
from nanikiru.models import UNKNOWN, Event, RoundRecord
from nanikiru.processing import Session, apply_event, append_event, replay, save_record, load_record, undo


def initial(**kwargs):
    return make_record("1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s", **kwargs)


def discard(session, seat=0, tile_id="entry-draw", code="9m", kind="tsumogiri"):
    session.append("discard", actor=seat, data={"tile_id": tile_id, "code": code, "discard_kind": kind})


def close(session):
    session.append("close_response", target_id=session.position.pending["trigger_event"])


class ProcessingTests(unittest.TestCase):
    def test_full_cycle_and_deterministic_replay(self):
        session = Session(initial(remaining=20))
        discard(session)
        self.assertEqual(session.position.phase, "await_response")
        close(session)
        for seat, code in ((1, "8p"), (2, "8s"), (3, "8m")):
            session.append("draw", actor=seat, data={"tile_id": None, "code": UNKNOWN})
            self.assertEqual(session.position.players[seat].untracked_hand_count, 14)
            discard(session, seat, f"river-{seat}", code, UNKNOWN)
            self.assertEqual(session.position.players[seat].untracked_hand_count, 13)
            close(session)
        session.append("draw", actor=0, data={"tile_id": "new-draw", "code": "6s"})
        self.assertEqual(session.position.remaining_draws, 16)
        self.assertEqual(session.position.draw_context["tile_id"], "new-draw")
        self.assertEqual(session.position, replay(session.record))
        self.assertEqual(len([t for t in session.position.tiles if t.zone == "hand"]), 14)
        self.assertEqual(len([t for t in session.position.tiles if t.zone == "river"]), 4)

    def test_error_is_atomic_and_revision_changes_only_on_success(self):
        session = Session(initial())
        before, version = session.record, session.state_version
        with self.assertRaises(ValueError):
            discard(session, code="8m")
        self.assertEqual(session.record, before)
        self.assertEqual(session.state_version, version)
        discard(session)
        self.assertNotEqual(session.state_version, version)
        with self.assertRaises(ValueError):
            session.append("draw", actor=1, data={"tile_id": None, "code": UNKNOWN})
        self.assertEqual(len(session.record.events), 1)

    def test_wrong_actor_duplicate_and_target(self):
        record = initial()
        event = Event(1, "discard", 0, data={"tile_id": "entry-draw", "code": "9m", "discard_kind": "tsumogiri"})
        after = append_event(record, event)
        with self.assertRaises(ValueError):
            append_event(after, event)
        with self.assertRaises(ValueError):
            append_event(after, Event(3, "close_response", target_id=2))
        with self.assertRaises(ValueError):
            apply_event(record.initial_snapshot, replace(event, actor=1))

    def test_private_opponent_draw_is_rejected(self):
        session = Session(initial())
        discard(session)
        close(session)
        for tile_id, code in ((None, "1m"), ("private", UNKNOWN)):
            with self.assertRaises(ValueError):
                session.append("draw", actor=1, data={"tile_id": tile_id, "code": code})
        self.assertEqual(len(session.record.events), 2)

    def test_unknown_face_can_be_revealed_without_duplicate(self):
        record = initial()
        p = record.initial_snapshot
        p.tiles = tuple(replace(t, code=UNKNOWN) if t.id == "entry-draw" else t for t in p.tiles)
        session = Session(record)
        discard(session)
        self.assertEqual(len(session.position.tiles), 15)
        tile = next(t for t in session.position.tiles if t.id == "entry-draw")
        self.assertEqual((tile.zone, tile.code), ("river", "9m"))

    def test_response_close_does_not_fabricate_private_passes(self):
        session = Session(initial())
        discard(session)
        self.assertEqual(session.position.pending["responses"], {})
        close(session)
        self.assertEqual(session.record.events[-1].data, {})
        self.assertIsNone(session.position.pending)
        position = replay(session.record, 1)
        position.pending["responses"] = {1: "ron"}
        with self.assertRaises(ValueError):
            apply_event(position, session.record.events[-1])

    def test_undo_branch_and_defensive_views(self):
        session = Session(initial())
        discard(session)
        version = session.state_version
        session.undo()
        self.assertNotEqual(session.state_version, version)
        discard(session, tile_id="h0", code="1m", kind="hand")
        self.assertNotEqual(session.state_version, version)
        detached = session.record
        detached.events[0].data["code"] = "9m"
        self.assertEqual(session.record.events[0].data["code"], "1m")
        with self.assertRaises(ValueError):
            undo(initial())

    def test_payload_unknown_kind_and_halt(self):
        session = Session(initial())
        for kind, data in (("pon", {}), ("discard", {"seed": 123})):
            with self.assertRaises(ValueError):
                session.append(kind, actor=0, data=data)
        session.append("halt", data={"reason": "riichi"})
        self.assertIs(session.position.phase, UNKNOWN)
        with self.assertRaises(ValueError):
            discard(session)
        session.undo()
        discard(session)

    def test_last_draw_blocks_without_fake_settlement(self):
        session = Session(initial(remaining=0))
        discard(session)
        close(session)
        with self.assertRaises(ValueError):
            session.append("draw", actor=1, data={"tile_id": None, "code": UNKNOWN})
        self.assertEqual(session.position.phase, "await_draw")

    def test_inventory_and_discard_kind_errors_leave_input_unchanged(self):
        p = initial().initial_snapshot
        before = deepcopy(p)
        with self.assertRaises(ValueError):
            apply_event(p, Event(1, "discard", 0, data={"tile_id": "entry-draw", "code": "9m", "discard_kind": "hand"}))
        with self.assertRaises(ValueError):
            apply_event(p, Event(1, "discard", 0, data={"tile_id": "new", "code": "1m", "discard_kind": UNKNOWN}))
        self.assertEqual(p, before)

    def test_midgame_unknown_counts_and_coverage_remain_unknown(self):
        record = initial(from_start=False, actor=1, phase="await_draw")
        p = record.initial_snapshot
        p.players = tuple(replace(x, untracked_hand_count=UNKNOWN) if x.seat == 1 else x for x in p.players)
        session = Session(record)
        session.append("draw", actor=1, data={"tile_id": None, "code": UNKNOWN})
        discard(session, 1, "mid-river", "8s", UNKNOWN)
        self.assertIs(session.position.players[1].untracked_hand_count, UNKNOWN)
        self.assertFalse(session.position.completeness["public_tiles"])
        self.assertFalse(session.record.coverage["from_start"])

    def test_roundtrip_unknown_none_and_pending(self):
        session = Session(initial(from_start=False))
        discard(session, kind=UNKNOWN)
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / "round.json"
            save_record(session.record, path)
            loaded = load_record(path)
            self.assertEqual(loaded, session.record)
            self.assertIs(loaded.events[0].data["discard_kind"], UNKNOWN)
            self.assertIs(loaded.initial_snapshot.remaining_draws, UNKNOWN)
            self.assertIsNone(loaded.initial_snapshot.pending)
            self.assertEqual(replay(loaded), session.position)
            version = session.state_version
            session.load(path)
            self.assertNotEqual(session.state_version, version)

    def test_bad_load_and_bad_save_do_not_replace_good_data(self):
        session = Session(initial())
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / "round.json"
            save_record(session.record, path)
            before = path.read_bytes()
            bad = replace(session.record, events=(Event(1, "pon", 0),))
            with self.assertRaises(ValueError):
                save_record(bad, path)
            self.assertEqual(path.read_bytes(), before)
            doc = json.loads(before)
            doc["record"]["events"] = [{"id": 1, "kind": "draw", "actor": 1, "target_id": None,
                                         "data": {"tile_id": "secret", "code": "1m"}}]
            path.write_text(json.dumps(doc), encoding="utf-8")
            previous, version = session.record, session.state_version
            with self.assertRaises(ValueError):
                session.load(path)
            self.assertEqual(session.record, previous)
            self.assertEqual(session.state_version, version)

    def test_entry_response_without_preentry_event_identity(self):
        session = Session(initial())
        discard(session)
        p = session.position
        p.pending["trigger_event"] = UNKNOWN
        record = RoundRecord(p, coverage={"from_start": False, "gaps": []})
        result = append_event(record, Event(1, "close_response", target_id=UNKNOWN))
        self.assertEqual(replay(result).actor, 1)

    def test_unknown_own_draw_and_inventory_overflow(self):
        record = initial(from_start=False, phase="await_draw")
        p = record.initial_snapshot
        session = Session(record)
        session.append("draw", actor=0, data={"tile_id": "unknown-draw", "code": UNKNOWN})
        tile = next(t for t in session.position.tiles if t.id == "unknown-draw")
        self.assertIs(tile.code, UNKNOWN)
        self.assertEqual(session.position.players[0].untracked_hand_count, 0)
        before = deepcopy(p)
        with self.assertRaises(ValueError):
            apply_event(p, Event(1, "draw", 0, data={"tile_id": "h0", "code": "1m"}))
        self.assertEqual(p, before)

    def test_known_wrong_hand_count_and_gaps_are_not_silently_filled(self):
        record = initial(from_start=False, actor=1, phase="await_draw")
        record.initial_snapshot.players = tuple(replace(p, untracked_hand_count=12) if p.seat == 1 else p
                                                for p in record.initial_snapshot.players)
        with self.assertRaises(ValueError):
            append_event(record, Event(1, "draw", 1, data={"tile_id": None, "code": UNKNOWN}))
        record.coverage["gaps"] = [{"after": 0}]
        with self.assertRaises(ValueError):
            replay(record)

    def test_invalid_entry_and_fifth_public_copy_are_rejected(self):
        record = initial()
        record.initial_snapshot.tiles = tuple(t for t in record.initial_snapshot.tiles if t.id != "h0")
        with self.assertRaises(ValueError):
            Session(record)
        record = initial(from_start=False, actor=1, rivers=(("9m",)*4, (), (), ()))
        session = Session(record)
        before, version = session.record, session.state_version
        with self.assertRaises(ValueError):
            discard(session, 1, "fifth", "9m", UNKNOWN)
        self.assertEqual(session.record, before)
        self.assertEqual(session.state_version, version)


if __name__ == "__main__":
    unittest.main()
