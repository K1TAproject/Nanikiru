"""A single-window playable table with asynchronous observer-only analysis."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog

from nanikiru.analysis.output import debug_json, summarize_result
from nanikiru.arena.session import Session, MODES, inspect_turn, load_weights
from nanikiru.simulation import Simulator
from nanikiru.simulation.persistence import save_simulation, load_simulation, replay_simulation
from nanikiru.rules.config import tenhou_hanchan
from simulator_ui.app import NAMES, result_text
from test_ui.tiles import tile_name
from .board import draw_table, FELT

ROOT=Path(__file__).resolve().parents[1]
DEFAULT_WEIGHTS=ROOT/'data/external-replays/v2-round1-A-retry-01/fit/weights.json'


def action_label(action, position):
    codes={t.id:t.code for t in position.tiles}
    return NAMES.get(action.kind,action.kind)+(' '+' '.join(tile_name(codes[t]) for t in action.tile_ids) if action.tile_ids else '')


def evaluate_views(main_turn, actor_turn):
    main=inspect_turn(main_turn.request)
    actor=main if actor_turn is main_turn else inspect_turn(actor_turn.request) if actor_turn else None
    return main,actor


class TableApp:
    def __init__(self, root):
        self.root=root;root.title('Nanikiru · 机器人牌桌 / A 规')
        root.geometry('1440x920');root.minsize(1100,760)
        self.executor=ThreadPoolExecutor(max_workers=1,thread_name_prefix='table-analysis')
        self.future=None;self.generation=0;self.closed=False;self.playing=False;self.want_step=False
        self.pending=None;self.main_turn=None;self.last_result=None;self.selected=None
        self.weights=None;self.hitboxes=[];self.manual_actions=[]
        self.seed=tk.StringVar(value='');self.observer=tk.IntVar(value=0)
        self.modes=[tk.StringVar(value='人工' if s==0 else 'v1 牌效') for s in range(4)]
        self.advisor=tk.StringVar(value='拟合 v2' if DEFAULT_WEIGHTS.exists() else '默认 v2')
        self.weight_path=tk.StringVar(value=str(DEFAULT_WEIGHTS))
        self.message=tk.StringVar(value='');self.status=tk.StringVar(value='');self.delay=tk.IntVar(value=600)
        self._build()
        if DEFAULT_WEIGHTS.exists():
            try:self.weights=load_weights(DEFAULT_WEIGHTS)
            except (OSError,ValueError,TypeError) as error:
                self.advisor.set('默认 v2');self.message.set(f'训练权重未载入：{error}')
        self.session=Session()
        self.refresh()
        root.protocol('WM_DELETE_WINDOW',self.close)

    def _build(self):
        top=ttk.Frame(self.root,padding=8);top.pack(fill='x')
        ttk.Label(top,text='A 规 · 四人半庄 / 连续单局',font=('Microsoft YaHei',12,'bold')).pack(side='left',padx=(0,12))
        ttk.Label(top,text='种子').pack(side='left')
        ttk.Entry(top,textvariable=self.seed,width=13).pack(side='left',padx=4)
        ttk.Button(top,text='新开一局',command=self.new_game).pack(side='left',padx=3)
        ttk.Button(top,text='保存',command=self.save).pack(side='left',padx=3)
        ttk.Button(top,text='载入',command=self.load).pack(side='left',padx=3)
        ttk.Button(top,text='撤销一步',command=self.undo).pack(side='left',padx=3)
        ttk.Label(top,text='主视角').pack(side='left',padx=(12,3))
        view=ttk.Combobox(top,textvariable=self.observer,values=(0,1,2,3),width=3,state='readonly');view.pack(side='left')
        view.bind('<<ComboboxSelected>>',lambda e:self.change_settings())
        controls=ttk.Frame(self.root,padding=(8,0,8,6));controls.pack(fill='x')
        for seat in range(4):
            ttk.Label(controls,text=f'{seat} 家').pack(side='left',padx=(6,3))
            box=ttk.Combobox(controls,textvariable=self.modes[seat],values=MODES,width=10,state='readonly');box.pack(side='left')
            box.bind('<<ComboboxSelected>>',lambda e:self.change_settings())
        self.auto_button=ttk.Button(controls,text='开始自动',command=self.toggle_auto);self.auto_button.pack(side='left',padx=12)
        ttk.Button(controls,text='机器人单步',command=self.step).pack(side='left')
        ttk.Label(controls,text='间隔 ms').pack(side='left',padx=(10,2))
        ttk.Combobox(controls,textvariable=self.delay,values=(200,600,1200,2000),width=5,state='readonly').pack(side='left')
        ttk.Label(self.root,textvariable=self.status,padding=(12,3)).pack(fill='x')
        self.footer=ttk.Label(self.root,textvariable=self.message,padding=8,foreground='#965826',wraplength=1050)
        self.footer.pack(side='bottom',fill='x')
        pane=ttk.Panedwindow(self.root,orient='horizontal');pane.pack(fill='both',expand=True,padx=8)
        left=ttk.Frame(pane);right=ttk.Frame(pane,width=420);pane.add(left,weight=3);pane.add(right,weight=1)
        self.canvas=tk.Canvas(left,bg=FELT,highlightthickness=0,width=900,height=760)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.canvas.bind('<Button-1>',self.select_tile)
        manual=ttk.Frame(left,padding=6);manual.pack(side='bottom',fill='x')
        self.action_box=ttk.Combobox(manual,state='readonly',width=28);self.action_box.pack(side='left',fill='x',expand=True)
        self.execute_button=ttk.Button(manual,text='执行所选动作',command=self.execute_manual);self.execute_button.pack(side='left',padx=4)
        ttk.Button(manual,text='查看待操作家',command=self.follow_actor).pack(side='left')
        self.canvas.pack(fill='both',expand=True)
        toolbar=ttk.Frame(right,padding=5);toolbar.pack(fill='x')
        ttk.Label(toolbar,text='主视角分析').pack(side='left')
        advisor=ttk.Combobox(toolbar,textvariable=self.advisor,values=MODES[1:],width=12,state='readonly');advisor.pack(side='right')
        advisor.bind('<<ComboboxSelected>>',lambda e:self.change_settings())
        ttk.Entry(right,textvariable=self.weight_path,state='readonly').pack(fill='x',padx=5)
        ttk.Button(right,text='选择并载入训练权重',command=self.choose_weights).pack(fill='x',padx=5,pady=3)
        ttk.Label(right,text='拟合仅覆盖弃牌／立直；鸣牌使用 v2 启发式。\n分数与危险指数不是和牌／放铳概率。',wraplength=390,foreground='#87652a').pack(fill='x',padx=5,pady=3)
        self.summary=self._text(right,9)
        ttk.Label(right,text='当前主视角候选（含条件候选）').pack(anchor='w',padx=5)
        cols=('action','legal','shanten','ukeire','score')
        candidate_frame=ttk.Frame(right);candidate_frame.pack(fill='x',padx=5)
        self.candidates=ttk.Treeview(candidate_frame,columns=cols,show='headings',height=7)
        for name,title,width in zip(cols,('动作','许可','向听','进张','评分'),(140,50,45,45,60)):
            self.candidates.heading(name,text=title);self.candidates.column(name,width=width,minwidth=35)
        candidate_scroll=ttk.Scrollbar(candidate_frame,command=self.candidates.yview);candidate_scroll.pack(side='right',fill='y')
        self.candidates.configure(yscrollcommand=candidate_scroll.set)
        self.candidates.pack(fill='x',expand=True)
        self.candidates.bind('<<TreeviewSelect>>',self.select_candidate)
        self.tabs=ttk.Notebook(right);self.tabs.pack(fill='both',expand=True,padx=5,pady=5)
        self.event_text=self._tab('事件')
        self.result_text=self._tab('结算')
        self.json_text=self._tab('分析输入／输出')

    def _text(self,parent,height):
        frame=ttk.Frame(parent);frame.pack(fill='both',expand=True,padx=5,pady=3)
        text=tk.Text(frame,height=height,width=42,wrap='word',font=('Microsoft YaHei',10),state='disabled')
        scroll=ttk.Scrollbar(frame,command=text.yview);scroll.pack(side='right',fill='y')
        text.configure(yscrollcommand=scroll.set);text.pack(fill='both',expand=True)
        return text

    def _tab(self,label):
        frame=ttk.Frame(self.tabs);self.tabs.add(frame,text=label)
        return self._text(frame,6)

    @staticmethod
    def write(widget,text):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',text);widget.configure(state='disabled')

    def pause(self):
        self.playing=False;self.want_step=False;self.auto_button.configure(text='开始自动')

    def change_settings(self):
        self.pause();self.refresh()

    def refresh(self):
        self.generation+=1;token=self.generation
        if self.future:self.future.cancel()
        self.pending=None;self.main_turn=None;self.last_result=None;self.selected=None
        game=self.session.game;seat=self.observer.get()
        actor=self.session.acting_seat(seat if self.modes[seat].get()=='人工' else None)
        self.actor=actor
        self.status.set(f'主视角 {seat} 家  ·  第 {game.command_count} 个操作  ·  '+
                        ('本局结束' if game.phase=='ended' else f'等待 {actor} 家 · {self.modes[actor].get()}'))
        position=game.observe(seat);events=game.record(seat).events
        self.draw()
        self.manual_actions=list(game.legal_actions(seat))
        self.action_box.configure(values=[action_label(a,position) for a in self.manual_actions])
        if self.manual_actions:self.action_box.current(0)
        else:self.action_box.set('当前视角无待执行动作')
        self.execute_button.configure(state='normal' if self.manual_actions else 'disabled')
        self.candidates.delete(*self.candidates.get_children())
        self.write(self.summary,'正在分析当前主视角……')
        self.write(self.json_text,'正在分析；旧结果已失效。')
        lines=[]
        for e in events:
            fact=e.data['fact'];kind=e.kind.removeprefix('sim_');code=fact.get('code')
            from nanikiru.models import UNKNOWN
            face='' if code is None else ' 未知' if code is UNKNOWN else ' '+tile_name(code)
            lines.append(f'{e.id:03d}  {e.actor if e.actor is not None else "系统"} · {NAMES.get(kind,kind)}{face}')
        self.write(self.event_text,'\n'.join(lines));self.event_text.see('end')
        self.write(self.result_text,result_text(game.result))
        if game.phase=='ended':
            self.pause();self.write(self.summary,'本局已结束。请在结算页查看支付与役种。')
            self.tabs.select(1);return
        try:
            self.main_turn=self.session.turn(seat,self.advisor.get(),self.weights)
            actor_turn=None
            if actor is not None and self.modes[actor].get()!='人工':
                mode=self.modes[actor].get()
                actor_turn=self.main_turn if actor==seat and mode==self.advisor.get() else self.session.turn(actor,mode,self.weights)
            self.actor_turn=actor_turn
            self.future=self.executor.submit(evaluate_views,self.main_turn,actor_turn)
            future=self.future
            self.root.after(50,lambda:self.poll(token,future))
        except (ValueError,TypeError) as error:self.fail(error)

    def poll(self,token,future):
        if self.closed or token!=self.generation:return
        if not future.done():self.root.after(50,lambda:self.poll(token,future));return
        try:
            main,actor=future.result();self.last_result=main.result
            note=main.reason if self.manual_actions else '当前无需本家操作；这里只展示本家可见信息。'
            self.write(self.summary,summarize_result(self.main_turn.request,main.result)+'\n'+note)
            self.write(self.json_text,'输入（仅主视角）：\n'+debug_json(self.main_turn.request)+'\n输出：\n'+debug_json(main.result))
            self.candidate_rows=list(main.result.candidates)
            self.candidate_rows.sort(key=lambda c:c.metrics.get('policy_score').value if c.metrics.get('policy_score') and c.metrics['policy_score'].value is not None else -1e9,reverse=True)
            for i,c in enumerate(self.candidate_rows):
                def val(name):
                    metric=c.metrics.get(name);value=metric.value if metric else None
                    return '—' if value is None else f'{value:.2f}' if isinstance(value,float) else str(value)
                self.candidates.insert('','end',iid=str(i),values=(action_label(c.action,self.main_turn.request.position),
                    {'allowed':'可执行','unknown':'条件','forbidden':'禁止'}[c.legality],val('shanten'),val('ukeire'),val('policy_score')))
            self.pending=(self.actor_turn,actor) if actor is not None else None
            self.draw()
            if self.playing or self.want_step:self.root.after(self.delay.get() if self.playing else 0,lambda:self.advance(token))
        except Exception as error:self.fail(error)

    def draw(self):
        if not hasattr(self,'session'):return
        game=self.session.game;seat=self.observer.get();recommended=[]
        if self.main_turn and self.last_result:
            recommended=[self.main_turn.tile_ids[t] for a in self.last_result.recommendations.get('overall',()) for t in a.tile_ids]
        self.hitboxes=draw_table(self.canvas,game.observe(seat),game.record(seat).events,
            modes=[v.get() for v in self.modes],selected=self.selected,recommended=recommended)

    def step(self):
        self.pause();self.want_step=True
        if self.pending:self.advance(self.generation)
        elif self.actor is not None and self.modes[self.actor].get()=='人工':
            self.want_step=False;self.message.set('当前为人工玩家，请执行所选动作或更改该家的控制方式。')

    def toggle_auto(self):
        if self.playing:self.pause();return
        if self.session.game.phase=='ended':return
        self.playing=True;self.auto_button.configure(text='暂停')
        if self.pending:self.advance(self.generation)
        elif self.actor is not None and self.modes[self.actor].get()=='人工':self.message.set('自动模式等待人工玩家；执行后继续。')

    def advance(self,token):
        if token!=self.generation or not (self.playing or self.want_step) or not self.pending:return
        turn,decision=self.pending;self.pending=None;self.want_step=False
        if decision.action is None:self.fail(ValueError(decision.reason));return
        try:
            label=action_label(decision.action,turn.request.position)
            self.session.submit(turn,decision.action)
            self.message.set(f'{decision.action.actor} 家：{label} · {decision.reason}')
            self.refresh()
        except ValueError as error:self.fail(error)

    def fail(self,error):
        self.pause();self.message.set(f'已暂停，未执行替代动作：{error}')
        self.write(self.summary,f'分析／推进失败：{error}')

    def select_tile(self,event):
        for x1,y1,x2,y2,tile in self.hitboxes:
            if x1<=event.x<=x2 and y1<=event.y<=y2:
                self.selected=tile
                for i,a in enumerate(self.manual_actions):
                    if a.kind=='discard' and a.tile_ids==(tile,):self.action_box.current(i);break
                self.show_selected();self.draw();return

    def show_selected(self):
        if self.main_turn and self.last_result:
            local=next((alias for alias,original in self.main_turn.tile_ids.items() if original==self.selected),None)
            self.write(self.summary,summarize_result(self.main_turn.request,self.last_result,local))

    def select_candidate(self,event=None):
        selection=self.candidates.selection()
        if not selection or not self.main_turn:return
        candidate=self.candidate_rows[int(selection[0])]
        if candidate.legality!='allowed':return
        ids=tuple(self.main_turn.tile_ids[t] for t in candidate.action.tile_ids)
        for i,a in enumerate(self.manual_actions):
            if a.kind==candidate.action.kind and set(a.tile_ids)==set(ids):
                self.action_box.current(i);self.selected=ids[0] if ids else None;self.show_selected();self.draw();break

    def execute_manual(self):
        i=self.action_box.current()
        if not 0<=i<len(self.manual_actions):return
        action=self.manual_actions[i]
        try:
            self.message.set(f'人工执行 {action.actor} 家：'+action_label(action,self.session.game.observe(action.actor)))
            self.session.game.submit(action);self.refresh()
        except ValueError as error:self.fail(error)

    def follow_actor(self):
        if self.actor is not None:self.observer.set(self.actor);self.change_settings()

    def new_game(self):
        self.pause();self.session=Session(Simulator(rules=tenhou_hanchan(),seed=self.seed.get() or None))
        self.tabs.select(0);self.refresh();self.message.set('新局已建立；当前 A 规。')

    def undo(self):
        self.pause();game=self.session.game
        if game.command_count:self.session=Session(replay_simulation(game,game.command_count-1));self.refresh()

    def save(self):
        self.pause();path=filedialog.asksaveasfilename(parent=self.root,defaultextension='.json',filetypes=[('对局存档','*.json')])
        if path:
            try:save_simulation(self.session.game,path);self.message.set('已保存完整对局存档（含私有牌山，仅用于回放）。')
            except (OSError,ValueError) as error:self.fail(error)

    def load(self):
        self.pause();path=filedialog.askopenfilename(parent=self.root,filetypes=[('对局存档','*.json')])
        if path:
            try:
                game=load_simulation(path)
                if game.rules!=tenhou_hanchan():raise ValueError('本牌桌限定 A 规，请在原模拟器打开其他规则存档')
                self.session=Session(game);self.refresh()
            except (OSError,ValueError) as error:self.fail(error)

    def choose_weights(self):
        self.pause();path=filedialog.askopenfilename(parent=self.root,initialdir=str(DEFAULT_WEIGHTS.parent),filetypes=[('训练权重','weights.json')])
        if path:
            try:
                weights=load_weights(path);self.weights=weights;self.weight_path.set(path);self.refresh()
                self.message.set('训练权重已载入，仅用于本牌桌；未改变项目默认参数。')
            except (OSError,ValueError,TypeError) as error:self.fail(error)

    def close(self):
        self.closed=True;self.generation+=1;self.pause()
        self.executor.shutdown(wait=False,cancel_futures=True);self.root.destroy()


def main():
    root=tk.Tk();TableApp(root);root.mainloop()
