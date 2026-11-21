"""Automated widget checks, including historical failed tasks and fit curves."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from .app import Monitor


def main():
    with tempfile.TemporaryDirectory() as folder:
        directory=Path(folder)
        (directory/'experiment.json').write_text(json.dumps({'chunks':[{},{}]}))
        (directory/'status.json').write_text(json.dumps({'stage':'failed','completed_chunks':0,'error':'fixture error'}))
        (directory/'cache-000').mkdir();(directory/'import-001').mkdir()
        (directory/'cache-000/manifest.json').write_text(json.dumps({'status':'complete','rows':{'train':30}}))
        (directory/'import-001/import-report.json').write_text(json.dumps({'status':'incomplete'}))
        events=[dict(event='baseline',round=0,rounds=2,train_loss=2,validation_loss=2.1),
                dict(event='round_complete',round=1,rounds=2,train_loss=1.5,validation_loss=1.8)]
        (directory/'fit-progress.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        before=(directory/'status.json').read_bytes()
        root=tk.Tk();errors=[];root.report_callback_exception=lambda *args:errors.append(args)
        app=Monitor(root,directory)
        try:
            for size in ('900x650','1100x820'):
                root.geometry(size);root.update()
                app.draw_chart(events);root.update()
                assert '1 / 2' in app.summary.get()
                assert app.table.item('1')['values'][1]=='失败'
                assert len(app.chart.find_all())>8
                assert app.detail.winfo_rooty()+app.detail.winfo_height()<=root.winfo_rooty()+root.winfo_height()
            assert (directory/'status.json').read_bytes()==before
            assert not errors,errors
            print('Training monitor smoke passed: manifests, failure, curves, layouts, read-only.')
        finally:app.close()


if __name__=='__main__':main()
