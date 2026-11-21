"""Run on an unlocked desktop; checks controls and retained single-window layout."""

from pathlib import Path
import tempfile
import tkinter as tk

from nanikiru.analysis.output import debug_json
from .app import SimulatorApp


def main():
    root=tk.Tk(); errors=[]
    root.report_callback_exception=lambda *args:errors.append(args)
    app=SimulatorApp(root)
    try:
        root.update()
        app.method.set('fixed-v2'); app.method_options.set('{"risk":12}')
        app.refresh(); root.update()
        assert app.last_request.method_options == {'risk':12}
        assert app.last_result.method == 'fixed-v2' and app.last_result.recommendations
        assert '危险指数' in app.summary.get()
        app.method_options.set('{bad'); app.refresh(); root.update()
        assert app.last_result is None
        app.method_options.set('{}'); app.method.set('minimal-shape-v1'); app.refresh()
        for geometry in ('1200x820','1450x1000'):
            root.geometry(geometry); root.update()
            assert app.cursor is None
            assert app.last_request is not None
            assert app.last_result.state_version==app.last_request.state_version
            assert app.input_text.get('1.0','end-1c')==debug_json(app.last_request)
            assert app.footer.winfo_height()>5
            assert app.hand_canvas.winfo_ismapped()
        app.show_rules.set(True); app.toggle_rules(); root.update()
        app.rule_profile.set('天凤四人半庄'); app.select_rule_profile(); app.new_game(); root.update()
        from nanikiru.rules.config import tenhou_hanchan
        assert app.game.rules == tenhou_hanchan()
        assert app.last_request.position.rules == tenhou_hanchan()
        app.rule_profile.set('原自定义规则'); app.select_rule_profile()
        app.rule_vars['open_tanyao'].set(False); app.seed.set('smoke'); app.new_game(); root.update()
        assert not app.last_request.position.rules.open_tanyao
        app.show_rules.set(False); app.toggle_rules()
        left,right,tile_id=app.hand_hitboxes[0]
        app.hand_canvas.event_generate('<Button-1>',x=int((left+right)/2),y=40); root.update()
        assert app.actions[app.action_combo.current()].tile_ids==(tile_id,)
        assert app.game.command_count==0
        app.step(); root.update()
        assert app.game._commands
        app.observer.set(2); app.change_view(); root.update()
        assert app.last_request.position.observer_seat==2
        app.omniscient.set(True); before=debug_json(app.last_request); app.refresh(); root.update()
        assert before==debug_json(app.last_request)
        app.show_json.set(True); app.toggle_json(); root.update()
        for widget in (app.input_text,app.output_text,app.hand_canvas,app.footer):
            assert widget.winfo_ismapped()
            assert widget.winfo_rooty()+widget.winfo_height()<=root.winfo_rooty()+root.winfo_height()
        app.history.selection_set('0'); app.select_history(); root.update()
        assert app.cursor==0 and not app.actions
        assert len(app.last_request.history)==0
        app.latest(); root.update(); assert app.cursor is None
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            app.path.set(str(Path(d)/'round.json')); app.save()
            assert Path(app.path.get()).exists()
            app.step(); app.load(); root.update()
            assert len(app.game._commands)==1
        app.undo(); root.update(); assert not app.game._commands
        assert not errors, errors
        print('Simulator UI smoke passed: rules, actions, isolated views, JSON, replay, save/load, undo, sizes.')
    finally:
        root.destroy()


if __name__=='__main__': main()
