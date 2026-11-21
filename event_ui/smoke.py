"""Desktop smoke check: python -m event_ui.smoke. No changes to test_ui."""

from pathlib import Path
import tempfile
import tkinter as tk

from nanikiru.models import UNKNOWN
from .app import EventApp


def main():
    root = tk.Tk()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(args)
    app = EventApp(root)
    try:
        for geometry in ("1260x900", "1180x760"):
            root.geometry(geometry)
            root.update()
            assert len(app.hand_buttons) == 14
            for frame in (app.hand_frame, app.draw_frame, app.indicator_frame, app.history, app.rivers_frame.master):
                assert frame.winfo_ismapped()
                assert frame.winfo_rootx()+frame.winfo_width() <= root.winfo_rootx()+root.winfo_width()
        app.hand_buttons["entry-draw"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert "6 枚" in app.analysis_text.get()
        old_version = app.analysis_version
        app.discard_button.invoke()
        root.update()
        assert app.session.position.phase == "await_response"
        assert len(app.hand_buttons) == 13
        assert str(app.draw_button.cget("state")) == "disabled"
        assert old_version != app.analysis_version
        app.close_button.invoke(); root.update()
        assert app.session.position.actor == 1
        assert str(app.code_box.cget("state")) == "disabled"
        for seat, code in ((1, "8p"), (2, "8s"), (3, "8m")):
            app.draw_button.invoke(); root.update()
            assert app.session.record.events[-1].data["code"] is UNKNOWN
            app.code_input.set(code)
            app.discard_button.invoke(); root.update()
            assert app.session.position.pending["source_seat"] == seat
            app.close_button.invoke(); root.update()
        app.code_input.set("6s")
        app.draw_button.invoke(); root.update()
        assert len(app.hand_buttons) == 14
        assert len([t for t in app.position.tiles if t.zone == "river"]) == 4
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as folder:
            path = Path(folder) / "round.json"
            app.path_input.set(str(path))
            app.save()
            saved = app.session.record
            app.history.selection_set("1"); root.update()
            assert app.cursor == 1
            assert str(app.discard_button.cget("state")) == "disabled"
            app.perform(app.draw)
            assert "回放" in app.message.get()
            assert app.session.record == saved
            app.latest(); root.update()
            version = app.session.state_version
            app.undo_button.invoke(); root.update()
            assert app.session.position.phase == "await_draw"
            assert app.session.state_version != version
            app.load(); root.update()
            assert app.session.record == saved
            app.halt_button.invoke(); root.update()
            assert app.session.position.phase is UNKNOWN
            assert str(app.draw_button.cget("state")) == "disabled"
            app.undo_button.invoke(); root.update()
            assert app.session.record == saved
        app.entry_mode.set("中途")
        app.entry_phase.set("待摸牌")
        app.entry_actor.set("2")
        app.river_inputs[1].set("9p 9s")
        app.new_record(); root.update()
        assert app.session.position.actor == 2
        assert not app.session.record.coverage["from_start"]
        assert not app.session.position.completeness["public_tiles"]
        assert not errors, errors
        print("Event UI smoke passed: sizes, full turn cycle, response gating, save/load, read-only replay, undo, halt, midgame entry.")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
