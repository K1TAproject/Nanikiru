import tkinter as tk
import unittest
from unittest.mock import patch

from nanikiru import Decision, Discard, Game, Tile
from nanikiru.game_ui import GameWindow
from test_efficiency import game_with
from test_developer_preview import NO_YAKU, ONE_AWAY
from test_rules import fixture, discard_draw


class PreviewUITests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = GameWindow(self.root, Game(4))
        self.ui.bots_enabled.set(False)
        self.root.update_idletasks()

    def tearDown(self):
        self.root.after_cancel(self.ui.poll_id)
        self.root.destroy()

    def test_auto_pass_single_step_source_and_guards(self):
        self.assertTrue(self.ui.auto_pass.get())
        discard_draw(self.ui.game)
        self.ui.refresh()
        self.assertEqual(self.ui.game.legal_actions(1), [Decision(1, 'pass')])
        for attribute, value in [('testing', True), ('preview', 0), ('lesson_pending', (id(self.ui.game), 1))]:
            previous = getattr(self.ui, attribute)
            setattr(self.ui, attribute, value)
            self.ui.advance_bot()
            self.assertEqual(self.ui.game.action_count, 1)
            setattr(self.ui, attribute, previous)
        self.ui.game.stop()
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.game.resume()
        self.ui.auto_pass.set(False)
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.auto_pass.set(True)
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 2)
        self.assertEqual(self.ui.game.decision_records(1)[0]['policy'], 'ui-auto-pass-v1')
        self.assertEqual(self.ui.game.decision_records(1)[0]['actual_action']['kind'], 'pass')

    def test_any_special_response_preserves_human_choice(self):
        self.ui.game = fixture({1: '7z 7z 7z 1p 2p 3p 4s 5s 6s 2z 2z 7m 8m'}, '9m')
        discard_draw(self.ui.game)
        self.ui.refresh()
        self.assertIn(Decision(1, 'ron'), self.ui.game.legal_actions(1))
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        # Having a call rather than a win must also keep the human's choice.
        self.ui.game = fixture({1: '1m 2m 4p 5p 6p 7s 8s 9s 1z 1z 3z 4z 5z'}, '3m')
        discard_draw(self.ui.game)
        self.ui.refresh()
        self.assertTrue(any(a.kind == 'chi' for a in self.ui.game.legal_actions(1)))
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)

    def test_live_comparison_lesson_feedback_and_history(self):
        self.ui.game = game_with(NO_YAKU)
        self.ui.refresh()
        self.ui.open_preview()
        panel = self.ui.preview_panel
        self.assertIn('默听普通荣和全部无役', panel.text.get('1.0', 'end'))
        self.assertEqual(self.ui.game.action_count, 0)
        self.ui.lesson_enabled.set(True)
        self.ui.special.set(next(k for k, a in self.ui.decision_options.items() if a.kind == 'riichi' and a.is_tsumogiri))
        self.ui.submit_special()
        self.ui.open_preview()
        panel.show_riichi()
        self.assertIn('历史行动前', panel.text.get('1.0', 'end'))
        feedback = self.ui.feedback_text.get('1.0', 'end')
        self.ui.open_preview_history()
        self.assertEqual(panel.record_box.current(), self.ui.comparison_panel.record_box.current())
        self.assertEqual(panel.choices[panel.cut_box.current()],
                         self.ui.comparison_panel.choices[self.ui.comparison_panel.candidate_box.current()])
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.continue_lesson()
        self.ui.advance_bot()
        self.assertEqual(self.ui.feedback_text.get('1.0', 'end'), feedback)
        self.ui.preview = 0
        self.ui.refresh()
        self.assertFalse(panel.records)
        self.assertIsNone(panel.live)

    def test_side_hands_single_column_and_identity(self):
        calls = []
        original = self.ui.card
        def capture(x, y, tile, w, h, key=None, selected=False):
            if x in (1115, 45) and 207 <= y <= 665:
                calls.append((x, y, tile))
            return original(x, y, tile, w, h, key, selected)
        self.ui.view.set('全知调试')
        self.ui.refresh()
        with patch.object(self.ui, 'card', side_effect=capture):
            self.ui.draw_table()
        for x in (1115, 45):
            hand = [c for c in calls if c[0] == x]
            self.assertEqual(len(hand), 13)
            self.assertEqual(len({c[1] for c in hand}), 13)

    def test_old_worker_results_do_not_replace_branch_or_view(self):
        self.ui.game = game_with(ONE_AWAY)
        discard_draw(self.ui.game)
        self.ui.refresh()
        panel = self.ui.preview_panel
        token = panel.token
        self.ui.view.set('玩家 1')
        self.ui.refresh()
        message = panel.text.get('1.0', 'end')
        panel.queue.put((token, 'old', 'stale error'))
        panel.collect()
        self.assertEqual(panel.text.get('1.0', 'end'), message)
        self.assertFalse(panel.cache)


if __name__ == '__main__':
    unittest.main()
