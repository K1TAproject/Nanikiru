"""Widget integration checks; root stays hidden and dialogs are mocked."""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from examples.record_round import sample_snapshot
from nanikiru import Discard, Recorder, Tile
from nanikiru.debug_ui import DebugWindow


class DirectLaunchTests(unittest.TestCase):
    def test_direct_script_from_package_directory(self):
        # Execute the script's __main__ branch, replacing only the blocking UI loop.
        code = (
            "import runpy, sys, tkinter; "
            "tkinter.Tk.mainloop = lambda root: (root.withdraw(), root.update_idletasks(), "
            "print(root.title()), root.destroy()); "
            "runpy.run_path(sys.argv[1], run_name='__main__')"
        )
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        env.pop("PYTHONPATH", None)
        for name in ("debug_ui.py", "game_ui.py"):
            with self.subTest(script=name):
                script = Path(__file__).resolve().parents[1] / "nanikiru" / name
                result = subprocess.run([sys.executable, "-I", "-X", "utf8", "-c", code, str(script)],
                                        cwd=script.parent, env=env, capture_output=True,
                                        text=True, encoding="utf-8", timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Nanikiru", result.stdout)


class DebugWindowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk display unavailable: {exc}")
        self.root.withdraw()
        self.ui = DebugWindow(self.root, Recorder(sample_snapshot()[0]))
        self.root.update_idletasks()

    def tearDown(self):
        if hasattr(self, "ui"):
            self.root.after_cancel(self.ui.poll_id)
            self.root.destroy()

    def test_submit_preview_and_rejected_special_action(self):
        self.ui.submit()
        self.assertEqual(len(self.ui.recorder.events), 1)
        self.assertIn("待响应", self.ui.status.cget("text"))
        self.ui.history.current(0)
        self.ui.show_step()
        self.ui.submit()
        self.assertEqual(len(self.ui.recorder.events), 1)
        self.ui.live()
        self.ui.action.set("立直")
        self.ui.submit()
        self.assertEqual(len(self.ui.recorder.events), 1)
        self.assertIn("尚未实现", self.ui.log_text.get("1.0", "end"))
        self.ui.undo()
        self.assertEqual(len(self.ui.recorder.events), 0)

    def test_save_load_pause_and_error_keeps_record(self):
        self.ui.submit()
        self.ui.pause()
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "record.json")
            with patch("nanikiru.debug_ui.filedialog.asksaveasfilename", return_value=path):
                self.ui.save()
            self.assertFalse(self.ui.dirty)
            with patch("nanikiru.debug_ui.filedialog.askopenfilename", return_value=path):
                self.ui.load()
            self.assertTrue(self.ui.recorder.paused)
            before = self.ui.recorder.state
            Path(path).write_text("broken", encoding="utf-8")
            with patch("nanikiru.debug_ui.filedialog.askopenfilename", return_value=path):
                self.ui.load()
            self.assertEqual(before, self.ui.recorder.state)

    def test_registered_builder_uses_core_validation(self):
        self.ui.register_action("测试弃牌", lambda p, t, mode: Discard(p, Tile.parse(t), mode))
        self.ui.action.set("测试弃牌")
        self.ui.tile.set("1m")
        self.ui.submit()
        self.assertEqual(len(self.ui.recorder.events), 0)
        self.ui.tile.set("5z")
        self.ui.submit()
        self.assertEqual(len(self.ui.recorder.events), 1)


if __name__ == "__main__":
    unittest.main()
