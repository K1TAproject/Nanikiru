from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Discard, Game, Tile
from nanikiru.efficiency import analyze_discards, compare_discard
from nanikiru.tenpai import analyze_tenpai
from nanikiru.tenpai_comparison import compare_candidates
from test_efficiency import game_with
from test_tenpai import river


def saved(view, actions, actual):
    basic = analyze_discards(view, actions)
    return {'action_index': 1, 'player': view['observer_seat'], 'observation': deepcopy(view),
            'actual_action': {'type': 'discard', **asdict(actual)}, 'analysis': basic,
            'comparison': compare_discard(basic, actual), 'tenpai': analyze_tenpai(view, actions)}


class ComparisonTests(unittest.TestCase):
    def test_equal_efficiency_different_furiten(self):
        view = game_with(draw='7z').observe(0)
        # Public fixture: equal unseen counts, but only own 2s creates furiten.
        river(view, '2s')
        river(view, '1s', 1)
        actions = [Discard(0, Tile.parse(t), False) for t in ('1s', '5s')]
        record = saved(view, actions, actions[0])
        before = deepcopy(record)
        result = compare_candidates(record, asdict(actions[1]))
        self.assertEqual(result['differences']['shape_unseen'], 0)
        self.assertTrue(all(result[s]['basic']['is_best'] for s in ('actual', 'alternative')))
        self.assertEqual(result['actual']['tenpai']['ron_state'], 'blocked')
        self.assertEqual(result['alternative']['tenpai']['ron_state'], 'permitted')
        self.assertIn('荣和限制不同', ' '.join(result['messages']))
        self.assertEqual(record, before)
        self.assertNotIn('recommended', result)

    def test_more_waits_vs_conditional_value_and_red_identity(self):
        view = game_with(draw='7z').observe(0)
        actions = [Discard(0, Tile.parse('7z'), True), Discard(0, Tile.parse('4s'), False)]
        result = compare_candidates(saved(view, actions, actions[0]), asdict(actions[1]))
        a, b = result['actual']['tenpai'], result['alternative']['tenpai']
        self.assertGreater(a['shape_unseen'], b['shape_unseen'])
        self.assertEqual(a['waits'][0]['variants'][0]['ron']['value']['han'], 2)
        self.assertEqual([v['ron']['value']['han'] for v in b['waits'][0]['variants']], [3, 4])
        self.assertEqual([v['tile'] for v in b['waits'][0]['variants']], ['5s', '0s'])
        self.assertIn('取舍', ' '.join(result['messages']))

    def test_partial_yaku_tsumo_and_exhausted(self):
        view = game_with('2m 3m 2p 3p 4p 2s 3s 4s 7p 8p 9p 2z 2z').observe(0)
        view['round_wind'] = 'south'
        action = Discard(0, Tile.parse('9m'), True)
        for _ in range(4):
            river(view, '1m', 1)
        result = compare_candidates(saved(view, [action], action), asdict(action))
        waits = result['actual']['tenpai']['waits']
        self.assertEqual([w['tile'] for w in waits], ['1m', '4m'])
        self.assertEqual(waits[0]['unseen'], 0)
        self.assertFalse(waits[0]['variants'][0]['ron']['has_yaku'])
        self.assertTrue(waits[0]['variants'][0]['tsumo']['has_yaku'])
        self.assertTrue(waits[1]['variants'][0]['ron']['has_yaku'])
        self.assertIn('强制选择', ' '.join(result['messages']))

    def test_missing_non_tenpai_temporary_and_hidden_isolation(self):
        g = game_with()
        actions = [a for a in g.legal_actions(0) if isinstance(a, Discard)]
        actual = Discard(0, Tile.parse('9m'), True)
        view = g.observe(0)
        view['own_status']['temporary_furiten'] = True
        record = saved(view, actions, actual)
        result = compare_candidates(record, asdict(actual))
        self.assertEqual(result['actual']['tenpai']['ron_state_after_own_draw'], 'permitted')
        self.assertEqual(result['actual']['tenpai']['ron_state'], 'blocked')
        other = next(c['discard'] for c in record['analysis']['candidates'] if c['shanten'] != 0)
        self.assertEqual(compare_candidates(record, other)['status'], 'not_applicable')
        del record['tenpai']
        self.assertEqual(compare_candidates(record, asdict(actual))['status'], 'limited')
        view.pop('own_status')
        self.assertEqual(compare_candidates(saved(view, actions, actual), asdict(actual))['status'], 'limited')
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[70], wall[71] = wall[71], wall[70]
        other_game = Game.from_wall(wall)
        a = saved(g.observe(0), actions, actual)
        b = saved(other_game.observe(0), actions, actual)
        self.assertEqual(compare_candidates(a, asdict(actual)), compare_candidates(b, asdict(actual)))
        a['observation']['seed'] = 4
        with self.assertRaises(ValueError):
            compare_candidates(a, asdict(actual))

    def test_roundtrip_branch_and_defense_record_identity(self):
        g = game_with(draw='7z')
        action = Discard(0, Tile.parse('7z'), True)
        g.submit(action, mode='fold')
        record, decision = g.review_records(0)[0], g.decision_records(0)[0]
        result = compare_candidates(record, asdict(action), decision)
        self.assertEqual(result['actual']['mode'], 'fold')
        self.assertEqual(len(result['actual']['defense']['opponents']), 3)
        bad = deepcopy(decision)
        bad['action_index'] += 1
        with self.assertRaises(ValueError):
            compare_candidates(record, asdict(action), bad)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'game.json'
            g.save(path)
            loaded = Game.load(path)
            self.assertEqual(compare_candidates(loaded.review_records(0)[0], asdict(action), loaded.decision_records(0)[0]), result)
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['version'], 7)
        self.assertEqual(compare_candidates(g.replay(1).review_records(0)[0], asdict(action), decision), result)
        g.undo()
        self.assertFalse(g.review_records(0))
        g.submit(Discard(0, Tile.parse('4s'), False))
        self.assertNotEqual(compare_candidates(g.review_records(0)[0], asdict(action))['actual'], result['actual'])
