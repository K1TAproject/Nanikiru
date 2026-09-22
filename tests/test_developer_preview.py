from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru import Decision, Discard, Game, Tile
from nanikiru.bot import choose_action, choose_yaku_action
from nanikiru.progression import analyze_progression
from nanikiru.riichi_decision import analyze_riichi, choose_riichi_action, VERSION
from test_efficiency import game_with
from test_rules import pass_all, fixture, tiles

NO_YAKU = '1m 2m 3m 4p 5p 6p 7s 8s 9s 2z 2z 4m 6m'
ONE_AWAY = '1m 2m 3m 4p 5p 6p 7s 8s 2z 2z 4m 6m 8p'


class DeveloperPreviewTests(unittest.TestCase):
    def test_riichi_same_discard_double_context_and_no_mutation(self):
        game = game_with(NO_YAKU)
        before = game.debug_state()
        obs = game.observe(0)
        original = deepcopy(obs)
        legal = game.legal_actions(0)
        result = analyze_riichi(obs, legal)
        row = result['candidates'][0]
        self.assertTrue(row['recommend_riichi'])
        self.assertEqual(row['dama']['yaku_unseen']['ron'], 0)
        self.assertGreater(row['riichi']['yaku_unseen']['ron'], 0)
        self.assertEqual(row['dama']['shape_unseen'], row['riichi']['shape_unseen'])
        self.assertEqual(row['dama_action']['tile'], row['riichi_action']['tiles'][0])
        self.assertTrue(any(y['name'] == 'Double Riichi' for w in row['riichi']['waits']
                            for v in w['variants'] for y in v['ron']['value']['yaku']))
        self.assertEqual(obs, original)
        self.assertEqual(game.debug_state(), before)
        self.assertIsInstance(choose_yaku_action(obs, legal), Discard)
        self.assertIsInstance(choose_action(obs, legal), Discard)
        action = choose_riichi_action(obs, legal)
        self.assertEqual(action.kind, 'riichi')
        game.submit(action, policy=VERSION)
        self.assertFalse(game.observe(0)['players'][0]['riichi'])
        self.assertEqual(game.observe(0)['players'][0]['score'], 25000)
        pass_all(game)
        self.assertTrue(game.observe(0)['players'][0]['riichi'])
        self.assertEqual(game.observe(0)['players'][0]['score'], 24000)

    def test_conservative_fallbacks_and_legal_win_priority(self):
        game = game_with(NO_YAKU)
        obs, legal = game.observe(0), game.legal_actions(0)
        for flag in ('temporary_furiten', 'riichi_furiten'):
            for value in (True, None):
                changed = deepcopy(obs)
                changed['own_status'][flag] = value
                self.assertFalse(analyze_riichi(changed, legal)['candidates'][0]['recommend_riichi'])
        threatened = deepcopy(obs)
        threatened['players'][1]['riichi'] = True
        self.assertFalse(analyze_riichi(threatened, legal)['candidates'][0]['recommend_riichi'])
        self.assertIsInstance(choose_riichi_action(obs, legal, 'fold'), Discard)
        yaku = game_with()
        self.assertTrue(all(not c['recommend_riichi'] for c in analyze_riichi(yaku.observe(0), yaku.legal_actions(0))['candidates']))
        win = game_with(NO_YAKU, '5m')
        self.assertEqual(choose_riichi_action(win.observe(0), win.legal_actions(0), 'fold').kind, 'tsumo')
        no_money = game_with(NO_YAKU)
        no_money._state.players[0].score = 900
        self.assertEqual(analyze_riichi(no_money.observe(0), no_money.legal_actions(0))['candidates'], [])

    def test_effective_draws_branch_inventory_and_furiten_timing(self):
        game = game_with(ONE_AWAY)
        obs = game.observe(0)
        obs['own_status']['temporary_furiten'] = True
        original, state = deepcopy(obs), game.debug_state()
        action = Discard(0, Tile.parse('9m'), True)
        result = analyze_progression(obs, [action])
        draws = result['candidates'][0]['draws']
        self.assertGreater(len(draws), 1)
        self.assertGreater(len({tuple(w['tile'] for w in d['branches'][0]['waits']) for d in draws}), 1)
        for draw in draws:
            self.assertGreater(draw['unseen'], 0)
            for branch in draw['branches']:
                self.assertEqual(branch['shanten'], 0)
                self.assertNotIn('temporary_furiten', branch['ron_restrictions'])
                for w in branch['waits']:
                    self.assertLessEqual(w['unseen'], 4)
                    self.assertGreaterEqual(w['unseen'], 0)
                    self.assertEqual(w['unseen'], sum(v['unseen'] for v in w['variants']))
        self.assertEqual(obs, original)
        self.assertEqual(game.debug_state(), state)
        changed = deepcopy(obs)
        changed['own_status']['riichi_furiten'] = True
        self.assertTrue(all('riichi_furiten' in b['ron_restrictions']
                            for d in analyze_progression(changed, [action])['candidates'][0]['draws'] for b in d['branches']))
        changed.pop('own_status')
        self.assertTrue(all(d['limited'] for d in analyze_progression(changed, [action])['candidates'][0]['draws']))

    def test_red_inventory_and_special_hands(self):
        for hand in ('1m 1m 2m 2m 4p 4p 6p 6p 8s 8s 1z 2z 3z',
                     '1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 2m', ONE_AWAY):
            game = game_with(hand, '3p')
            action = Discard(0, Tile.parse('3p'), True)
            result = analyze_progression(game.observe(0), [action])
            self.assertEqual(result['candidates'][0]['status'], 'applicable')
            self.assertTrue(result['candidates'][0]['draws'])
        game = game_with(ONE_AWAY)
        cut = Discard(0, Tile.parse('9m'), True)
        draws = analyze_progression(game.observe(0), [cut])['candidates'][0]['draws']
        normal, red = [next(d for d in draws if d['tile'] == code) for code in ('5m', '0m')]
        self.assertEqual((normal['unseen'], red['unseen']), (3, 1))
        # The projected five is visible, so no branch can regain all four fives.
        for draw in (normal, red):
            for branch in draw['branches']:
                for wait in branch['waits']:
                    if wait['tile'] == '5m':
                        self.assertLessEqual(wait['unseen'], 3)

    def test_hidden_information_isolation_and_rejection(self):
        game = game_with(NO_YAKU)
        legal, obs = game.legal_actions(0), game.observe(0)
        before = analyze_riichi(obs, legal)
        # Change only future wall order; no analysis is allowed to depend on it.
        game._state.wall[90], game._state.wall[91] = game._state.wall[91], game._state.wall[90]
        self.assertEqual(before, analyze_riichi(game.observe(0), legal))
        changed = deepcopy(obs)
        changed['seed'] = 123
        with self.assertRaises(ValueError):
            analyze_riichi(changed, legal)
        with self.assertRaises(ValueError):
            analyze_progression(changed, [a for a in legal if isinstance(a, Discard)])

    def test_exhausted_waits_claimed_tiles_and_declaration_ron(self):
        from test_tenpai import river
        game = game_with(NO_YAKU)
        obs = game.observe(0)
        river(obs, '5m', 1)
        obs['players'][1]['discards'][0]['claimed_by'] = 2
        obs['players'][2]['melds'] = [{'kind': 'pon', 'tiles': [asdict(t) for t in tiles('0m 5m 5m')],
                                     'source_discard_id': 'test'}]
        obs['dora_indicators'] = [asdict(Tile.parse('5m'))]
        row = analyze_riichi(obs, game.legal_actions(0))['candidates'][0]
        self.assertEqual(row['dama']['shape_unseen'], 0)
        self.assertFalse(row['recommend_riichi'])
        game = fixture({0: NO_YAKU, 1: '7z 7z 7z 1p 2p 3p 4s 5s 6s 2z 2z 7m 8m'}, '9m')
        game.submit(Decision(0, 'riichi', (Tile.parse('9m'),), True), policy=VERSION)
        self.assertIn(Decision(1, 'ron'), game.legal_actions(1))
        game.submit(Decision(1, 'ron'))
        pass_all(game)
        self.assertEqual(game.observe(0)['result']['kind'], 'ron')
        self.assertFalse(game.observe(0)['players'][0]['riichi'])
        self.assertEqual(game.observe(0)['riichi_sticks'], 0)

    def test_projection_open_melds_kans_and_claimed_inventory(self):
        for kind, meld in (('chi', '7m 8m 9m'), ('closed_kan', '9m 9m 9m 9m')):
            obs = game_with().observe(0)
            own = obs['players'][0]
            own['hand']['known_tiles'] = [asdict(t) for t in tiles('1p 2p 3p 1s 2s 7z 7z 4s 5s 8p')]
            own['hand']['drawn_tile'] = {'tile': asdict(Tile.parse('9p'))}
            own['melds'] = [{'kind': kind, 'tiles': [asdict(t) for t in tiles(meld)], 'source_discard_id': None}]
            result = analyze_progression(obs, [Discard(0, Tile.parse('9p'), True)])
            self.assertEqual(result['candidates'][0]['status'], 'applicable')
            self.assertTrue(result['candidates'][0]['draws'])

    def test_records_new_and_v6_undo_branch(self):
        game = game_with(NO_YAKU)
        action = choose_riichi_action(game.observe(0), game.legal_actions(0))
        game.submit(action, policy=VERSION)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'game.json'
            game.save(path)
            saved = Game.load(path)
            self.assertEqual(saved.debug_decisions(), game.debug_decisions())
            self.assertEqual(saved.replay(1).debug_decisions(), game.debug_decisions())
            saved.undo()
            self.assertFalse(saved.debug_decisions())
            saved.submit(Discard(0, action.tiles[0], action.is_tsumogiri))
            self.assertNotEqual(saved.debug_decisions()[0]['actual_action'], game.debug_decisions()[0]['actual_action'])
            data = json.loads(path.read_text(encoding='utf-8'))
            data['version'] = 6
            for r in data['reviews'] + data['decision_records']:
                r['observation'].pop('riichi_context', None)
                r.pop('riichi', None)
            data['decision_records'][0]['policy'] = 'manual'
            path.write_text(json.dumps(data), encoding='utf-8')
            old = Game.load(path)
            self.assertNotIn('riichi', old.debug_decisions()[0])
            old.save(path)
            self.assertEqual(Game.load(path).debug_decisions(), old.debug_decisions())

    def test_complete_single_hand_new_strategy(self):
        game = game_with(NO_YAKU)
        policies = set()
        for _ in range(350):
            view = game.observe(0)
            if view['result']:
                break
            seat = view['actor']
            action = choose_riichi_action(game.observe(seat), game.legal_actions(seat))
            if isinstance(action, Decision):
                policies.add(action.kind)
            game.submit(action, policy=VERSION)
        self.assertIsNotNone(game.observe(0)['result'])
        self.assertIn('riichi', policies)
        self.assertFalse(game.paused)
        self.assertEqual(game.replay(game.action_count).debug_state(), game.debug_state())


if __name__ == '__main__':
    unittest.main()
