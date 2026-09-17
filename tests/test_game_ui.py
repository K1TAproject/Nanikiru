import tkinter as tk
import unittest
from unittest.mock import patch

from nanikiru.game import Game
from nanikiru.game_ui import GameWindow


class GameWindowTests(unittest.TestCase):
    def test_attack_fold_controls_preserve_recorded_mode(self):
        self.ui.human_mode.set("弃和")
        before = self.ui.game.action_count
        self.assertEqual(self.ui.game.action_count, before)
        tile = self.ui.data["players"][0]["hand"]["drawn_tile"]["tile"]
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        record = self.ui.game.decision_records(0)[0]
        self.assertEqual(record["defense"]["mode"], "fold")
        self.ui.human_mode.set("进攻")
        self.ui.refresh()
        self.assertEqual(self.ui.game.decision_records(0)[0], record)
        self.assertIn("弃和", self.ui.decision_text.get("1.0", "end"))
        self.ui.bot_strategy.set("基础牌效")
        self.ui.update_mode_controls()
        self.assertEqual(str(self.ui.bot_mode_box.cget("state")), "disabled")
        self.assertIn("不应用", self.ui.mode_hint.cget("text"))
        self.ui.bot_strategy.set("役种感知")
        self.ui.update_mode_controls()
        self.ui.bot_mode.set("弃和")
        self.ui.bots_enabled.set(True)
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.decision_records(1)[0]["defense"]["mode"], "fold")
        self.assertEqual(self.ui.view.get(), "玩家 0")

    def test_yaku_strategy_and_private_decision_replay(self):
        from test_rules import fixture, discard_draw
        from test_decision import PON_HAND
        self.ui.game = fixture({1: PON_HAND}, "5z")
        discard_draw(self.ui.game)
        self.ui.bots_enabled.set(True)
        self.ui.bot_strategy.set("役种感知")
        self.ui.refresh()
        self.ui.advance_bot()
        record = self.ui.game.decision_records(1)[0]
        self.assertEqual(record["actual_action"]["kind"], "pon")
        self.assertEqual(len(self.ui.visible_decisions), 1)  # Only human seat's discard.
        self.ui.view.set("全知调试")
        self.ui.refresh()
        self.assertEqual(len(self.ui.visible_decisions), 2)
        self.assertIn("鸣牌后", self.ui.decision_text.get("1.0", "end"))
        self.ui.view.set("玩家 2")
        self.ui.refresh()
        self.assertEqual(self.ui.visible_decisions, [])
        self.assertNotIn("鸣牌后", self.ui.decision_text.get("1.0", "end"))
        self.ui.history.set("0")
        self.ui.show_history()
        self.assertEqual(self.ui.visible_decisions, [])

    def test_combobox_popup_does_not_break_bot_poll(self):
        from test_rules import discard_draw
        discard_draw(self.ui.game)
        self.ui.bots_enabled.set(True)
        self.ui.refresh()
        self.root.deiconify()
        self.root.update_idletasks()
        box = self.ui.special_box
        box["values"] = ("测试菜单",)
        self.root.tk.call("ttk::combobox::Post", box._w)
        popup = self.root.tk.call("ttk::combobox::PopdownWindow", box._w)
        self.root.update_idletasks()
        self.root.tk.call("grab", "set", popup)
        try:
            self.assertTrue(self.root.tk.call("grab", "current", self.root._w))
            self.root.after_cancel(self.ui.poll_id)
            self.ui.next_bot_time = 0
            self.ui.poll()
            self.assertEqual(self.ui.game.action_count, 1)
            self.assertIn(self.ui.poll_id, self.root.tk.call("after", "info"))
        finally:
            self.root.tk.call("ttk::combobox::Unpost", box._w)
        self.root.after_cancel(self.ui.poll_id)
        self.ui.next_bot_time = 0
        self.ui.poll()
        self.assertEqual(self.ui.game.action_count, 2)

    def test_default_and_loaded_view_use_actual_dealer(self):
        import tempfile
        from pathlib import Path
        popup = tk.Toplevel(self.root)
        window = GameWindow(popup, Game(4, dealer=3))
        try:
            self.assertTrue(window.bots_enabled.get())
            self.assertEqual(window.human_seat, 3)
            self.assertEqual(window.view.get(), "玩家 3")
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "dealer.json"
                Game(9, dealer=1).save(path)
                with patch("nanikiru.game_ui.filedialog.askopenfilename", return_value=str(path)):
                    window.load()
                self.assertEqual(window.human_seat, 1)
                self.assertEqual(window.view.get(), "玩家 1")
                self.assertTrue(window.game.paused)
                window.view.set("玩家 2")
                window.refresh()
                window.follow_actor()
                self.assertEqual(window.view.get(), "玩家 1")
        finally:
            popup.after_cancel(window.poll_id)
            popup.destroy()

    def test_bots_keep_dealer_view_and_stop_for_human_preview_pause(self):
        from nanikiru import Discard
        self.ui.game = Game(4, dealer=2)
        self.ui.human_seat = 2
        self.ui.view.set("玩家 2")
        self.ui.bots_enabled.set(True)
        self.ui.refresh()
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 0)
        action = next(a for a in self.ui.game.legal_actions(2) if isinstance(a, Discard))
        self.ui.game.submit(action)
        self.ui.refresh()
        self.ui.preview = 0
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.preview = None
        self.ui.game.stop()
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.game.resume()
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, 2)
        self.assertEqual(self.ui.view.get(), "玩家 2")
        for _ in range(10):
            self.ui.advance_bot()
        self.assertEqual(self.ui.data["actor"], 2)
        self.assertEqual(self.ui.data["phase"], "await_response")
        count = self.ui.game.action_count
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, count)
        self.ui.undo()
        self.assertTrue(self.ui.game.paused)
        self.ui.advance_bot()
        self.assertEqual(self.ui.game.action_count, count - 1)

    def test_review_feedback_privacy_and_history(self):
        self.assertEqual(self.ui.visible_reviews, [])
        tile = self.ui.data["players"][0]["hand"]["drawn_tile"]["tile"]
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        self.assertIn("切后反馈", self.ui.notice.get())
        self.assertEqual(len(self.ui.visible_reviews), 1)
        rows = self.ui.review_table.get_children()
        self.assertTrue(rows)
        self.assertEqual(sum("chosen" in self.ui.review_table.item(row, "tags") for row in rows), 1)
        self.ui.view.set("玩家 1")
        self.ui.refresh()
        self.assertEqual(self.ui.visible_reviews, [])
        self.assertEqual(self.ui.review_table.get_children(), ())
        self.assertNotIn("行动前暗手", self.ui.review_description.get())
        self.ui.view.set("全知调试")
        self.ui.refresh()
        self.assertEqual(len(self.ui.visible_reviews), 1)
        self.ui.history.set("0")
        self.ui.show_history()
        self.assertEqual(self.ui.visible_reviews, [])
        self.ui.live()
        self.assertEqual(len(self.ui.visible_reviews), 1)
        self.ui.undo()
        self.assertEqual(self.ui.visible_reviews, [])

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.ui = GameWindow(self.root, Game(4))
        self.ui.bots_enabled.set(False)
        self.ui.refresh()
        self.root.update_idletasks()

    def tearDown(self):
        self.root.after_cancel(self.ui.poll_id)
        self.root.destroy()

    def test_views_clear_private_data_and_do_not_advance(self):
        original = self.ui.game.debug_state()
        for name in ["全知调试", "玩家 0", "玩家 1", "玩家 2", "玩家 3"]:
            self.ui.view.set(name)
            self.ui.refresh()
            if name != "全知调试":
                self.assertNotIn('"remaining_wall"', self.ui.json_text.get("1.0", "end"))
                self.assertNotIn('"seed"', self.ui.json_text.get("1.0", "end"))
            else:
                self.assertIn("全知调试", self.ui.banner.cget("text"))
            self.assertEqual(self.ui.game.debug_state(), original)

    def test_select_submit_replay_pause_and_special(self):
        tile = self.ui.data["players"][0]["hand"]["drawn_tile"]["tile"]
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        self.assertEqual(self.ui.game.action_count, 1)
        self.assertEqual(self.ui.data["actor"], 1)
        self.ui.history.set("0")
        self.ui.show_history()
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        self.assertEqual(self.ui.game.action_count, 1)
        self.ui.live()
        self.ui.follow_actor()
        self.ui.pause()
        self.ui.submit_special()
        self.assertIn("暂停", self.ui.notice.get())
        self.ui.pause()
        self.ui.special.set("无可用动作")
        self.ui.submit_special()
        self.assertIn("没有可执行", self.ui.notice.get())
        self.ui.undo()
        self.assertEqual(self.ui.game.action_count, 0)

    def test_test_runner_output(self):
        # Worker is made synchronous for a deterministic UI check; it must not mutate the game.
        class ImmediateThread:
            def __init__(self, target, **kwargs):
                self.target = target
            def start(self):
                self.target()
        before = self.ui.game.debug_state()
        with patch("nanikiru.game_ui.Thread", ImmediateThread), patch("nanikiru.game_ui.subprocess.run") as run:
            run.return_value.stdout, run.return_value.stderr, run.return_value.returncode = "PASS", "", 0
            self.ui.run_tests()
        self.root.after_cancel(self.ui.poll_id)
        self.ui.poll()
        self.assertIn("PASS", self.ui.test_text.get("1.0", "end"))
        self.assertFalse(self.ui.testing)
        self.assertEqual(self.ui.game.debug_state(), before)

    def test_small_window_keeps_action_and_replay_controls(self):
        self.root.geometry("960x760")
        self.root.update_idletasks()
        for widget in (self.ui.history, self.ui.submit_button, self.ui.test_button):
            y = widget.winfo_rooty() - self.root.winfo_rooty()
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(y + widget.winfo_height(), self.root.winfo_height())

    def test_sort_preserves_state_draw_slot_and_selected_tile(self):
        before = self.ui.game.debug_state()
        player = self.ui.data["players"][0]
        entries = self.ui.hand_entries(player)
        order = [("mpsz".index(t["suit"]), t["rank"], not t["is_red"])
                 for t, drawn, _ in entries if not drawn]
        self.assertEqual(order, sorted(order))
        self.assertTrue(entries[-1][1])
        self.assertEqual(entries[-1][0], player["hand"]["drawn_tile"]["tile"])
        tile, drawn, index = entries[0]
        self.assertEqual(tile, player["hand"]["known_tiles"][index])
        self.assertEqual(before, self.ui.game.debug_state())
        self.ui.select_tile((0, index, drawn), tile)
        self.ui.submit()
        from dataclasses import asdict
        self.assertEqual(asdict(self.ui.game.debug_state().players[0].discards[-1].tile), tile)
        self.ui.sort_tiles.set(False)
        self.ui.refresh()
        player = self.ui.data["players"][0]
        self.assertEqual([t for t, drawn, _ in self.ui.hand_entries(player) if not drawn],
                         player["hand"]["known_tiles"])

    def test_sort_red_five_and_hidden_tiles(self):
        from dataclasses import asdict
        from nanikiru import Tile
        p = {"hand": {"known_tiles": [asdict(Tile.parse(t)) for t in ("5m", "1z", "0m", "2s", "1p")],
                      "unknown_count": 0, "drawn_tile": {"tile": asdict(Tile.parse("1m"))}}}
        entries = self.ui.hand_entries(p)
        self.assertEqual([str(Tile(**t)) for t, _, _ in entries], ["0m", "5m", "1p", "2s", "1z", "1m"])
        for entry in self.ui.hand_entries(self.ui.data["players"][1]):
            self.assertIsNone(entry[0])

    def test_manual_responses_and_random_start(self):
        tile = self.ui.data["players"][0]["hand"]["drawn_tile"]["tile"]
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        self.assertEqual(self.ui.data["phase"], "await_response")
        for seat in (1, 2, 3):
            self.ui.follow_actor()
            self.assertIn("过", self.ui.decision_options)
            self.ui.special.set("过")
            self.ui.submit_special()
        self.assertEqual(self.ui.data["phase"], "await_discard")
        self.assertEqual(self.ui.data["remaining_draws"], 68)
        old = self.ui.game.debug_view()["seed"]
        self.ui.aka_enabled.set(False)
        self.ui.round_wind.set("西")
        with patch.object(self.ui, "replace_ok", return_value=True):
            self.ui.random_game()
        self.assertNotEqual(self.ui.game.debug_view()["seed"], old)
        self.assertEqual(self.ui.data["round_wind"], "west")
        self.assertFalse(self.ui.game.rules.aka_dora_enabled)

    def test_call_and_result_through_ui(self):
        from test_rules import fixture, WAIT
        self.ui.game = fixture({1: "1m 1m 1m"}, "1m")
        self.ui.view.set("全知调试")
        self.ui.refresh()
        tile = self.ui.data["players"][0]["hand"]["drawn_tile"]["tile"]
        self.ui.select_tile((0, 13, True), tile)
        self.ui.submit()
        label = next(k for k, a in self.ui.decision_options.items() if a.kind == "open_kan")
        self.ui.special.set(label)
        self.ui.submit_special()
        for _ in range(2):
            self.ui.special.set("过")
            self.ui.submit_special()
        self.assertEqual(self.ui.data["players"][1]["melds"][0]["kind"], "open_kan")
        self.ui.game = fixture({0: WAIT}, "6s")
        self.ui.refresh()
        self.ui.special.set("自摸")
        self.ui.submit_special()
        self.assertEqual(self.ui.data["result"]["kind"], "tsumo")
        self.assertIn("48000", self.ui.result_text.get("1.0", "end"))
        self.assertEqual(self.ui.decision_options, {})
