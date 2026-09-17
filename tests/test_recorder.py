import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from examples.record_round import finish, sample_snapshot
from nanikiru import Discard, Draw, MeldKind, Phase, RecordError, Recorder, Tile


class RecorderTests(unittest.TestCase):
    def test_example_run_from_its_own_directory(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as folder:
            example_dir = Path(folder) / "examples"
            example_dir.mkdir()
            script = example_dir / "record_round.py"
            shutil.copyfile(project / "examples" / "record_round.py", script)
            env = dict(os.environ, PYTHONPATH=str(project))
            result = subprocess.run([sys.executable, str(script)], cwd=example_dir,
                                    env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(Recorder.load(example_dir / "paused_round.json").paused)
            self.assertEqual(Recorder.load(example_dir / "completed_round.json").state.phase,
                             Phase.EXHAUSTED)

    def setUp(self):
        self.initial, self.future = sample_snapshot()
        self.r = Recorder(self.initial)

    def discard_first(self):
        self.r.submit(Discard(0, Tile.parse("5z"), True))

    def assert_rejected(self, action):
        before, events = self.r.state, self.r.events
        with self.assertRaises(RecordError):
            self.r.submit(action)
        self.assertEqual(self.r.state, before)
        self.assertEqual(self.r.events, events)

    def test_tile_validation(self):
        for code in ("0z", "8z", "1x", "10m", "-m"):
            with self.subTest(code=code), self.assertRaises(ValueError):
                Tile.parse(code)
        self.assertEqual(str(Tile.parse("0p")), "0p")
        self.assertNotEqual(Tile.parse("0p"), Tile.parse("5p"))

    def test_initial_snapshot_and_defensive_copy(self):
        self.assertEqual(self.r.state.remaining_draws, 5)
        self.initial.players[0].score = 0
        external = self.r.state
        external.players[0].hand.known_tiles.clear()
        self.assertEqual(self.r.state.players[0].score, 25000)
        self.assertEqual(len(self.r.state.players[0].hand.known_tiles), 13)
        self.assertEqual(self.r.state.seat_wind(1), "south")

    def test_initial_melds_count_sources_and_roundtrip(self):
        for kind, remaining in ((MeldKind.CHI, 6), (MeldKind.PON, 6),
                                (MeldKind.OPEN_KAN, 5), (MeldKind.ADDED_KAN, 5),
                                (MeldKind.CLOSED_KAN, 4)):
            with self.subTest(kind=kind):
                initial, future = sample_snapshot(kind)
                r = Recorder(initial)
                self.assertEqual(r.state.remaining_draws, remaining)
                r.submit(Discard(0, Tile.parse("5z"), True))
                finish(r, future)
                self.assertEqual(r.state.phase, Phase.EXHAUSTED)
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / "round.json"
                    r.save(path)
                    self.assertEqual(Recorder.load(path).state, r.state)

    def test_bad_snapshot_count_phase_source_and_indicator(self):
        for change in (lambda s: setattr(s.players[1].hand, "unknown_count", 12),
                       lambda s: setattr(s, "actor", 1),
                       lambda s: s.dora_indicators.clear(),
                       lambda s: setattr(s.players[0].discards[0], "claimed_by", 2)):
            initial, _ = sample_snapshot()
            change(initial)
            with self.assertRaises(RecordError):
                Recorder(initial)
        initial, _ = sample_snapshot(MeldKind.CHI)
        initial.players[1].melds[0].source_discard_id = "missing"
        with self.assertRaises(RecordError):
            Recorder(initial)

    def test_invalid_actions_are_atomic(self):
        for action in (Draw(0, Tile.parse("1m")), Discard(1, Tile.parse("1m")),
                       Discard(0, Tile.parse("1m"), False), Discard(0, Tile.parse("1z"), True),
                       Discard(0, Tile.parse("5z")), {"type": "pon"}, Draw(True)):
            self.assert_rejected(action)
        self.discard_first()
        self.assert_rejected(Draw(2))
        self.assert_rejected(Draw(1, Tile.parse("1m")))
        self.r.submit(Draw(1))
        self.assert_rejected(Draw(2))
        # All four 1m already appear in the river; rejection happens after candidate mutation.
        self.assert_rejected(Discard(1, Tile.parse("1m"), True))

    def test_hand_discard_merges_draw(self):
        self.r.submit(Discard(0, Tile.parse("1s"), False))
        hand = self.r.state.players[0].hand
        self.assertIsNone(hand.drawn_tile)
        self.assertNotIn(Tile.parse("1s"), hand.known_tiles)
        self.assertIn(Tile.parse("5z"), hand.known_tiles)

    def test_established_riichi(self):
        self.initial.players[0].riichi = True
        self.initial.players[1].riichi = True
        self.r = Recorder(self.initial)
        self.assert_rejected(Discard(0, Tile.parse("1s"), False))
        self.discard_first()
        self.r.submit(Draw(1))
        self.assert_rejected(Discard(1, self.future[0], False))
        self.r.submit(Discard(1, self.future[0], None))
        self.assertIsNone(self.r.state.players[1].discards[-1].is_tsumogiri)

    def test_stop_save_resume_and_exhaustion(self):
        self.discard_first()
        self.r.stop()
        self.assert_rejected(Draw(1))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "round.json"
            self.r.save(path)
            self.r = Recorder.load(path)
            self.assertTrue(self.r.paused)
            self.r.resume()
            finish(self.r, self.future)
            self.assertEqual(self.r.state.phase, Phase.EXHAUSTED)
            self.assertEqual([p.score for p in self.r.state.players], [25000] * 4)
            self.assert_rejected(Draw(2))
            self.r.save(path)
            self.assertEqual(Recorder.load(path).state, self.r.state)

    def test_replay_rewind_last_draw_and_undo(self):
        self.discard_first()
        finish(self.r, self.future)
        self.assertEqual(self.r.replay(), self.r.state)
        self.assertEqual(self.r.replay(0), self.initial)
        last = self.r.events[-1]
        self.r.undo()
        self.assertEqual(self.r.state.remaining_draws, 0)
        self.assertEqual(self.r.state.phase, Phase.DISCARD)
        self.r.submit(last)
        self.assertEqual(self.r.state.phase, Phase.EXHAUSTED)
        self.r.rewind(0)
        self.assertEqual(self.r.state, self.initial)
        with self.assertRaises(RecordError):
            self.r.undo()
        with self.assertRaises(RecordError):
            self.r.replay(-1)

    def test_corrupt_recordings(self):
        self.discard_first()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "round.json"
            self.r.save(path)
            original = json.loads(path.read_text(encoding="utf-8"))
            for change in (lambda d: d.update(schema_version=99),
                           lambda d: d["events"][0].update(id=2),
                           lambda d: d["events"][0].update(type="kan"),
                           lambda d: d["events"][0].update(player=1),
                           lambda d: d.update(paused="false")):
                data = json.loads(json.dumps(original))
                change(data)
                path.write_text(json.dumps(data), encoding="utf-8")
                with self.assertRaises(RecordError):
                    Recorder.load(path)


if __name__ == "__main__":
    unittest.main()
