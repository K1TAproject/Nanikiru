"""Exercise asynchronous UI controls on a desktop without network or training."""

import time
import tkinter as tk

from nanikiru.rules.config import tenhou_hanchan
from .app import TableApp


def main():
    root=tk.Tk();errors=[]
    root.report_callback_exception=lambda *args:errors.append(str(args))
    app=TableApp(root)
    def until(predicate,timeout=45):
        end=time.monotonic()+timeout
        while not predicate():
            root.update()
            if errors:raise AssertionError(errors)
            if time.monotonic()>end:raise AssertionError('UI timeout: '+app.message.get())
            time.sleep(.01)
        root.update()
    try:
        until(lambda:app.last_result is not None)
        assert app.main_turn.request.position.rules==tenhou_hanchan()
        assert app.canvas.winfo_ismapped() and app.summary.winfo_ismapped()
        for geometry in ('1100x760','1440x920'):
            root.geometry(geometry);root.update()
            assert len(app.hitboxes)==14
            assert app.canvas.winfo_width()>500
            assert app.footer.winfo_ismapped() and app.footer.winfo_y()+app.footer.winfo_height()<=root.winfo_height()
            assert app.execute_button.winfo_height()>=25
            assert all(0<=x1<x2<=app.canvas.winfo_width() and 0<=y1<y2<=app.canvas.winfo_height() for x1,y1,x2,y2,_ in app.hitboxes)
        x1,y1,x2,y2,tile=app.hitboxes[0]
        app.canvas.event_generate('<Button-1>',x=int((x1+x2)/2),y=int((y1+y2)/2));root.update()
        assert app.selected==tile
        app.execute_button.invoke();until(lambda:app.last_result is not None)
        assert app.session.game.command_count==1
        app.undo();until(lambda:app.last_result is not None)
        assert app.session.game.command_count==0
        for mode in app.modes:mode.set('v1 牌效')
        app.advisor.set('v1 牌效');app.change_settings()
        until(lambda:app.pending is not None)
        app.step();until(lambda:app.session.game.command_count==1 and app.pending is not None)
        app.toggle_auto();until(lambda:app.session.game.command_count>=4)
        app.pause();count=app.session.game.command_count
        until(lambda:app.last_result is not None)
        end=time.monotonic()+1
        while time.monotonic()<end:root.update();time.sleep(.01)
        assert app.session.game.command_count==count
        app.observer.set(2);app.change_settings();until(lambda:app.last_result is not None)
        assert app.main_turn.request.position.observer_seat==2
        assert not errors
        print('Table UI smoke passed: resize, tile selection, manual discard, undo, bot step, auto/pause, observer isolation.')
    finally:app.close()


if __name__=='__main__':main()
