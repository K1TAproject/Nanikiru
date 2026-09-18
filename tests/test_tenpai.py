from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Decision, Discard, Game, RecordError, Rules, Tile
from nanikiru.tenpai import analyze_tenpai
from nanikiru.scoring import evaluate
from test_efficiency import game_with, candidate
from test_rules import fixture, tiles, WAIT, discard_draw, pass_all


def analyze(game):
    return analyze_tenpai(game.observe(0), [a for a in game.legal_actions(0) if isinstance(a, Discard)])


def river(view, code, seat=0, claimed=None):
    view['players'][seat]['discards'].append({'id': 'test', 'tile': asdict(Tile.parse(code)), 'claimed_by': claimed})


class TenpaiTests(unittest.TestCase):
    def test_regular_multiple_waits_and_scoring_adapter(self):
        g = game_with()
        original = g.debug_state()
        c = candidate(analyze(g), '9m')
        self.assertEqual((c['shape_wait_types'], c['shape_unseen']), (2, 7))
        self.assertEqual([w['tile'] for w in c['waits']], ['3s', '6s'])
        for w in c['waits']:
            for v in w['variants']:
                for kind in ('ron', 'tsumo'):
                    expected = evaluate(original.players[0], Tile.parse(v['tile']), tsumo=kind == 'tsumo',
                                        indicators=[Tile.parse('8p')])
                    self.assertEqual(v[kind]['value'], expected)
        self.assertEqual(g.debug_state(), original)
        self.assertTrue(any(c['status'] == 'not_applicable' for c in analyze(g)['candidates']))

    def test_special_shapes(self):
        for hand, draw, kinds in (
            ('1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 4s 4s 1z', '9s', 1),
            ('1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z', '2m', 13)):
            c = candidate(analyze(game_with(hand, draw)), draw)
            self.assertEqual(c['shape_wait_types'], kinds)
            self.assertTrue(all(v['ron']['has_yaku'] for w in c['waits'] for v in w['variants']))

    def test_no_yaku_ron_but_closed_tsumo(self):
        hand = '1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 4m 6m'
        g = game_with(hand)
        c = candidate(analyze(g), '9m')
        self.assertEqual(c['waits'][0]['tile'], '5m')
        for v in c['waits'][0]['variants']:
            self.assertFalse(v['ron']['has_yaku'])
            self.assertIsNone(v['ron']['value'])
            self.assertTrue(v['tsumo']['has_yaku'])
        view = g.observe(0)
        view['players'][0]['melds'] = [{'kind': 'chi', 'tiles': [asdict(t) for t in tiles('1m 2m 3m')], 'source_discard_id': None}]
        view['players'][0]['hand']['known_tiles'] = view['players'][0]['hand']['known_tiles'][3:]
        c = candidate(analyze_tenpai(view, [Discard(0, Tile.parse('9m'), True)]), '9m')
        self.assertFalse(c['waits'][0]['variants'][0]['tsumo']['has_yaku'])

    def test_furiten_whole_wait_and_current_discard(self):
        g = game_with()
        view = g.observe(0)
        river(view, '6s')
        c = candidate(analyze_tenpai(view, [Discard(0, Tile.parse('9m'), True)]), '9m')
        self.assertEqual(c['ron_restrictions'], ['discard_furiten'])
        self.assertTrue(all(v['ron']['permission'] == 'blocked' and v['ron']['has_yaku']
                            for w in c['waits'] for v in w['variants']))
        c = candidate(analyze(game_with(WAIT, '6s')), '6s', True)
        self.assertEqual(c['ron_state'], 'blocked')
        self.assertEqual(c['shape_unseen'], 6)  # The discarded winning tile remains seen.

    def test_temporary_permanent_unknown_and_private_observation(self):
        g = game_with()
        view = g.observe(0)
        action = [Discard(0, Tile.parse('9m'), True)]
        for flag in ('temporary_furiten', 'riichi_furiten'):
            changed = deepcopy(view)
            changed['own_status'][flag] = True
            c = candidate(analyze_tenpai(changed, action), '9m')
            self.assertEqual(c['ron_restrictions'], [flag])
            self.assertEqual(c['ron_state_after_own_draw'], 'permitted' if flag == 'temporary_furiten' else 'blocked')
            self.assertTrue(all(v['tsumo']['permission'] == 'permitted' for w in c['waits'] for v in w['variants']))
        del view['own_status']
        result = analyze_tenpai(view, action)
        self.assertTrue(result['limited'])
        self.assertEqual(candidate(result, '9m')['ron_state'], 'unknown')
        g = fixture({1: WAIT}, '6s 9m')
        discard_draw(g)
        g.submit(Decision(1, 'pass'))
        self.assertTrue(g.observe(1)['own_status']['temporary_furiten'])
        self.assertFalse(g.observe(0)['own_status']['temporary_furiten'])
        pass_all(g)
        self.assertFalse(g.observe(1)['own_status']['temporary_furiten'])
        self.assertTrue(all('temporary_furiten' not in p for p in g.observe(0)['players']))

    def test_red_inventory_and_switch(self):
        hand = '1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 4m 6m'
        g = game_with(hand)
        view = g.observe(0)
        action = [Discard(0, Tile.parse('9m'), True)]
        river(view, '5m', 1)
        a = candidate(analyze_tenpai(view, action), '9m')['waits'][0]['variants']
        self.assertEqual([(v['tile'], v['unseen']) for v in a], [('5m', 2), ('0m', 1)])
        self.assertEqual(a[1]['tsumo']['value']['han'], a[0]['tsumo']['value']['han'] + 1)
        view['rules']['aka_dora_enabled'] = False
        b = candidate(analyze_tenpai(view, action), '9m')['waits'][0]['variants']
        self.assertEqual(b[0]['tsumo']['value'], b[1]['tsumo']['value'])
        view['players'][1]['melds'] = [{'kind': 'pon', 'tiles': [asdict(t) for t in tiles('0m 5m 5m')], 'source_discard_id': 'test'}]
        view['players'][1]['discards'][0]['claimed_by'] = 1
        view['dora_indicators'] = [asdict(Tile.parse('5m'))]
        c = candidate(analyze_tenpai(view, action), '9m')
        self.assertEqual((c['shape_wait_types'], c['available_wait_types'], c['shape_unseen']), (1, 0, 0))

    def test_meld_kan_counts_no_fifth_copy(self):
        for kind, meld in (('chi', '7m 8m 9m'), ('closed_kan', '9m 9m 9m 9m'), ('open_kan', '9m 9m 9m 9m')):
            view = game_with().observe(0)
            p = view['players'][0]
            p['hand']['known_tiles'] = [asdict(t) for t in tiles('1p 2p 3p 1s 2s 3s 7z 7z 4s 5s')]
            p['hand']['drawn_tile'] = {'tile': asdict(Tile.parse('9p'))}
            p['melds'] = [{'kind': kind, 'tiles': [asdict(t) for t in tiles(meld)], 'source_discard_id': None}]
            c = candidate(analyze_tenpai(view, [Discard(0, Tile.parse('9p'), True)]), '9p')
            self.assertEqual(c['shape_unseen'], 7)
        p['hand']['known_tiles'] = [asdict(t) for t in tiles('1p 2p 3p 1s 2s 3s 7z 7z 7z 9m')]
        p['melds'][0] = {'kind': 'pon', 'tiles': [asdict(t) for t in tiles('9m 9m 9m')], 'source_discard_id': None}
        # Four nines already in the complete own holding: no fifth-copy pair.
        c = candidate(analyze_tenpai(view, [Discard(0, Tile.parse('9p'), True)]), '9p')
        self.assertEqual(c['shape_wait_types'], 0)

    def test_core_conditional_ron_consistency_and_riichi_declaration(self):
        g = fixture({1: WAIT}, '9m 9p 9s 8m 6s', slots={130: Tile.parse('8p')})
        discard_draw(g)
        pass_all(g)
        obs = g.observe(1)
        c = candidate(analyze_tenpai(obs, [Discard(1, Tile.parse('9p'), True)]), '9p')
        expected = next(w for w in c['waits'] if w['tile'] == '6s')['variants'][0]['ron']['value']
        for _ in range(3):
            discard_draw(g)
            pass_all(g)
        discard_draw(g)
        self.assertEqual(g._win_value(1), expected)
        g = game_with()
        g.submit(Decision(0, 'riichi', (Tile.parse('9m'),), True))
        record = g.review_records(0)[0]
        self.assertFalse(record['observation']['players'][0]['riichi'])
        self.assertFalse(any(y['name'] == 'Riichi' for w in candidate(record['tenpai'], '9m')['waits']
                             for v in w['variants'] for y in v['ron']['value']['yaku']))

    def test_hidden_isolation_and_inputs_unchanged(self):
        g = game_with()
        wall = g.debug_state().wall
        wall[1], wall[2] = wall[2], wall[1]
        wall[70], wall[71] = wall[71], wall[70]
        other = Game.from_wall(wall)
        self.assertEqual(analyze(g), analyze(other))
        view, legal = g.observe(0), [Discard(0, Tile.parse('9m'), True)]
        before = deepcopy((view, legal))
        analyze_tenpai(view, legal)
        self.assertEqual((view, legal), before)
        with self.assertRaises(ValueError):
            analyze_tenpai(g.debug_view(), legal)

    def test_records_branch_save_legacy_and_tampering(self):
        g = game_with()
        discard_draw(g)
        first = g.debug_reviews()
        self.assertEqual(g.replay(1).debug_reviews(), first)
        g.undo()
        self.assertEqual(g.debug_reviews(), [])
        g.submit(Discard(0, Tile.parse('1m'), False))
        self.assertNotEqual(g.debug_reviews(), first)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'game.json'
            g.save(path)
            self.assertEqual(Game.load(path).debug_reviews(), g.debug_reviews())
            data = json.loads(path.read_text(encoding='utf-8'))
            bad = deepcopy(data)
            bad['reviews'][0]['tenpai']['version'] = 'future'
            path.write_text(json.dumps(bad), encoding='utf-8')
            with self.assertRaises(RecordError):
                Game.load(path)
            data['version'] = 5
            for r in data['reviews']:
                del r['tenpai']
                del r['observation']['own_status']
            for r in data['decision_records']:
                del r['observation']['own_status']
            path.write_text(json.dumps(data), encoding='utf-8')
            old = Game.load(path)
            self.assertEqual(old.debug_reviews(), data['reviews'])
            old.save(path)
            self.assertEqual(Game.load(path).debug_reviews(), data['reviews'])
