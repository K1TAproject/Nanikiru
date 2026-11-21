"""Real Tk interaction/layout checks; requires a desktop, closes when done.

Run separately from headless tests: python -m test_ui.smoke
"""

import random
import tkinter as tk

from .app import PositionApp
from .positions import make_position, random_position
from .tiles import TileButton, tile_name


def main():
    root = tk.Tk()
    callback_errors = []
    root.report_callback_exception = lambda *args: callback_errors.append(args)
    app = PositionApp(root, make_position(
        "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s".split(), "9m", "6s"))
    try:
        for geometry in ("1160x820", "1100x650"):
            root.geometry(geometry)
            root.update()
            hand_buttons = list(app.hand_frame.winfo_children())
            assert len(hand_buttons) == 13
            assert len({w.winfo_rooty() for w in hand_buttons}) == 1
            assert hand_buttons[-1].winfo_rootx() < app.draw_frame.winfo_rootx()
            assert app.draw_frame.winfo_rootx() < app.indicator_frame.winfo_rootx()
            for frame in (app.hand_frame, app.draw_frame, app.indicator_frame,
                          app.effective_frame.master, app.recommendation_frame.master):
                assert frame.winfo_ismapped()
                assert frame.winfo_rootx() + frame.winfo_width() <= root.winfo_rootx() + root.winfo_width()
            for tile_id, button in app.tile_buttons.items():
                button.event_generate("<Button-1>", x=20, y=20)
                root.update()
                assert app.selected_id == tile_id
                assert tile_name(app.by_id[tile_id].code) in app.selection_text.get()
            assert "0 向听" in app.selection_text.get()
            assert "6 枚" in app.selection_text.get()
            recommendation = next(w for w in app.recommendation_frame.winfo_children()[0].winfo_children()
                                  if isinstance(w, TileButton))
            recommendation.event_generate("<Button-1>", x=20, y=20)
            root.update()
            assert app.selected_id == "draw"
        app.new_position(random_position(random.Random(9)))
        root.update()
        assert len(app.tile_buttons) == 14
        assert app.selected_id == "draw"
        app.example()
        app.editing.set(True)
        app.toggle_editor()
        root.update()
        assert app.editor.winfo_ismapped()
        assert len(app.palette_buttons) == 37
        app.indicator_button.event_generate("<Button-1>", x=20, y=20)
        root.update()
        app.palette_buttons["9p"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert "7 枚" in app.selection_text.get()
        assert "7 枚" in app.recommendation_text.get()
        app.tile_buttons["h0"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        app.palette_buttons["0p"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert app.by_id["h0"].code == "0p"
        assert app.selected_id == "h0"
        app.select("h1")
        root.update()
        assert not app.palette_buttons["0p"].enabled
        previous = app.position
        app.palette_buttons["0p"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert app.position is previous
        app.indicator_button.event_generate("<Button-1>", x=20, y=20)
        root.update()
        app.palette_buttons["9p"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert app.by_id["indicator"].code == "9p"
        app.select("draw")
        app.palette_buttons["8p"].event_generate("<Button-1>", x=20, y=20)
        root.update()
        assert app.by_id["draw"].code == "8p"
        assert app.hand_frame.winfo_ismapped() and app.recommendation_frame.winfo_ismapped()
        app.editing.set(False)
        app.toggle_editor()
        root.update()
        assert not app.editor.winfo_ismapped()
        assert not callback_errors, callback_errors
        print("Tk smoke passed: two sizes, 14 tile clicks, recommendations, random reset, manual hand/draw/indicator edits, inventory guard.")
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
