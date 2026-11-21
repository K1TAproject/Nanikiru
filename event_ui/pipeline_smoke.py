"""Analysis input/output UI checks: python -m event_ui.pipeline_smoke."""

import json
import tkinter as tk
from unittest.mock import patch

from nanikiru.analysis.output import debug_json
from .app import EventApp


def main():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = EventApp(root)
    panel = app.analysis_panel
    try:
        root.geometry("1180x760"); root.update()
        panel.method.set('fixed-v2'); panel.method_options.set('{"risk":12}')
        panel.run_button.invoke(); root.update()
        assert app.last_request.method_options == {'risk':12}
        assert app.last_result.method == 'fixed-v2'
        panel.method_options.set('{bad'); panel.run_button.invoke(); root.update()
        assert app.last_result is None
        panel.method_options.set('{}'); panel.method.set('minimal-shape-v1')
        panel.run_button.invoke(); root.update()
        assert app.message_label.winfo_ismapped() and app.message_label.winfo_height() > 10
        assert app.message_label.winfo_rooty()+app.message_label.winfo_height() <= root.winfo_rooty()+root.winfo_height()
        assert app.last_request.state_version == app.analysis_version
        assert panel.input_json.get("1.0", "end-1c") == debug_json(app.last_request)
        assert panel.output_json.get("1.0", "end-1c") == debug_json(app.last_result)
        with patch("event_ui.app.analyze", side_effect=AssertionError("Selection must not recompute")):
            app.select("h0")
            app.select("entry-draw")
        old = app.last_result
        app.discard_button.invoke(); root.update()
        assert len(app.last_request.history) == 1
        current = app.last_result
        try:
            app.present_result(old)
            raise AssertionError("stale result accepted")
        except ValueError:
            pass
        assert app.last_result is current
        panel.auto.set(False)
        app.close_button.invoke(); root.update()
        assert app.last_request is None and app.last_result is None
        assert "尚未" in panel.output_json.get("1.0", "end")
        panel.run_button.invoke(); root.update()
        assert len(app.last_request.history) == 2
        app.history.selection_set("1"); root.update()
        assert app.last_result is None
        panel.run_button.invoke(); root.update()
        payload = json.loads(panel.input_json.get("1.0", "end"))
        assert len(payload["history"]) == 1
        assert payload["history"][0]["kind"] == "discard"

        panel.method.set("basic-v1"); app.analysis_controls_changed()
        panel.run_button.invoke(); root.update()
        assert app.last_result is None
        assert "error" in json.loads(panel.output_json.get("1.0", "end"))
        panel.tasks["recommend"].set(False); app.analysis_controls_changed()
        panel.run_button.invoke(); root.update()
        assert app.last_result.method == "basic-v1"
        assert "discard_ranking" not in app.last_result.situation

        panel.expanded.set(True); panel.toggle_details(); root.update()
        for widget in (panel.input_json, panel.output_json, app.hand_frame, app.history, app.message_label):
            assert widget.winfo_ismapped()
            assert widget.winfo_rootx()+widget.winfo_width() <= root.winfo_rootx()+root.winfo_width()
            assert widget.winfo_rooty()+widget.winfo_height() <= root.winfo_rooty()+root.winfo_height()
        panel.input_json.yview_moveto(1); panel.output_json.yview_moveto(1); root.update()
        panel.expanded.set(False); panel.toggle_details(); root.update()
        assert not panel.details.winfo_ismapped()
        panel.method.set("minimal-shape-v1"); panel.tasks["recommend"].set(True); panel.auto.set(True)
        app.entry_mode.set("中途"); app.new_record(); root.update()
        assert app.last_result.situation["discard_ranking"].details["criteria"] == ("shanten",)
        assert "仅比较向听" in app.analysis_text.get()
        assert not errors, errors
        print("Analysis UI smoke passed: exact JSON, no selection recompute, stale guard, manual/auto, prefix, methods/tasks, details and unknown coverage.")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
